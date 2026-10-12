"""开发模拟通道：仅写日志，不产生任何外部调用。

说明：路由默认不含 mock 通道（避免把「模拟成功」写进真实发送记录）；
本类供本地调试、describe 展示与单测使用。
"""
from __future__ import annotations

from app.adapters.notify.base import NotifyMessage, NotifyRecipient
from app.core.logging import get_logger

logger = get_logger(__name__)


class MockNotifyProvider:
    name = "mock"
    channel = "mock"

    def ready(self) -> bool:
        return True

    def describe(self) -> dict:
        return {
            "provider": "mock",
            "ready": True,
            "detail": "开发模拟通道：仅写日志，不产生外部调用",
        }

    def send(self, msg: NotifyMessage, recipient: NotifyRecipient) -> tuple[bool, str]:
        logger.info(
            "[mock 通知] audience=%s event=%s to=%s | %s | %s",
            msg.audience,
            msg.event,
            recipient.email or recipient.phone or "-",
            msg.title,
            msg.content,
        )
        return True, "开发模拟通道：已写日志"
