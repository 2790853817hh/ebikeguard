"""通知通道工厂（多通道）。

与 sms 的「单选 provider」不同：通知按「受众 → 通道名单」并行分发——
本模块只负责**单通道**实例获取与状态描述；路由、开关与频控在
app/services/notification_service.py（适配器层不反向依赖服务层）。
"""
from __future__ import annotations

from functools import lru_cache

from app.adapters.notify.base import NotifyMessage, NotifyProvider, NotifyRecipient
from app.adapters.notify.mock import MockNotifyProvider
from app.adapters.sms.factory import describe_sms_provider, get_sms_provider
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

EMAIL_PROVIDER_CHOICES = "mock / smtp"
WEBHOOK_KIND_CHOICES = "wecom / dingtalk"


class SmsNotifyAdapter:
    """把既有短信适配器包装为 NotifyProvider（仅复用 send_notice）。

    DYPNS 等「仅验证码」通道会显式失败并说明「降级站内信」，上层据此如实记录 failed。
    """

    name = "sms"
    channel = "sms"

    def ready(self) -> bool:
        return describe_sms_provider()["ready"]

    def describe(self) -> dict:
        info = describe_sms_provider()
        return {"provider": info.get("provider"), "ready": info.get("ready", False), "detail": info.get("detail", "")}

    def send(self, msg: NotifyMessage, recipient: NotifyRecipient) -> tuple[bool, str]:
        if not (recipient.phone or "").strip():
            return False, "收件人手机号为空，跳过短信发送"
        return get_sms_provider().send_notice(recipient.phone, f"【电动车安全上报】{msg.content}")


@lru_cache
def _email_smtp() -> NotifyProvider:
    from app.adapters.notify.email_smtp import EmailSmtpProvider

    return EmailSmtpProvider()


@lru_cache
def _webhook() -> NotifyProvider:
    from app.adapters.notify.webhook import WebhookNotifyProvider

    return WebhookNotifyProvider()


@lru_cache
def _wechat() -> NotifyProvider:
    from app.adapters.notify.wechat import WeChatNotifyProvider

    return WeChatNotifyProvider()


@lru_cache
def _sms_notify() -> SmsNotifyAdapter:
    return SmsNotifyAdapter()


def get_email_provider() -> NotifyProvider:
    """邮件通道实例（按 EMAIL_PROVIDER 归一化；unknown 告警且保持关闭）。"""
    key = settings.email_provider_key
    if key == "smtp":
        return _email_smtp()
    if key == "unknown":
        logger.warning(
            "无法识别的 EMAIL_PROVIDER=%s（可选值：%s），邮件通道保持关闭",
            settings.email_provider,
            EMAIL_PROVIDER_CHOICES,
        )
    return MockNotifyProvider()


def get_webhook_provider() -> NotifyProvider:
    return _webhook()


def get_wechat_provider() -> NotifyProvider:
    return _wechat()


def get_sms_notify_provider() -> SmsNotifyAdapter:
    return _sms_notify()


def describe_notify_channels() -> dict:
    """给自检脚本 / /api/v1/meta 用的多通道状态：{email, webhook, wechat, sms}。"""
    return {
        "email": _describe_email(),
        "webhook": get_webhook_provider().describe(),
        "wechat": get_wechat_provider().describe(),
        "sms": get_sms_notify_provider().describe(),
    }


def _describe_email() -> dict:
    key = settings.email_provider_key
    if key == "unknown":
        return {
            "provider": settings.email_provider or "(空)",
            "ready": False,
            "detail": f"无法识别的 EMAIL_PROVIDER，可选值：{EMAIL_PROVIDER_CHOICES}",
        }
    if key == "mock":
        return {
            "provider": "mock",
            "ready": False,
            "detail": "未启用（EMAIL_PROVIDER=mock；配置 SMTP 后改为 smtp 并重启服务）",
        }
    return _email_smtp().describe()
