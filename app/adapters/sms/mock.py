"""Mock 短信通道（开发/演示）。

不真实发送短信，验证码写入日志并（在 DEBUG 下）通过接口回显，便于端到端联调。
生产环境请切换 SMS_PROVIDER=aliyun 并实现 aliyun.py。
"""
from __future__ import annotations

from app.adapters.sms.base import SmsProvider
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MockSmsProvider(SmsProvider):
    name = "mock"

    def send_code(self, phone: str, code: str, scene: str = "login") -> tuple[bool, str, str | None]:
        logger.info("[MOCK SMS] 向 %s 发送验证码（scene=%s）：%s", phone, scene, code)
        echo = code if settings.debug else None
        return True, "验证码已发送（开发环境模拟）", echo

    def send_notice(self, phone: str, content: str) -> tuple[bool, str]:
        logger.info("[MOCK SMS] 向 %s 发送通知：%s", phone, content)
        return True, "已模拟发送"
