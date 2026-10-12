"""通知通道抽象接口。

统一约定（与 app/adapters/sms 保持一致）：
- 同步方法；一切异常在实现内捕获，转为 (False, 可读中文原因)，绝不上抛；
- 未配置/未实现的能力必须**显式失败**并说明原因（不假成功）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class NotifyMessage:
    """一条待下发的通知消息（渠道无关）。"""

    event: str  # NotificationEvent 值
    title: str  # 短标题（邮件主题 / 卡片标题）
    content: str  # 正文
    audience: str  # "reporter" | "internal"
    report_id: int | None = None
    link_url: str | None = None  # 可选：站内详情相对路径


@dataclass
class NotifyRecipient:
    """发送目标：不同渠道各取所需字段。"""

    user_id: int | None = None
    phone: str | None = None  # 短信通道
    email: str | None = None  # 邮件通道
    name: str | None = None  # 可选称呼


@runtime_checkable
class NotifyProvider(Protocol):
    name: str  # 通道实现名（smtp / webhook / wechat / mock / sms）
    channel: str  # NotificationChannel 值（用于落库与日志）

    def ready(self) -> bool:
        """配置是否完整（凭证齐备、可实际发送）。"""
        ...

    def describe(self) -> dict:
        """通道状态：{provider, ready, missing?, detail}，供自检 / meta 使用。"""
        ...

    def send(self, msg: NotifyMessage, recipient: NotifyRecipient) -> tuple[bool, str]:
        """发送；返回 (是否成功, 提示信息/失败原因)。"""
        ...
