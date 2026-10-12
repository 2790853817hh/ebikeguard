"""通知通道适配层单测（离线：不打真实网络、不发真实邮件）。

覆盖：邮件报文纯函数 / 企微与钉钉报文 / 机器人响应解析 / URL 脱敏 / SMTP 错误翻译 /
就绪判定与 unknown 兜底 / 微信占位显式失败 / 短信桥接适配。
"""
from __future__ import annotations

import smtplib
import ssl
from email.header import decode_header, make_header

import pytest

from app.adapters.notify import webhook as webhook_mod
from app.adapters.notify.base import NotifyMessage, NotifyRecipient
from app.adapters.notify.email_smtp import (
    EmailSmtpProvider,
    build_body,
    build_message,
    build_subject,
    describe_smtp_failure,
)
from app.adapters.notify.factory import describe_notify_channels, get_email_provider, get_sms_notify_provider
from app.adapters.notify.mock import MockNotifyProvider
from app.adapters.notify.wechat import WeChatNotifyProvider
from app.config import settings

_MSG = NotifyMessage(event="FINAL_RESULT", title="上报核实结果", content="正文内容", audience="reporter")


@pytest.fixture(autouse=True)
def _restore_settings():
    """快照/复原 settings——本文件直接改 pydantic 单例属性，避免跨用例污染。"""
    keys = [
        "email_provider", "smtp_host", "smtp_port", "smtp_security", "smtp_username",
        "smtp_password", "smtp_from", "smtp_from_name", "webhook_kind", "webhook_url",
    ]
    snapshot = {k: getattr(settings, k) for k in keys}
    yield
    for key, value in snapshot.items():
        setattr(settings, key, value)


def _configure_smtp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_security", "ssl")
    monkeypatch.setattr(settings, "smtp_username", "u@test.cn")
    monkeypatch.setattr(settings, "smtp_password", "authcode")


# ---------------------------------------------------------------- 邮件通道


def test_build_subject_and_body_are_pure():
    assert build_subject("需要您补充材料") == "【电动车安全上报】需要您补充材料"
    body = build_body("正文内容", link_url="report-detail.html?id=3")
    assert "正文内容" in body and "report-detail.html?id=3" in body and "请勿直接回复" in body
    assert "详情可在系统内查看" not in build_body("正文内容")


def test_build_message_utf8_headers():
    msg = build_message(
        from_addr="a@189.cn", from_name="电动车安全上报", to_addr="b@qq.com",
        subject=build_subject("测试"), body="你好",
    )
    assert msg["To"] == "b@qq.com"
    assert "a@189.cn" in msg["From"]
    decoded = str(make_header(decode_header(msg["Subject"])))
    assert decoded == "【电动车安全上报】测试"


def test_describe_smtp_failure_messages():
    assert "授权码" in describe_smtp_failure(smtplib.SMTPAuthenticationError(535, b"auth"))
    assert "465" in describe_smtp_failure(TimeoutError("t"))
    assert "starttls" in describe_smtp_failure(ssl.SSLError("handshake"))


def test_email_send_guards_without_network(monkeypatch):
    provider = EmailSmtpProvider()
    ok, reason = provider.send(_MSG, NotifyRecipient(email=""))
    assert ok is False and "收件人邮箱为空" in reason

    monkeypatch.setattr(settings, "email_provider", "mock")
    ok, reason = provider.send(_MSG, NotifyRecipient(email="a@b.cn"))
    assert ok is False and "配置不完整" in reason


def test_email_send_with_fake_smtp(monkeypatch):
    _configure_smtp(monkeypatch)
    sent: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, mime, from_addr=None, to_addrs=None):
            sent["to"] = to_addrs
            sent["subject"] = str(make_header(decode_header(mime["Subject"])))

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    ok, reason = EmailSmtpProvider().send(_MSG, NotifyRecipient(email="citizen@qq.com"))
    assert ok is True and reason == "邮件已发送"
    assert sent["host"] == "smtp.test" and sent["port"] == 465
    assert sent["to"] == ["citizen@qq.com"]
    assert sent["subject"] == "【电动车安全上报】上报核实结果"


def test_email_send_auth_failure_translated(monkeypatch):
    _configure_smtp(monkeypatch)

    class BadSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad auth")

    monkeypatch.setattr(smtplib, "SMTP_SSL", BadSMTP)
    ok, reason = EmailSmtpProvider().send(_MSG, NotifyRecipient(email="x@y.cn"))
    assert ok is False and "授权码" in reason


