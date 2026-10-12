"""邮件通道（SMTP，标准库实现，零新增依赖）。

启用步骤（详见 docs/外部服务接入指南.md「通知通道」一节）：
    1) `.env` 配置 SMTP_HOST / SMTP_PORT / SMTP_SECURITY / SMTP_USERNAME / SMTP_PASSWORD（授权码）
    2) 设 EMAIL_PROVIDER=smtp
    3) 验证：`python scripts/check_external.py --notify --probe`（真发一封）
    4) 改 `.env` 后必须重启服务，否则运行中的进程仍用旧配置

失败处理原则：一切异常在 send() 内捕获并返回 (False, 可读中文原因)，绝不中断业务主流程。
注意：阿里云服务器出站 25 端口被封装，必须使用 465（SSL）或 587（STARTTLS）。
"""
from __future__ import annotations

import smtplib
import socket
import ssl
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from app.adapters.notify.base import NotifyMessage, NotifyRecipient
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SUBJECT_PREFIX = "【电动车安全上报】"


def build_subject(title: str) -> str:
    """纯函数：邮件主题。"""
    return f"{SUBJECT_PREFIX}{title}"


def build_body(content: str, *, link_url: str | None = None) -> str:
    """纯函数：邮件正文（正文 + 可选站内链接 + 落款）。"""
    parts = [(content or "").strip()]
    if link_url:
        parts.append(f"详情可在系统内查看：{link_url}")
    parts.append("—— 电动车不安全使用行为识别系统（本邮件由系统自动发送，请勿直接回复）")
    return "\n\n".join(parts)


def build_message(*, from_addr: str, from_name: str | None, to_addr: str, subject: str, body: str) -> MIMEText:
    """纯函数：组装 MIMEText（UTF-8，主题经 RFC 2047 编码）。"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), from_addr)) if from_name else from_addr
    msg["To"] = to_addr
    return msg


def describe_smtp_failure(exc: Exception) -> str:
    """纯函数：常见 SMTP 异常 → 可读中文建议。"""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "SMTP 认证失败：请确认使用的是邮箱「授权码」而非登录密码"
            "（189 邮箱：设置→客户端设置；QQ 邮箱：设置→账户→开启 POP3/SMTP→生成授权码）"
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "SMTP 连接超时：检查 SMTP_HOST / SMTP_PORT；阿里云服务器必须使用 465 或 587（25 被封装）"
    if isinstance(exc, ssl.SSLError):
        return "SMTP SSL 握手失败：端口与加密方式不匹配（465 应配 ssl、587 应配 starttls）"
    if isinstance(exc, ConnectionRefusedError):
        return "SMTP 端口拒绝连接：检查端口配置（465=ssl / 587=starttls）"
    if isinstance(exc, OSError):
        return f"SMTP 网络错误（{type(exc).__name__}）：检查主机 / 端口 / 服务器出网"
    if isinstance(exc, smtplib.SMTPException):
        return f"SMTP 发送失败：{exc}"
    return f"邮件发送异常（{type(exc).__name__}）：{exc}"


class EmailSmtpProvider:
    """SMTP 邮件通道（EMAIL_PROVIDER=smtp 时启用）。"""

    name = "smtp"
    channel = "email"

    def ready(self) -> bool:
        return settings.email_ready()

    def describe(self) -> dict:
        missing: list[str] = []
        if not settings.smtp_host:
            missing.append("SMTP_HOST")
        if not settings.smtp_username:
            missing.append("SMTP_USERNAME")
        if not settings.smtp_password:
            missing.append("SMTP_PASSWORD")
        if settings.smtp_security_key == "unknown":
            missing.append("SMTP_SECURITY(应为 ssl / starttls)")
        if missing:
            return {"provider": "smtp", "ready": False, "missing": missing, "detail": "缺少配置：" + "、".join(missing)}
        return {
            "provider": "smtp",
            "ready": True,
            "detail": f"{settings.smtp_host}:{settings.smtp_port}（{settings.smtp_security_key}），发件人 {settings.smtp_from_addr}",
        }

    def send(self, msg: NotifyMessage, recipient: NotifyRecipient) -> tuple[bool, str]:
        if not (recipient.email or "").strip():
            return False, "收件人邮箱为空（未采集或未配置），跳过邮件发送"
        if settings.smtp_security_key == "unknown":
            return False, f"SMTP_SECURITY 无法识别：{settings.smtp_security}（可选 ssl / starttls）"
        if not self.ready():
            return False, "邮件通道配置不完整（核对 .env 的 SMTP_*，或运行 scripts/check_external.py --notify）"

        to_addr = recipient.email.strip()
        mime = build_message(
            from_addr=settings.smtp_from_addr,
            from_name=settings.smtp_from_name or None,
            to_addr=to_addr,
            subject=build_subject(msg.title),
            body=build_body(msg.content, link_url=msg.link_url),
        )
        host, port, timeout = settings.smtp_host, settings.smtp_port, settings.smtp_timeout_seconds
        try:
            context = ssl.create_default_context()
            if settings.smtp_security_key == "ssl":
                with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as server:
                    server.login(settings.smtp_username, settings.smtp_password)
                    server.send_message(mime, from_addr=settings.smtp_from_addr, to_addrs=[to_addr])
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(settings.smtp_username, settings.smtp_password)
                    server.send_message(mime, from_addr=settings.smtp_from_addr, to_addrs=[to_addr])
            return True, "邮件已发送"
        except Exception as exc:  # noqa: BLE001 —— 一切异常转可读原因，绝不上抛
            logger.warning("邮件发送失败（to=%s）：%s", to_addr, exc)
            return False, describe_smtp_failure(exc)
