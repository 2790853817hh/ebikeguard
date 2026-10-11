"""视觉大模型适配器的纯逻辑测试（不触发任何外部调用）。

覆盖：
- JSON 解析容错（围栏 / 杂文本 / 非 JSON）
- 结论归一化、风险分推导
- 图片预处理（缩放 / JPEG 编码）
- audit 全路径：成功映射、请求体契约、各类失败降级
- probe 连通性探测
"""
from __future__ import annotations

import io
import json

import httpx
import pytest
from PIL import Image

from app.adapters.ai.base import AuditImage, AuditRequest
from app.adapters.ai.qwen_vl import (
    QwenVLProvider,
    _default_risk,
    _http_hint,
    _normalize_conclusion,
    _parse_reasons,
    extract_json_object,
    prepare_image,
    to_data_uri,
)
from app.core.constants import AiConclusion


# ---------------------------------------------------------------- 测试替身


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {}, ensure_ascii=False)

    def json(self):
        if self._payload is None:
            raise ValueError("response is not json")
        return self._payload


class _FakeClient:
    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.last_request: dict | None = None

    def post(self, url, json=None, headers=None):
        self.last_request = {"url": url, "json": json, "headers": headers}
        if self.exc is not None:
            raise self.exc
        return self.response


class _FakeStorage:
    def __init__(self, blobs: dict[str, bytes], fail: bool = False) -> None:
        self.blobs = blobs
        self.fail = fail

    def read(self, key: str) -> bytes:
        if self.fail:
            raise FileNotFoundError(key)
        return self.blobs[key]


def _jpeg_bytes(w: int = 800, h: int = 600) -> bytes:
    im = Image.new("RGB", (w, h), (120, 160, 200))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _patch_storage(monkeypatch, blobs: dict[str, bytes] | None = None, fail: bool = False) -> None:
    fake = _FakeStorage(blobs or {"origin/test.jpg": _jpeg_bytes()}, fail=fail)
    monkeypatch.setattr("app.adapters.storage.factory.get_storage", lambda: fake)


def _audit_request(images: list[AuditImage] | None = None) -> AuditRequest:
    if images is None:
        images = [AuditImage(image_id="i1", storage_key="origin/test.jpg", sha256="a" * 64)]
    return AuditRequest(
        report_id=1,
        behavior_type_code="PARK_INDOOR",
        behavior_type_name="电动车进楼入户停放",
        behavior_risk_weight=0.8,
        remark="楼道里停着电动车充电",
        address_text="某小区 3 号楼 1 单元",
        images=images,
    )


def _model_json(**overrides) -> str:
    parsed = {
        "conclusion": "VIOLATION",
        "confidence": 0.82,
        "risk_score": 0.9,
        "summary": "楼道内停放一辆电动自行车并连接插线板充电",
        "reasons": [{"code": "EBikeInHallway", "text": "画面左上角可见电动车后轮与充电线", "weight": 0.4}],
    }
    parsed.update(overrides)
    return json.dumps(parsed, ensure_ascii=False)


