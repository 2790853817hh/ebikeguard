"""视觉大模型提供者（OpenAI 兼容协议实现）。

默认对接阿里云百炼（Model Studio）的 qwen3-vl 系列 —— 个人实名即可开通、
数据不出境（政务场景关键）、价格极低（qwen3-vl-plus 约 1.5 分/条上报）：

    AI_PROVIDER=qwenvl
    DASHSCOPE_API_KEY=sk-xxxx
    AI_VL_MODEL=qwen3-vl-plus       # 想更省换 qwen3-vl-flash；想更强换 qwen-vl-max

设计取舍：
1. 用 httpx 直调 OpenAI 兼容接口、不引入 dashscope SDK —— httpx 已在核心依赖中，
   视觉模型接入保持「零新增依赖」；且百炼 / 智谱 / 硅基流动等平台都兼容多模态
   OpenAI 协议，换平台只需改 AI_VL_BASE_URL / AI_VL_MODEL / DASHSCOPE_API_KEY。
2. 图片经 StorageBackend 读取后，按 AI_VL_MAX_EDGE 缩放为 JPEG 再 base64 内嵌 ——
   9 张原图（每张数 MB）直传会超出请求体限制，缩放后单次请求约 1.5MB，
   token 消耗约 1000/张，成本可控。
3. 失败降级：任何环节异常（密钥缺失、读图失败、超时、HTTP 错误、输出无法解析）
   均返回 UNCERTAIN 并附可读理由 —— 保守档策略会自动转入人工审核队列，
   绝不因模型故障中断上报流程。
"""
from __future__ import annotations

import base64
import io
import json
import time
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageOps

from app.adapters.ai.base import AuditReason, AuditRequest, AuditResult
from app.config import settings
from app.core.constants import AiConclusion
from app.core.logging import get_logger

logger = get_logger(__name__)

# 结论归一化：容忍模型输出大小写、空格与中文变体
_CONCLUSION_ALIASES: dict[str, AiConclusion] = {
    "violation": AiConclusion.VIOLATION,
    "违规": AiConclusion.VIOLATION,
    "疑似违规": AiConclusion.VIOLATION,
    "存在违规": AiConclusion.VIOLATION,
    "normal": AiConclusion.NORMAL,
    "正常": AiConclusion.NORMAL,
    "无违规": AiConclusion.NORMAL,
    "uncertain": AiConclusion.UNCERTAIN,
    "无法判断": AiConclusion.UNCERTAIN,
    "无法确定": AiConclusion.UNCERTAIN,
    "不确定": AiConclusion.UNCERTAIN,
}

PROMPT_TEMPLATE = """你是电动车消防安全领域的图像审核助手。请根据现场照片，判断是否存在「电动车不安全使用行为」并给出初审建议。

【上报信息】
{meta}

【审核要求】
1. 逐张检查照片，重点确认三件事：
   a) 画面中是否有电动车（含电动自行车、电动摩托车、电动三轮车、电池）；
   b) 所处位置是否为建筑内公共区域（楼道、楼梯间、门厅、电梯轿厢、地下车库等），或存在飞线充电、插线板拖线充电、占用堵塞疏散通道等情形；
   c) 照片内容与上报的行为类型、备注描述是否一致。
2. 照片模糊、角度不佳或信息不足时，如实说明，禁止臆测不存在的物体。
3. 本结论仅作人工审核参考，宁可保守也不夸大。

【输出格式】只输出一个 JSON 对象，不要输出任何其它文字或代码块标记：
{{"conclusion": "VIOLATION 或 NORMAL 或 UNCERTAIN",
  "confidence": 0.0 到 1.0 之间的小数,
  "risk_score": 0.0 到 1.0 之间的小数（该行为构成消防安全风险的可能性）,
  "summary": "一句话概述你从照片中看到的内容",
  "reasons": [{{"code": "英文短码", "text": "中文理由，说明依据照片中哪些具体细节"}}]}}

判断口径：
- VIOLATION：照片可以证实存在电动车不安全使用行为；
- NORMAL：照片可以证实不存在该行为（如拍摄的是合规停放区、画面中并无电动车）；
- UNCERTAIN：照片信息不足以判断。
"""


