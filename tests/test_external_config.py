"""外部服务配置就绪判定测试（纯逻辑，不发任何外部请求）。"""
from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

from app.adapters.sms.aliyun_dypns import hint_for
from app.adapters.sms.aliyun_errors import describe_send_failure
from app.adapters.sms.factory import describe_sms_provider, get_sms_provider
from app.adapters.sms.mock import MockSmsProvider
from app.config import settings


def _load_check_external():
    """把 scripts/check_external.py 当模块载入，供纯函数级断言使用。"""
    path = Path(__file__).resolve().parent.parent / "scripts" / "check_external.py"
    spec = importlib.util.spec_from_file_location("check_external_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_domain_hint_points_to_missing_a_record(monkeypatch):
    """域名解析不到 IP 时，提示必须落在「还没加 A 记录」这个可执行动作上。

    历史缺陷：旧提示只说「请确认域名已备案并完成解析」，把最常见的
    「解析记录还没添加」这一情况误导成备案问题。
    """
    mod = _load_check_external()

    def _raise(*_args, **_kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(mod.socket, "gethostbyname", _raise)
    monkeypatch.setattr(mod, "nameservers", lambda _d: ["dns17.hichina.com", "dns18.hichina.com"])
    mod.rows.clear()
    mod.check_domain("ebikeguard.xyz")

    item, status, detail = mod.rows[0]
    assert item == "域名与 HTTPS 证书"
    assert status == mod.FAIL
    assert "A 记录" in detail
    assert "备案" in detail
    # NS 提示用于判断「记录加到哪个控制台」，必须带出来
    assert "dns17.hichina.com" in detail


def test_domain_hint_survives_ns_lookup_failure(monkeypatch):
    """NS 查询依赖外网；失败时只能少一句附加提示，不能把主结论带偏。"""
    mod = _load_check_external()

    def _raise(*_args, **_kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed")

    def _boom(_domain):
        raise OSError("network unreachable")

    monkeypatch.setattr(mod.socket, "gethostbyname", _raise)
    monkeypatch.setattr(mod, "nameservers", _boom)
    mod.rows.clear()
    mod.check_domain("ebikeguard.xyz")

    item, status, detail = mod.rows[0]
    assert status == mod.FAIL
    assert "A 记录" in detail
    assert "当前 NS" not in detail


def test_domain_skip_when_not_specified():
    """不传 --domain 时只提示、不算失败。"""
    mod = _load_check_external()
    mod.rows.clear()
    mod.check_domain(None)
    assert mod.rows[0][1] == mod.SKIP


@pytest.fixture(autouse=True)
def _restore_settings():
    snapshot = {
        "sms_provider": settings.sms_provider,
        "sms_sign_name": settings.sms_sign_name,
        "sms_template_code": settings.sms_template_code,
        "sms_template_extra_json": settings.sms_template_extra_json,
        "sms_access_key_id": settings.sms_access_key_id,
        "sms_access_key_secret": settings.sms_access_key_secret,
        "oss_access_key_id": settings.oss_access_key_id,
        "oss_access_key_secret": settings.oss_access_key_secret,
    }
    yield
    for k, v in snapshot.items():
        setattr(settings, k, v)


def test_mock_provider_is_ready():
    settings.sms_provider = "mock"
    info = describe_sms_provider()
    assert info["provider"] == "mock"
    assert info["ready"] is True


def test_aliyun_reports_missing_keys():
    settings.sms_provider = "aliyun"
    settings.sms_access_key_id = ""
    settings.sms_access_key_secret = ""
    settings.oss_access_key_id = ""
    settings.oss_access_key_secret = ""
    settings.sms_sign_name = ""
    settings.sms_template_code = ""

    info = describe_sms_provider()
    assert info["provider"] == "aliyun"
    assert info["ready"] is False
    assert set(info["missing"]) == {
        "SMS_ACCESS_KEY_ID",
        "SMS_ACCESS_KEY_SECRET",
        "SMS_SIGN_NAME",
        "SMS_TEMPLATE_CODE",
    }
    assert "SMS_SIGN_NAME" in info["detail"]


def test_aliyun_falls_back_to_oss_keys():
    settings.sms_provider = "aliyun"
    settings.sms_access_key_id = ""
    settings.sms_access_key_secret = ""
    settings.oss_access_key_id = "LTAI-oss-id"
    settings.oss_access_key_secret = "oss-secret"
    settings.sms_sign_name = "湘西州数据局"
    settings.sms_template_code = "SMS_123456"

    assert settings.effective_sms_ak_id == "LTAI-oss-id"
    assert settings.effective_sms_ak_secret == "oss-secret"
    assert settings.sms_ready() is True
    assert describe_sms_provider()["ready"] is True


def test_aliyun_not_ready_when_sign_or_template_missing():
    settings.sms_provider = "aliyun"
    settings.sms_access_key_id = "id"
    settings.sms_access_key_secret = "secret"
    settings.sms_sign_name = "签名"
    settings.sms_template_code = ""
    assert settings.sms_ready() is False
    assert "SMS_TEMPLATE_CODE" in describe_sms_provider()["missing"]


def test_mock_provider_never_leaks_code_when_debug_off(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    ok, message, echo = MockSmsProvider().send_code("13300000000", "123456")
    assert ok is True
    assert echo is None
    assert "发送" in message


def test_oss_and_amap_ready_flags():
    settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
    settings.oss_bucket = "b"
    settings.oss_access_key_id = "id"
    settings.oss_access_key_secret = "secret"
    assert settings.oss_ready() is True
    settings.oss_bucket = ""
    assert settings.oss_ready() is False

    settings.amap_key = "abc123"
    assert settings.amap_ready() is True
    settings.amap_key = ""
    assert settings.amap_ready() is False


# ---------------------------------------------------------------- 个人可用通道（aliyun_dypns）


def test_dypns_aliases_normalize_to_one_channel():
    for alias in ("aliyun_dypns", "dypns", "pnvs", "aliyun_pnvs", "DYpnsAPI"):
        settings.sms_provider = alias
        assert settings.sms_provider_key == "aliyun_dypns", alias


def test_dypns_reports_ready_with_granted_sign_and_template():
    settings.sms_provider = "dypns"
    settings.sms_access_key_id = "LTAI-dypns-id"
    settings.sms_access_key_secret = "dypns-secret"
    settings.sms_sign_name = "恒创联众"
    settings.sms_template_code = "100001"

    info = describe_sms_provider()
    assert info["provider"] == "aliyun_dypns"
    assert info["ready"] is True
    assert "仅支持验证码" in info["detail"]


def test_dypns_reports_missing_keys():
    settings.sms_provider = "aliyun_dypns"
    settings.sms_access_key_id = ""
    settings.sms_access_key_secret = ""
    settings.oss_access_key_id = ""
    settings.oss_access_key_secret = ""
    settings.sms_sign_name = ""
    settings.sms_template_code = ""

    info = describe_sms_provider()
    assert info["ready"] is False
    assert set(info["missing"]) == {
        "SMS_ACCESS_KEY_ID",
        "SMS_ACCESS_KEY_SECRET",
        "SMS_SIGN_NAME",
        "SMS_TEMPLATE_CODE",
    }


def test_unknown_provider_is_flagged_not_silently_mocked():
    """通道名写错时必须报错，不能悄悄退回 Mock 让人以为短信已生效。"""
    for typo in ("aliyun_dypn", "aliyuns", "sms"):
        settings.sms_provider = typo
        assert settings.sms_provider_key == "unknown", typo
        info = describe_sms_provider()
        assert info["ready"] is False
        assert "无法识别的通道名" in info["detail"]
        assert isinstance(get_sms_provider(), MockSmsProvider)  # 兜底仍可运行，但自检会报 ❌


def test_dypns_error_hints_are_chinese_actions():
    assert "流控" in hint_for("BUSINESS_LIMIT_CONTROL")
    assert "赠送模板" in hint_for("EntityNotExist.SmsTemplateCode")
    assert "赠送签名" in hint_for("EntityNotExist.SmsSignName")
    assert "赠送签名" in hint_for("INVALID_PARAMETERS")
    assert "开通" in hint_for("FUNCTION_NOT_OPENED")
    assert "dypns:SendSmsVerifyCode" in hint_for("isv.RAM_PERMISSION_DENY")
    assert hint_for("SOME_UNKNOWN_CODE") == ""
    assert hint_for(None) == ""
    # 短信服务(dysmsapi)专属返回码不应出现在本通道的提示表里，避免给出误导性建议
    assert hint_for("isv.SMS_TEMPLATE_ILLEGAL") == ""
    assert hint_for("isv.AMOUNT_NOT_ENOUGH") == ""


def test_hint_names_the_missing_template_variable():
    """回归：真机报「模版变量min内容非法」时，必须点名是哪个变量、怎么补。"""
    from app.adapters.sms.aliyun_dypns import hint_for_error

    hint = hint_for_error("isv.INVALID_PARAMETERS", "模版变量min内容非法")
    assert "min" in hint
    assert "SMS_TEMPLATE_EXTRA_JSON" in hint
    assert '"min":"5"' in hint

    other = hint_for_error("isv.INVALID_PARAMETERS", "模版变量 validMinutes 内容非法")
    assert "validMinutes" in other

    # 无法提取变量名时回退到静态码表
    assert hint_for_error("isv.INVALID_PARAMETERS", "参数不合法") == hint_for("isv.INVALID_PARAMETERS")
    assert hint_for_error("BUSINESS_LIMIT_CONTROL", "") == hint_for("BUSINESS_LIMIT_CONTROL")


def test_template_param_merges_code_variable_and_extra_json():
    settings.sms_template_extra_json = '{"min": "5"}'
    param = dict(settings.sms_template_param)
    param[settings.sms_template_param_name or "code"] = "123456"
    assert param == {"min": "5", "code": "123456"}

    settings.sms_template_extra_json = "{不是合法JSON"
    assert settings.sms_template_param == {}  # 坏配置不抛异常，只丢弃额外变量

    settings.sms_template_extra_json = "{}"
    assert settings.sms_template_param == {}


def test_dypns_notice_degrades_to_inbox_instead_of_silent_success():
    """dypns 通道没有通知短信能力，必须明确失败，让上层走站内信而不是假成功。"""
    from app.adapters.sms.aliyun_dypns import AliyunDypnsSmsProvider

    provider = AliyunDypnsSmsProvider.__new__(AliyunDypnsSmsProvider)  # 不构造客户端，避免依赖 SDK
    ok, message = provider.send_notice("13300000000", "审核通过")
    assert ok is False
    assert "站内信" in message


def test_dypns_request_contract_and_response_parsing(monkeypatch):
    """用假客户端验证：请求字段符合 dypns 契约，成功/失败两条分支都能解析。无网络调用。"""
    pytest.importorskip("alibabacloud_dypnsapi20170525")

    from app.adapters.sms.aliyun_dypns import AliyunDypnsSmsProvider

    settings.sms_provider = "aliyun_dypns"
    settings.sms_access_key_id = "LTAI-fake-id"
    settings.sms_access_key_secret = "fake-secret"
    settings.sms_sign_name = "恒创联众"
    settings.sms_template_code = "100001"
    settings.sms_template_extra_json = '{"min": "5"}'

    provider = AliyunDypnsSmsProvider()
    captured: dict = {}

    class _Model:
        biz_id = "biz-1"

    class _Body:
        def __init__(self, code: str, message: str = "成功") -> None:
            self.code = code
            self.message = message
            self.model = _Model()

    class _Response:
        def __init__(self, body: _Body) -> None:
            self.body = body

    def _ok(request):
        captured["request"] = request
        return _Response(_Body("OK"))

    monkeypatch.setattr(provider._client, "send_sms_verify_code", _ok)
    ok, message, echo = provider.send_code("13300000000", "246810", "login")
    assert ok is True
    assert echo is None  # 生产环境绝不回显验证码

    request = captured["request"]
    assert request.phone_number == "13300000000"
    assert request.sign_name == "恒创联众"
    assert request.template_code == "100001"
    assert json.loads(request.template_param) == {"min": "5", "code": "246810"}
    assert request.code_type == 1
    assert request.return_verify_code is False

    monkeypatch.setattr(
        provider._client,
        "send_sms_verify_code",
        lambda req: _Response(_Body("BUSINESS_LIMIT_CONTROL", "触发号码天级流控")),
    )
    ok2, message2, _ = provider.send_code("13300000000", "246810", "login")
    assert ok2 is False
    assert "流控" in message2 and "BUSINESS_LIMIT_CONTROL" in message2


def test_aliyun_send_code_keeps_three_tuple_on_failure(monkeypatch):
    """企业通道的失败分支同样必须返回三元组，否则登录接口解包会直接抛 ValueError。"""
    pytest.importorskip("alibabacloud_dysmsapi20170525")

    from app.adapters.sms.aliyun import AliyunSmsProvider

    settings.sms_provider = "aliyun"
    settings.sms_access_key_id = "LTAI-fake-id"
    settings.sms_access_key_secret = "fake-secret"
    settings.sms_sign_name = "湘西州数据局"
    settings.sms_template_code = "SMS_123456"

    provider = AliyunSmsProvider()

    class _Body:
        def __init__(self, code: str, message: str) -> None:
            self.code = code
            self.message = message

    class _Response:
        def __init__(self, body: _Body) -> None:
            self.body = body

    for code, message in (("OK", "成功"), ("isv.SMS_TEMPLATE_ILLEGAL", "模板未审核通过")):
        monkeypatch.setattr(
            provider._client,
            "send_sms",
            lambda req, _c=code, _m=message: _Response(_Body(_c, _m)),
        )
        result = provider.send_code("13300000000", "246810", "login")
        assert len(result) == 3
        assert result[2] is None  # 不回显验证码


# ---------------------------------------------------------------- SDK 异常的中文翻译


class _FakeTeaException(Exception):
    """模拟 Tea SDK 鉴权失败时抛出的异常：原因只存在于消息文本里。"""

    def __init__(self, text: str, data=None) -> None:
        super().__init__(text)
        self.data = data


def test_extract_error_code_from_all_three_sources():
    from app.adapters.sms.aliyun_errors import extract_error_code

    class WithCode(Exception):
        code = "BUSINESS_LIMIT_CONTROL"

    assert extract_error_code(WithCode("boom")) == "BUSINESS_LIMIT_CONTROL"
    assert extract_error_code(_FakeTeaException("x", {"Code": "isp.QPS_LIMIT"})) == "isp.QPS_LIMIT"
    assert (
        extract_error_code(
            _FakeTeaException(
                "Error: InvalidAccessKeyId.NotFound code: 404, Specified access key is not found."
            )
        )
        == "InvalidAccessKeyId.NotFound"
    )
    assert extract_error_code(RuntimeError("connection reset by peer")) is None


def test_send_failure_keeps_the_real_reason_instead_of_clientexception():
    """回归：真机曾把原因吞成「ClientException」，用户看不到可操作信息。"""
    from app.adapters.sms.aliyun_dypns import hint_for as dypns_hint

    exc = _FakeTeaException(
        "Error: InvalidAccessKeyId.NotFound code: 404, Specified access key is not found.",
        {"Code": "InvalidAccessKeyId.NotFound"},
    )
    text = describe_send_failure(exc, dypns_hint)
    assert "InvalidAccessKeyId.NotFound" in text
    assert "ClientException" not in text
    assert "AccessKey" in text  # 带上可操作的中文建议

    bare = describe_send_failure(ConnectionResetError("reset"), dypns_hint)
    assert "未返回错误码" in bare
    assert "ConnectionResetError" in bare
