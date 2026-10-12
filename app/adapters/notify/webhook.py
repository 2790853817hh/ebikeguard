"""群机器人 Webhook 通道（企业微信 / 钉钉）。

启用步骤（详见 docs/外部服务接入指南.md「通知通道」一节）：
    1) 在群内添加机器人，复制 Webhook 地址（含密钥）
    2) `.env` 配置 WEBHOOK_KIND（wecom|dingtalk）与 WEBHOOK_URL
    3) 验证：`python scripts/check_external.py --notify --probe`
    4) 改 `.env` 后必须重启服务

安全：URL 含密钥（key=...），日志与一切输出一律经 mask_url() 脱敏，勿外贴。
失败处理原则：异常捕获后返回 (False, 可读中文原因)，绝不上抛。
"""
from __future__ import annotations

import json
import socket
from urllib.parse import urlsplit

import httpx

from app.adapters.notify.base import NotifyMessage, NotifyRecipient
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

KIND_LABELS = {"wecom": "企业微信", "dingtalk": "钉钉"}
WEBHOOK_KIND_CHOICES = "wecom / dingtalk"


def mask_url(url: str) -> str:
    """纯函数：URL 脱敏（保留 scheme + host 与末 4 位，其余打码）。"""
    raw = (url or "").strip()
    if not raw:
        return "(未配置)"
    tail = raw[-4:] if len(raw) >= 4 else "****"
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ""
        return f"{parts.scheme}://{host}/…{tail}"
    except ValueError:  # pragma: no cover - urlsplit 极少抛错
        return f"…{tail}"


def build_payload(kind: str, title: str, content: str) -> dict | None:
    """纯函数：按机器人类型组装 markdown 报文；未知类型返回 None。

    两类机器人均为 markdown 消息；钉钉要求携带 title 字段。
    """
    if kind == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n{content}"}}
    if kind == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n{content}"}}
    return None


def parse_robot_result(status_code: int, body_text: str) -> tuple[bool, str]:
    """纯函数：解析机器人响应（企微/钉钉成功均为 {"errcode":0,"errmsg":"ok"}）。"""
    try:
        data = json.loads(body_text) if body_text else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    errcode = data.get("errcode")
    if status_code == 200 and errcode in (0, "0"):
        return True, "群机器人已推送"
    errmsg = str(data.get("errmsg") or (body_text or ""))[:120]
    if status_code == 200 and errcode is None:
        return False, f"机器人响应无法解析（HTTP 200）：{errmsg}"
    return False, f"机器人返回异常：HTTP {status_code} errcode={errcode} {errmsg}"


def describe_webhook_failure(exc: Exception) -> str:
    """纯函数：常见网络异常 → 可读中文建议。"""
    if isinstance(exc, (socket.timeout, TimeoutError, httpx.TimeoutException)):
        return "Webhook 请求超时：检查服务器出网与机器人地址（默认超时 8 秒，可调 WEBHOOK_TIMEOUT_SECONDS）"
    if isinstance(exc, httpx.TransportError):
        return f"Webhook 连接失败：无法访问机器人地址（{type(exc).__name__}），检查 WEBHOOK_URL 与服务器出网"
    if isinstance(exc, httpx.HTTPError):
        return f"Webhook 请求异常（{type(exc).__name__}）：{exc}"
    return f"Webhook 推送异常（{type(exc).__name__}）：{exc}"


class WebhookNotifyProvider:
    """群机器人通道（企业微信 / 钉钉）。"""

    name = "webhook"
    channel = "webhook"

    def ready(self) -> bool:
        return settings.webhook_ready()

    def describe(self) -> dict:
        if not settings.webhook_url:
            return {
                "provider": settings.webhook_kind_key or "wecom",
                "ready": False,
                "detail": "未配置 WEBHOOK_URL（群机器人地址）",
            }
        if settings.webhook_kind_key == "unknown":
            return {
                "provider": settings.webhook_kind or "(空)",
                "ready": False,
                "detail": f"无法识别的 WEBHOOK_KIND，可选值：{WEBHOOK_KIND_CHOICES}",
            }
        return {
            "provider": settings.webhook_kind_key,
            "ready": True,
            "detail": f"{KIND_LABELS[settings.webhook_kind_key]}群机器人已配置（{mask_url(settings.webhook_url)}）",
        }

    def send(self, msg: NotifyMessage, recipient: NotifyRecipient) -> tuple[bool, str]:
        # recipient 无个人目标：群消息发到配置的群地址（仅内部受众使用本通道）
        if not settings.webhook_url:
            return False, "未配置 WEBHOOK_URL，跳过群机器人推送"
        payload = build_payload(settings.webhook_kind_key, msg.title, msg.content)
        if payload is None:
            return False, f"无法识别的 WEBHOOK_KIND：{settings.webhook_kind}（可选 {WEBHOOK_KIND_CHOICES}）"
        try:
            resp = httpx.post(settings.webhook_url, json=payload, timeout=settings.webhook_timeout_seconds)
            ok, detail = parse_robot_result(resp.status_code, resp.text)
            if not ok:
                logger.warning("Webhook 推送未成功（%s）：%s", mask_url(settings.webhook_url), detail)
            return ok, detail
        except Exception as exc:  # noqa: BLE001 —— 一切异常转可读原因，绝不上抛
            logger.warning("Webhook 推送失败（%s）：%s", mask_url(settings.webhook_url), exc)
            return False, describe_webhook_failure(exc)