def _ok_payload(content: str | list | None = None) -> dict:
    return {
        "choices": [{"message": {"content": content if content is not None else _model_json()}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 120, "total_tokens": 1320},
    }


def _provider(client: _FakeClient, key: str = "sk-abcdef1234567890") -> QwenVLProvider:
    return QwenVLProvider(
        api_key=key,
        base_url="https://example.test/v1",
        model="qwen3-vl-plus",
        http_client=client,
    )


# ---------------------------------------------------------------- 纯函数


def test_extract_json_object_accepts_plain_and_fenced_and_noisy_text():
    assert extract_json_object('{"conclusion": "NORMAL"}') == {"conclusion": "NORMAL"}
    fenced = '```json\n{"conclusion": "VIOLATION"}\n```'
    assert extract_json_object(fenced) == {"conclusion": "VIOLATION"}
    noisy = '好的，分析如下：\n{"conclusion": "UNCERTAIN", "confidence": 0.3}\n以上。'
    assert extract_json_object(noisy) == {"conclusion": "UNCERTAIN", "confidence": 0.3}


def test_extract_json_object_rejects_non_object_and_garbage():
    assert extract_json_object("我无法判断这张图片") is None
    assert extract_json_object("") is None
    assert extract_json_object('["a", "b"]') is None  # 数组不是对象


def test_normalize_conclusion_tolerates_variants():
    assert _normalize_conclusion("violation") == AiConclusion.VIOLATION
    assert _normalize_conclusion(" VIOLATION ") == AiConclusion.VIOLATION
    assert _normalize_conclusion("疑似违规") == AiConclusion.VIOLATION
    assert _normalize_conclusion("正常") == AiConclusion.NORMAL
    assert _normalize_conclusion("无法判断") == AiConclusion.UNCERTAIN
    assert _normalize_conclusion("这是什么鬼") == AiConclusion.UNCERTAIN  # 未知值保守降级
    assert _normalize_conclusion(None) == AiConclusion.UNCERTAIN


def test_parse_reasons_puts_summary_first_and_cleans_fields():
    parsed = {
        "summary": "一句话概述",
        "reasons": [
            {"code": "A", "text": " 理由一 "},
            {"code": "", "text": "理由二"},
            {"text": ""},  # 空文本应被丢弃
            "不是字典",  # 非字典应被丢弃
        ],
    }
    reasons = _parse_reasons(parsed)
    assert reasons[0].code == "MODEL_SUMMARY"
    assert [r.text for r in reasons[1:]] == ["理由一", "理由二"]
    assert reasons[1].code == "A"
    assert reasons[2].code == "MODEL_REASON"  # 空 code 有默认值


def test_default_risk_is_monotonic_with_confidence():
    assert _default_risk(AiConclusion.VIOLATION, 0.8) == pytest.approx(0.9)
    assert _default_risk(AiConclusion.NORMAL, 0.8) == pytest.approx(0.1)
    assert _default_risk(AiConclusion.UNCERTAIN, 0.8) == pytest.approx(0.5)


def test_http_hint_maps_known_quota_errors_to_chinese_actions():
    """免费额度用尽 / 欠费 / 限流三类最常见的非故障报错必须有可操作的中文提示。"""
    assert "免费额度" in _http_hint(403, '{"code":"AllocationQuota.FreeTierOnly"}')
    assert "欠费" in _http_hint(400, "AccountOverdue")
    assert "限流" in _http_hint(429, "Requests rate limit exceeded")
    assert _http_hint(500, "internal error") == ""  # 未知错误不硬编提示


def test_prepare_image_resizes_to_max_edge_and_encodes_jpeg():
    out = prepare_image(_jpeg_bytes(1600, 1200), max_edge=800, jpeg_quality=80)
    assert out[:2] == b"\xff\xd8"  # JPEG 魔数
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) == 800


def test_prepare_image_keeps_small_images_unscaled():
    out = prepare_image(_jpeg_bytes(300, 200), max_edge=1024)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (300, 200)


def test_to_data_uri_has_expected_prefix():
    uri = to_data_uri(b"\x00\x01")
    assert uri.startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------- audit 主路径


def test_audit_without_key_degrades_to_manual(monkeypatch):
    provider = QwenVLProvider(api_key="", base_url="https://x.test/v1", model="qwen3-vl-plus")
    result = provider.audit(_audit_request())
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.confidence == 0.0
    assert result.reasons[0].code == "PROVIDER_NOT_CONFIGURED"
    assert result.model_version == "unconfigured"


def test_audit_without_images_degrades(monkeypatch):
    provider = _provider(_FakeClient(_FakeResponse(200, _ok_payload())))
    result = provider.audit(_audit_request(images=[]))
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "NO_IMAGE"


