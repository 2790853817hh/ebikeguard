"""短信工厂。

可选通道（SMS_PROVIDER）：
    mock           开发模拟，验证码写日志并由接口回显，不产生任何外部调用
    aliyun_dypns   阿里云「短信认证」服务——**个人实名即可开通**，仅验证码
    aliyun         阿里云「短信服务」——需企业/事业单位资质，支持验证码 + 通知
"""
from __future__ import annotations

from functools import lru_cache

from app.adapters.sms.base import SmsProvider
from app.adapters.sms.mock import MockSmsProvider
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER_LABELS = {
    "mock": "开发模拟通道",
    "aliyun_dypns": "阿里云短信认证（个人实名可开通，仅验证码）",
    "aliyun": "阿里云短信服务（需企业资质，验证码 + 通知）",
}
PROVIDER_CHOICES = "mock / aliyun_dypns / aliyun"


@lru_cache
def _aliyun() -> SmsProvider:
    from app.adapters.sms.aliyun import AliyunSmsProvider

    return AliyunSmsProvider()


@lru_cache
def _aliyun_dypns() -> SmsProvider:
    from app.adapters.sms.aliyun_dypns import AliyunDypnsSmsProvider

    return AliyunDypnsSmsProvider()


def get_sms_provider() -> SmsProvider:
    key = settings.sms_provider_key
    if key == "aliyun_dypns":
        return _aliyun_dypns()
    if key == "aliyun":
        return _aliyun()
    if key == "unknown":
        logger.warning(
            "无法识别的 SMS_PROVIDER=%s（可选值：%s），已退回开发模拟通道",
            settings.sms_provider,
            PROVIDER_CHOICES,
        )
    return MockSmsProvider()


def describe_sms_provider() -> dict:
    """给自检脚本/运维用的通道状态说明。"""
    key = settings.sms_provider_key
    if key == "unknown":
        return {
            "provider": settings.sms_provider or "(空)",
            "ready": False,
            "detail": f"无法识别的通道名，可选值：{PROVIDER_CHOICES}",
        }
    if key == "mock":
        return {
            "provider": "mock",
            "ready": True,
            "detail": "开发模拟通道，验证码写入日志并由接口回显（仅 DEBUG 环境）",
        }

    missing = []
    if not settings.effective_sms_ak_id:
        missing.append("SMS_ACCESS_KEY_ID")
    if not settings.effective_sms_ak_secret:
        missing.append("SMS_ACCESS_KEY_SECRET")
    if not settings.sms_sign_name:
        missing.append("SMS_SIGN_NAME")
    if not settings.sms_template_code:
        missing.append("SMS_TEMPLATE_CODE")

    if key == "aliyun_dypns":
        detail = (
            f"个人实名即可开通（签名={settings.sms_sign_name or '未填'}、模板={settings.sms_template_code or '未填'}）；"
            "仅支持验证码，通知短信自动降级为站内信"
        )
    else:
        detail = (
            f"需企业/事业单位资质（签名={settings.sms_sign_name or '未填'}、模板={settings.sms_template_code or '未填'}）；"
            "支持验证码与通知短信"
        )
    return {
        "provider": key,
        "ready": not missing,
        "missing": missing,
        "detail": detail if not missing else "缺少配置：" + "、".join(missing),
    }