# ---------------------------------------------------------------- Webhook 通道


def test_build_payload_wecom_and_dingtalk():
    wecom = webhook_mod.build_payload("wecom", "标题", "内容")
    assert wecom == {"msgtype": "markdown", "markdown": {"content": "**标题**\n内容"}}
    ding = webhook_mod.build_payload("dingtalk", "标题", "内容")
    assert ding is not None and ding["msgtype"] == "markdown"
    assert ding["markdown"]["title"] == "标题" and "### 标题" in ding["markdown"]["text"]
    assert webhook_mod.build_payload("slack", "标题", "内容") is None


def test_parse_robot_result():
    ok, _ = webhook_mod.parse_robot_result(200, '{"errcode":0,"errmsg":"ok"}')
    assert ok is True
    ok, detail = webhook_mod.parse_robot_result(200, '{"errcode":93000,"errmsg":"invalid webhook url"}')
    assert ok is False and "93000" in detail
    ok, detail = webhook_mod.parse_robot_result(200, "<html>proxy error</html>")
    assert ok is False and "无法解析" in detail
    ok, detail = webhook_mod.parse_robot_result(500, '{"errcode":0}')
    assert ok is False and "500" in detail


def test_mask_url_hides_key():
    url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef12-3456-7890-abcd-ef1234567890"
    masked = webhook_mod.mask_url(url)
    assert "qyapi.weixin.qq.com" in masked
    assert masked.endswith("7890")
    assert "abcdef12-3456" not in masked
    assert webhook_mod.mask_url("") == "(未配置)"


# ---------------------------------------------------------------- 占位 / 兜底 / 短信桥接


def test_wechat_placeholder_explicit_failure():
    provider = WeChatNotifyProvider()
    assert provider.ready() is False
    ok, reason = provider.send(_MSG, NotifyRecipient())
    assert ok is False and "未实现" in reason


def test_describe_notify_channels_states(monkeypatch):
    monkeypatch.setattr(settings, "email_provider", "mock")
    monkeypatch.setattr(settings, "webhook_url", "")
    info = describe_notify_channels()
    assert info["email"]["ready"] is False and "未启用" in info["email"]["detail"]
    assert info["webhook"]["ready"] is False
    assert info["wechat"]["ready"] is False

    monkeypatch.setattr(settings, "email_provider", "smtp")
    info = describe_notify_channels()
    assert info["email"]["ready"] is False and "SMTP_HOST" in info["email"]["detail"]

    _configure_smtp(monkeypatch)
    info = describe_notify_channels()
    assert info["email"]["ready"] is True and "smtp.test" in info["email"]["detail"]

    monkeypatch.setattr(settings, "webhook_kind", "dingtalk")
    monkeypatch.setattr(settings, "webhook_url", "https://oapi.dingtalk.com/robot/send?access_token=abc1234567890")
    info = describe_notify_channels()
    assert info["webhook"]["ready"] is True and info["webhook"]["provider"] == "dingtalk"
    assert "abc1234567890" not in info["webhook"]["detail"]


def test_unknown_providers_do_not_silently_send(monkeypatch):
    monkeypatch.setattr(settings, "email_provider", "typo-mail")
    assert isinstance(get_email_provider(), MockNotifyProvider)  # 关闭而非误发
    info = describe_notify_channels()["email"]
    assert info["ready"] is False and "无法识别" in info["detail"]

    monkeypatch.setattr(settings, "webhook_kind", "slack")
    monkeypatch.setattr(settings, "webhook_url", "https://example.com/hook")
    info = describe_notify_channels()["webhook"]
    assert info["ready"] is False and "无法识别" in info["detail"]


def test_sms_notify_adapter_delegates(monkeypatch):
    captured: dict = {}

    class StubSms:
        def send_notice(self, phone, content):
            captured["phone"], captured["content"] = phone, content
            return True, "stub ok"

    monkeypatch.setattr("app.adapters.notify.factory.get_sms_provider", lambda: StubSms())
    adapter = get_sms_notify_provider()
    ok, _ = adapter.send(_MSG, NotifyRecipient(phone="13800000000"))
    assert ok is True and captured["phone"] == "13800000000"
    assert captured["content"].startswith("【电动车安全上报】")

    ok, reason = adapter.send(_MSG, NotifyRecipient(phone=""))
    assert ok is False and "手机号为空" in reason
