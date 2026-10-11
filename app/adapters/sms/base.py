"""短信发送抽象接口。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SmsProvider(Protocol):
    name: str

    def send_code(self, phone: str, code: str, scene: str = "login") -> tuple[bool, str, str | None]:
        """发送验证码，返回 (是否成功, 提示信息, 开发环境回显码)。"""
        ...

    def send_notice(self, phone: str, content: str) -> tuple[bool, str]:
        """发送业务通知短信。"""
        ...