def test_audit_success_maps_fields_and_request_contract(monkeypatch):
    client = _FakeClient(_FakeResponse(200, _ok_payload()))
    _patch_storage(monkeypatch)
    provider = _provider(client)
    result = provider.audit(_audit_request())

    assert result.conclusion == AiConclusion.VIOLATION
    assert result.confidence == pytest.approx(0.82)
    assert result.risk_score == pytest.approx(0.9)
    codes = [r.code for r in result.reasons]
    assert "MODEL_SUMMARY" in codes
    assert "EBikeInHallway" in codes
    # 引擎能力边界声明必须在（保守档策略的合规要求）
    assert "ENGINE_NOTE" in codes
    assert "qwen3-vl-plus" in result.model_name

    # 请求体契约：OpenAI 兼容多模态格式
    body = client.last_request["json"]
    assert body["model"] == "qwen3-vl-plus"
    assert body["temperature"] == pytest.approx(0.1)
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert content[-1]["type"] == "text"
    assert "电动车进楼入户停放" in content[-1]["text"]
    assert client.last_request["headers"]["Authorization"].startswith("Bearer sk-")


def test_audit_accepts_list_content_variant(monkeypatch):
    payload = _ok_payload(content=[{"type": "text", "text": _model_json(conclusion="NORMAL", confidence=0.7)}])
    client = _FakeClient(_FakeResponse(200, payload))
    _patch_storage(monkeypatch)
    result = _provider(client).audit(_audit_request())
    assert result.conclusion == AiConclusion.NORMAL
    assert result.risk_score == pytest.approx(0.9)  # 模型给了 risk_score，优先采用


def test_audit_derives_risk_when_model_omits_it(monkeypatch):
    content = _model_json(risk_score=None)
    client = _FakeClient(_FakeResponse(200, _ok_payload(content=content)))
    _patch_storage(monkeypatch)
    result = _provider(client).audit(_audit_request())
    assert result.risk_score == pytest.approx(0.5 + 0.5 * 0.82)


def test_audit_unparseable_output_degrades(monkeypatch):
    client = _FakeClient(_FakeResponse(200, _ok_payload(content="图片看不清楚，我无法给出判断。")))
    _patch_storage(monkeypatch)
    result = _provider(client).audit(_audit_request())
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "MODEL_OUTPUT_UNPARSEABLE"
    assert result.model_version == "parse-error"
    assert result.raw and "text" in result.raw


def test_audit_http_error_degrades_with_status(monkeypatch):
    client = _FakeClient(_FakeResponse(401, {"error": {"message": "Invalid API-key"}}))
    _patch_storage(monkeypatch)
    result = _provider(client).audit(_audit_request())
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "MODEL_HTTP_ERROR"
    assert "401" in result.reasons[0].text


def test_audit_free_tier_exhausted_error_gets_actionable_hint(monkeypatch):
    """免费额度用尽（403 + AllocationQuota.FreeTierOnly）时，理由要给出处理动作。"""
    payload = {"error": {"code": "AllocationQuota.FreeTierOnly", "message": "Free quota exhausted"}}
    client = _FakeClient(_FakeResponse(403, payload))
    _patch_storage(monkeypatch)
    result = _provider(client).audit(_audit_request())
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert "403" in result.reasons[0].text
    assert "免费额度已用尽" in result.reasons[0].text
    assert "用完即停" in result.reasons[0].text


def test_audit_timeout_degrades(monkeypatch):
    client = _FakeClient(exc=httpx.TimeoutException("read timeout"))
    _patch_storage(monkeypatch)
    result = _provider(client).audit(_audit_request())
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "MODEL_TIMEOUT"
    assert "超时" in result.reasons[0].text


def test_audit_network_error_degrades(monkeypatch):
    client = _FakeClient(exc=httpx.ConnectError("connection refused"))
    _patch_storage(monkeypatch)
    result = _provider(client).audit(_audit_request())
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "MODEL_NETWORK_ERROR"


