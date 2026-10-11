"""阿里云短信类 SDK 异常的中文翻译（纯函数，便于单测）。

背景：Tea 系列 SDK 在网络层/鉴权层失败时抛出的异常，其可读原因藏在三个地方——
异常的 `.code`、`.data['Code']`、或消息文本 `Error: xxx code: 404, ...`。
只取 `type(exc).__name__` 会退化成 `ClientException`，用户拿不到任何可操作信息。
"""
from __future__ import annotations

import re

# 形如 "Error: InvalidAccessKeyId.NotFound code: 404, Specified access key is not found."
_CODE_IN_MESSAGE = re.compile(r"(?:Error:\s*)?([A-Za-z][A-Za-z0-9_.]*)\s+code\s*:", re.IGNORECASE)


def extract_error_code(exc: BaseException) -> str | None:
    """按 .code → .data['Code'] → 消息文本 的顺序提取阿里云错误码，取不到返回 None。"""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip():
        return code.strip()

    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        for key in ("Code", "code"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    match = _CODE_IN_MESSAGE.search(str(exc))
    return match.group(1) if match else None


def describe_send_failure(exc: BaseException, hint_for) -> str:
    """把一个发送异常翻译成用户可读的中文原因。

    hint_for: 形如 `(code) -> 中文建议` 的映射函数，由各通道提供。
    """
    code = extract_error_code(exc)
    hint = hint_for(code) if code else ""
    if code:
        return f"短信发送失败：{code} —— {hint or '请检查网络连通性、AccessKey 权限与阿里云账号状态'}"
    return f"短信发送失败：未返回错误码（{type(exc).__name__}）—— 请检查网络连通性与 AccessKey 配置"
