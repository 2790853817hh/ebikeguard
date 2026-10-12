"""微信通道（占位：枚举与结构已预留，本轮不实现发送）。

说明：微信服务号模板消息 / 小程序订阅消息均要求 **单位（企业/组织）资质**，
个人主体无法开通。待单位主体与备案到位后，在此按统一协议实现真实发送即可启用；
在此之前 send() 显式失败并说明原因（不假成功）。
"""
from __future__ import annotations

from app.adapters.notify.base import NotifyMessage, NotifyRecipient


class WeChatNotifyProvider:
    name = "wechat"
    channel = "wechat"

    def ready(self) -> bool:
        return False

    def describe(self) -> dict:
        return {
            "provider": "wechat",
            "ready": False,
            "detail": "预留通道：需要单位/企业资质（服务号模板消息或小程序订阅消息），当前未实现发送",
        }

    def send(self, msg: NotifyMessage, recipient: NotifyRecipient) -> tuple[bool, str]:
        return False, "微信通道尚未实现（结构已预留：待单位资质到位后接入服务号模板消息/小程序订阅消息）"