def test_audit_image_read_failure_degrades(monkeypatch):
    client = _FakeClient(_FakeResponse(200, _ok_payload()))
    _patch_storage(monkeypatch, fail=True)
    result = _provider(client).audit(_audit_request())
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "IMAGE_READ_FAILED"
    assert client.last_request is None  # 全部图片失败时不应发起调用


def test_audit_truncates_images_beyond_max_and_notes_it(monkeypatch):
    blobs = {f"origin/{i}.jpg": _jpeg_bytes(200, 200) for i in range(3)}
    _patch_storage(monkeypatch, blobs=blobs)
    images = [AuditImage(image_id=f"i{i}", storage_key=f"origin/{i}.jpg", sha256="a" * 64) for i in range(3)]
    client = _FakeClient(_FakeResponse(200, _ok_payload()))
    provider = QwenVLProvider(
        api_key="sk-abcdef1234567890",
        base_url="https://example.test/v1",
        model="qwen3-vl-plus",
        max_images=2,
        http_client=client,
    )
    result = provider.audit(_audit_request(images=images))
    content = client.last_request["json"]["messages"][0]["content"]
    image_items = [c for c in content if c["type"] == "image_url"]
    assert len(image_items) == 2  # 只送 2 张
    assert any(r.code == "IMAGE_PARTIAL" for r in result.reasons)  # 且如实告知


# ---------------------------------------------------------------- audit_images_bytes（离线验收路径）


def test_audit_images_bytes_success(monkeypatch):
    """离线验收路径：直接给内存图片字节，不经过存储层。"""
    client = _FakeClient(_FakeResponse(200, _ok_payload()))
    provider = _provider(client)
    result = provider.audit_images_bytes(
        [_jpeg_bytes(300, 200)], behavior_name="进楼入户", remark="楼道里停着电动车"
    )
    assert result.conclusion == AiConclusion.VIOLATION
    codes = [r.code for r in result.reasons]
    assert "MODEL_SUMMARY" in codes and "ENGINE_NOTE" in codes
    # 请求体里应包含行为类型提示
    text = client.last_request["json"]["messages"][0]["content"][-1]["text"]
    assert "进楼入户" in text
    # 不经过存储层：images 直接来自入参
    assert client.last_request["json"]["messages"][0]["content"][0]["type"] == "image_url"


def test_audit_images_bytes_without_key_degrades():
    provider = QwenVLProvider(api_key="", base_url="https://x.test/v1", model="qwen3-vl-plus")
    result = provider.audit_images_bytes([b"fake"])
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "PROVIDER_NOT_CONFIGURED"


def test_audit_images_bytes_empty_list_degrades():
    provider = _provider(_FakeClient(_FakeResponse(200, _ok_payload())))
    result = provider.audit_images_bytes([])
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "NO_IMAGE"


def test_audit_images_bytes_undecodable_input_degrades():
    """坏字节（无法解码）时降级，不抛异常、不发起调用。"""
    client = _FakeClient(_FakeResponse(200, _ok_payload()))
    provider = _provider(client)
    result = provider.audit_images_bytes([b"not-an-image"])
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.reasons[0].code == "IMAGE_READ_FAILED"
    assert client.last_request is None


# ---------------------------------------------------------------- probe


def test_probe_without_key_returns_false():
    provider = QwenVLProvider(api_key="", base_url="https://x.test/v1", model="qwen3-vl-plus")
    ok, message = provider.probe()
    assert ok is False
    assert "DASHSCOPE_API_KEY" in message


def test_probe_success_reports_reply_and_tokens():
    client = _FakeClient(
        _FakeResponse(200, {"choices": [{"message": {"content": "收到"}}], "usage": {"total_tokens": 66}})
    )
    provider = _provider(client)
    ok, message = provider.probe()
    assert ok is True
    assert "收到" in message
    assert "66" in message


def test_probe_failure_surfaces_reason():
    client = _FakeClient(_FakeResponse(404, {"error": {"message": "model not found"}}))
    ok, message = _provider(client).probe()
    assert ok is False
    assert "404" in message