class _ModelCallError(Exception):
    """内部信号：模型调用失败，携带可读的 code/message 供降级理由使用。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 纯函数（可单测）


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_conclusion(value: Any) -> AiConclusion:
    """把模型输出的结论文本归一化为 AiConclusion（容忍大小写/空格/中文变体）。"""
    text = str(value or "").strip().lower().replace(" ", "")
    return _CONCLUSION_ALIASES.get(text, AiConclusion.UNCERTAIN)


def extract_json_object(text: str) -> dict | None:
    """从模型输出中提取 JSON 对象（容忍 ```json 围栏与前后杂文本）。纯函数。"""
    if not text:
        return None
    s = text.strip()
    if "```" in s:
        for part in s.split("```"):
            candidate = part.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                s = candidate
                break
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 尝试兜底截取
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(s[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None
    return None


def prepare_image(raw: bytes, *, max_edge: int = 1024, jpeg_quality: int = 82) -> bytes:
    """送模型前预处理：等比缩放到 max_edge 以内并转 JPEG（统一格式、控制体积）。纯函数。"""
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im) or im
        im = im.convert("RGB")
        w, h = im.size
        scale = max_edge / max(w, h)
        if scale < 1:
            im = im.resize(
                (max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS
            )
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=jpeg_quality)
        return buf.getvalue()


def to_data_uri(jpeg_bytes: bytes) -> str:
    """转成 OpenAI 多模态协议要求的 base64 data URI。纯函数。"""
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")


def _content_to_text(content: Any) -> str:
    """兼容两种返回：字符串 content，或 [{type: text, text: ...}] 数组。纯函数。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return "" if content is None else str(content)


def _parse_reasons(parsed: dict) -> list[AuditReason]:
    """把模型 JSON 里的 reasons/summary 转为统一 AuditReason 列表。纯函数。"""
    reasons: list[AuditReason] = []
    items = parsed.get("reasons")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                reasons.append(
                    AuditReason(
                        code=str(item.get("code") or "MODEL_REASON")[:40],
                        text=str(item["text"]).strip()[:300],
                        weight=round(_to_float(item.get("weight")) or 0.0, 3),
                    )
                )
    summary = str(parsed.get("summary") or "").strip()
    if summary:
        reasons.insert(0, AuditReason(code="MODEL_SUMMARY", text=summary[:300], weight=0.0))
    return reasons


def _default_risk(conclusion: AiConclusion, confidence: float) -> float:
    """模型未给 risk_score 时的保守推导：风险分 = 违规可能性。纯函数。"""
    if conclusion == AiConclusion.VIOLATION:
        return 0.5 + 0.5 * confidence
    if conclusion == AiConclusion.NORMAL:
        return 0.5 * (1.0 - confidence)
    return 0.5


def _http_hint(status_code: int, body: str) -> str:
    """把常见的配额 / 账户类 HTTP 错误补充成可操作的中文提示（纯函数）。

    背景：百炼新用户各模型有 100 万 Token / 90 天免费额度，额度用尽后若已开启
    「用完即停」会返回 HTTP 403 + AllocationQuota.FreeTierOnly；这是最可能遇到的
    非故障类报错，直接透出英文码对使用者没有行动指引价值。
    """
    text = body or ""
    if "AllocationQuota.FreeTierOnly" in text:
        return "；原因：免费额度已用尽且已开启「用完即停」，到百炼控制台该模型处关闭开关可改按量付费"
    if "Arrearage" in text or "AccountOverdue" in text:
        return "；原因：阿里云账户欠费，充值后自动恢复"
    if status_code == 429:
        return "；原因：触发限流（请求频率超限），无需处理，稍后自动恢复"
    return ""


# ---------------------------------------------------------------- Provider 实现


class QwenVLProvider:
    """视觉大模型审核提供者（OpenAI 兼容协议，默认阿里云百炼 qwen3-vl）。"""

    name = "qwen-vl"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_images: int | None = None,
        max_edge: int | None = None,
        jpeg_quality: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.dashscope_api_key
        self.base_url = (base_url or settings.ai_vl_base_url).rstrip("/")
        self.model = model or settings.ai_vl_model
        self.timeout_seconds = timeout_seconds or settings.ai_vl_timeout_seconds
        self.max_images = max_images or settings.ai_vl_max_images
        self.max_edge = max_edge or settings.ai_vl_max_edge
        self.jpeg_quality = jpeg_quality or settings.ai_vl_jpeg_quality
        self._injected_client = http_client  # 测试注入点；生产走临时 httpx.Client

    # ------------------------------------------------------------ 对外主入口

    def audit(self, req: AuditRequest) -> AuditResult:
        started = time.perf_counter()

        if not self.api_key:
            # 未配置密钥时优雅降级：返回 UNCERTAIN 并把请求交给人工，而不是抛错中断业务
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [
                    AuditReason(
                        code="PROVIDER_NOT_CONFIGURED",
                        text="未配置视觉大模型密钥（DASHSCOPE_API_KEY），已转人工审核",
                        weight=0.0,
                    )
                ],
                model_version="unconfigured",
            )

        if not req.images:
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [AuditReason(code="NO_IMAGE", text="上报未包含现场照片，无法进行图像内容识别", weight=0.0)],
            )

        data_uris, skipped = self._load_images(req)
        if not data_uris:
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [
                    AuditReason(
                        code="IMAGE_READ_FAILED",
                        text=f"现场照片读取失败（共 {skipped} 张），已转人工审核",
                        weight=0.0,
                    )
                ],
            )

        prompt = self._build_prompt(req, len(data_uris), skipped)
        return self._run_model(started, data_uris, prompt, skipped, context=f"report={req.report_id}")

    def audit_images_bytes(
        self,
        images: list[bytes],
        *,
        behavior_name: str = "未指明",
        remark: str = "",
        address: str = "",
    ) -> AuditResult:
        """对内存中的图片字节直接做一次审核（离线验收用，不经过存储层与数据库）。

        供「本地测试图批量验收」（scripts/check_ai_images.py）这类场景使用；
        生产链路的 audit(req) 仍从 StorageBackend 读图。
        """
        started = time.perf_counter()

        if not self.api_key:
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [
                    AuditReason(
                        code="PROVIDER_NOT_CONFIGURED",
                        text="未配置视觉大模型密钥（DASHSCOPE_API_KEY），已转人工审核",
                        weight=0.0,
                    )
                ],
                model_version="unconfigured",
            )
        if not images:
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [AuditReason(code="NO_IMAGE", text="未提供图片，无法进行图像内容识别", weight=0.0)],
            )

        data_uris: list[str] = []
        skipped = max(0, len(images) - self.max_images)
        for raw in images[: self.max_images]:
            try:
                data_uris.append(
                    to_data_uri(prepare_image(raw, max_edge=self.max_edge, jpeg_quality=self.jpeg_quality))
                )
            except Exception as exc:  # noqa: BLE001 - 单张失败不阻断其余
                skipped += 1
                logger.warning("图片预处理失败（离线验收）：%s", exc)
        if not data_uris:
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [AuditReason(code="IMAGE_READ_FAILED", text=f"图片无法解码（共 {skipped} 张）", weight=0.0)],
            )

        meta_lines = [
            f"- 上报行为类型：{behavior_name}",
            f"- 地点描述：{address or '未提供'}",
            f"- 上报人备注：{remark or '无'}",
            f"- 本次提供照片：{len(data_uris)} 张" + (f"（另有 {skipped} 张未能读取）" if skipped else ""),
        ]
        prompt = PROMPT_TEMPLATE.format(meta="\n".join(meta_lines))
        return self._run_model(started, data_uris, prompt, skipped, context="offline")

    def probe(self) -> tuple[bool, str]:
        """连通性探测：向模型发一张程序生成的小图，验证 Key 与网络是否可用。"""
        if not self.api_key:
            return False, "未配置 DASHSCOPE_API_KEY"
        im = Image.new("RGB", (256, 256), (210, 210, 210))
        ImageDraw.Draw(im).rectangle([96, 96, 160, 160], fill=(20, 20, 20))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        try:
            text, usage = self._request([to_data_uri(buf.getvalue())], "这是一张连通性测试图，请只回复两个字：收到")
        except _ModelCallError as exc:
            return False, exc.message
        text = (text or "").strip()
        if not text:
            return False, "模型返回内容为空（请检查模型名与控制台开通状态）"
        tokens = usage.get("total_tokens")
        suffix = f"（消耗 {tokens} tokens）" if isinstance(tokens, int) else ""
        return True, f"调用成功，模型「{self.model}」回复「{text[:30]}」{suffix}"

    # ------------------------------------------------------------ 内部步骤

    def _run_model(
        self, started: float, data_uris: list[str], prompt: str, skipped: int, context: str
    ) -> AuditResult:
        """调用模型并解析结果 —— audit 与 audit_images_bytes 共用的后半段（含全部降级分支）。"""
        try:
            text, usage = self._request(data_uris, prompt)
        except _ModelCallError as exc:
            logger.warning("视觉大模型调用失败（%s）：%s", context, exc.message)
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [AuditReason(code=exc.code, text=exc.message, weight=0.0)],
            )

        parsed = extract_json_object(text)
        if parsed is None:
            logger.warning("视觉大模型输出无法解析（%s）：%s", context, text[:120])
            return self._result(
                started,
                AiConclusion.UNCERTAIN,
                0.0,
                [
                    AuditReason(
                        code="MODEL_OUTPUT_UNPARSEABLE",
                        text="模型输出无法解析为约定的 JSON 格式，已转人工审核",
                        weight=0.0,
                    )
                ],
                raw={"text": text[:1000], "usage": usage},
                model_version="parse-error",
            )

        conclusion = _normalize_conclusion(parsed.get("conclusion"))
        confidence = _clamp(_to_float(parsed.get("confidence")) or 0.0)
        risk = _to_float(parsed.get("risk_score"))
        risk = _clamp(risk) if risk is not None else _default_risk(conclusion, confidence)

        reasons = _parse_reasons(parsed)
        if skipped:
            reasons.append(
                AuditReason(code="IMAGE_PARTIAL", text=f"有 {skipped} 张现场照片未纳入本次识别", weight=0.0)
            )
        # 引擎能力边界如实声明（保守档策略的合规要求）
        reasons.append(
            AuditReason(
                code="ENGINE_NOTE",
                text=(
                    f"本结论由多模态大模型（{self.model}）依据照片自动生成，仅作审核参考；"
                    "系统采取保守策略，疑似违规一律转人工复核，且当前未启用自动放行。"
                ),
                weight=0.0,
            )
        )
        logger.info(
            "视觉大模型审核完成（%s 结论=%s 置信度=%.2f 用时=%sms）",
            context,
            conclusion,
            confidence,
            int((time.perf_counter() - started) * 1000),
        )
        return self._result(
            started,
            conclusion,
            confidence,
            reasons,
            raw={"parsed": parsed, "usage": usage},
            risk=risk,
        )

    def _result(
        self,
        started: float,
        conclusion: AiConclusion,
        confidence: float,
        reasons: list[AuditReason],
        *,
        raw: dict[str, Any] | None = None,
        model_version: str = "v1",
        risk: float | None = None,
    ) -> AuditResult:
        return AuditResult(
            conclusion=conclusion,
            confidence=_clamp(confidence),
            reasons=reasons,
            model_name=self.model,
            model_version=model_version,
            raw=raw,
            latency_ms=int((time.perf_counter() - started) * 1000),
            risk_score=risk,
        )

    def _load_images(self, req: AuditRequest) -> tuple[list[str], int]:
        """经存储层读取原图 → 缩放 → base64 data URI。单张失败不阻断其余。"""
        from app.adapters.storage.factory import get_storage

        storage = get_storage()
        data_uris: list[str] = []
        skipped = max(0, len(req.images) - self.max_images)
        for img in req.images[: self.max_images]:
            try:
                raw = storage.read(img.storage_key)
                jpeg = prepare_image(raw, max_edge=self.max_edge, jpeg_quality=self.jpeg_quality)
                data_uris.append(to_data_uri(jpeg))
            except Exception as exc:  # noqa: BLE001 - 单张失败不阻断其余
                skipped += 1
                logger.warning("图片读取或预处理失败（key=%s）：%s", img.storage_key, exc)
        return data_uris, skipped

    def _build_prompt(self, req: AuditRequest, image_count: int, skipped: int) -> str:
        meta_lines = [
            f"- 上报行为类型：{req.behavior_type_name or '未指明'}（代码 {req.behavior_type_code or '-'}）",
            f"- 地点描述：{req.address_text or '未提供'}",
            f"- 上报人备注：{req.remark or '无'}",
            f"- 拍摄时间：{req.shoot_time or '未提供'}",
            f"- 本次提供照片：{image_count} 张" + (f"（另有 {skipped} 张未能读取）" if skipped else ""),
        ]
        return PROMPT_TEMPLATE.format(meta="\n".join(meta_lines))

    def _request(self, data_uris: list[str], prompt: str) -> tuple[str, dict]:
        """调用 OpenAI 兼容的多模态接口，返回（文本内容, usage）。失败抛 _ModelCallError。"""
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        [{"type": "image_url", "image_url": {"url": uri}} for uri in data_uris]
                        + [{"type": "text", "text": prompt}]
                    ),
                }
            ],
            "temperature": 0.1,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            if self._injected_client is not None:
                resp = self._injected_client.post(url, json=body, headers=headers)
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise _ModelCallError(
                "MODEL_TIMEOUT", f"视觉大模型调用超时（>{self.timeout_seconds}s），已转人工审核"
            ) from exc
        except httpx.HTTPError as exc:
            raise _ModelCallError(
                "MODEL_NETWORK_ERROR", f"视觉大模型网络异常（{type(exc).__name__}），已转人工审核"
            ) from exc

        if resp.status_code != 200:
            snippet = (resp.text or "")[:180].replace("\n", " ")
            raise _ModelCallError(
                "MODEL_HTTP_ERROR",
                f"视觉大模型返回 HTTP {resp.status_code}（{snippet}）{_http_hint(resp.status_code, resp.text)}，已转人工审核",
            )

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise _ModelCallError(
                "MODEL_BAD_RESPONSE", f"视觉大模型响应结构异常（{type(exc).__name__}），已转人工审核"
            ) from exc

        return _content_to_text(content), (data.get("usage") or {})
