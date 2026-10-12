"""通知与反馈服务（站内信 + 多通道外发）。

- 站内信：所有关键节点必达（channel=inapp）；
- 外部通道：按「受众 → 通道名单」路由（system_config 可调），支持邮件 / 群机器人 Webhook /
  短信（复用短信适配器）；微信为预留通道，统一走 app/adapters/notify 适配层；
- 发送记录状态真实化：仅在**真正尝试后**写行，sent_status 如实记 sent / failed；
  未尝试（未就绪 / 无目标 / 频控）不写行、只记日志；
- 反馈文案遵循「得体、缓和、给出口」原则，避免生硬。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.adapters.notify.base import NotifyMessage, NotifyRecipient
from app.adapters.notify.factory import (
    get_email_provider,
    get_sms_notify_provider,
    get_webhook_provider,
    get_wechat_provider,
)
from app.config import settings
from app.core.constants import (
    INTERNAL_EVENTS,
    REPORTER_EVENTS,
    REJECT_REASON_LABEL,
    NotificationChannel,
    NotificationEvent,
    ReportStatus,
    Role,
    UserStatus,
)
from app.core.logging import get_logger
from app.core.utils import to_cn, utc_now, utc_now_iso
from app.models import Notification, Report, AppUser
from app.services import config_service

logger = get_logger(__name__)

_EXTERNAL_CHANNELS = (
    NotificationChannel.EMAIL,
    NotificationChannel.SMS,
    NotificationChannel.WECHAT,
    NotificationChannel.WEBHOOK,
)


def notify(
    db: Session,
    *,
    user_id: int,
    event: NotificationEvent | str,
    title: str,
    content: str,
    report_id: int | None = None,
    channel: NotificationChannel | str = NotificationChannel.INAPP,
    sent_status: str = "sent",
    commit: bool = False,
) -> Notification:
    row = Notification(
        user_id=user_id,
        report_id=report_id,
        event_type=str(event),
        title=title,
        content=content,
        channel=str(channel),
        sent_status=sent_status,
        created_at=utc_now_iso(),
    )
    db.add(row)
    if commit:
        db.commit()
    return row


# ---------------------------------------------------------------- 外发路由


def _route_channels(audience: str) -> list:
    """按 system_config 返回本次可用的外部通道 provider 列表（多通道并行）。

    三重过滤：总闸 → 通道名单 → 通道开关 + 就绪判定。未通过的跳过与记日志。
    """
    if not config_service.get_config_bool("notify.enabled", True):
        return []
    if audience == "reporter":
        raw = config_service.get_config("notify.reporter_channels", "email")
    else:
        raw = config_service.get_config("notify.internal_channels", "email,webhook")
    names = [n.strip().lower() for n in (raw or "").split(",") if n.strip()]

    providers: list = []
    for name in names:
        if name == "email":
            if config_service.get_config_bool("notify.email_enabled", True) and settings.email_ready():
                providers.append(get_email_provider())
        elif name == "webhook":
            if config_service.get_config_bool("notify.webhook_enabled", True) and settings.webhook_ready():
                providers.append(get_webhook_provider())
        elif name == "sms":
            adapter = get_sms_notify_provider()
            if adapter.ready():
                providers.append(adapter)
        elif name == "wechat":
            provider = get_wechat_provider()
            if provider.ready():  # 恒为 False（预留通道）；保留分支便于将来直接启用
                providers.append(provider)
        else:
            logger.warning("无法识别的通知通道名：%s（可选 email / webhook / sms / wechat）", name)
    return providers


def _rate_limited(db: Session, user_id: int) -> bool:
    """单用户每小时外发上限（notify.rate_limit_per_hour，<=0 表示不限）。"""
    limit = config_service.get_config_int("notify.rate_limit_per_hour", 20)
    if limit <= 0:
        return False
    since = (utc_now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.channel.in_([str(c) for c in _EXTERNAL_CHANNELS]),
            Notification.created_at >= since,
        )
        .count()
    )
    return count >= limit


def _dispatch_external(
    db: Session,
    user_id: int,
    event: str | NotificationEvent,
    title: str,
    content: str,
    report_id: int | None = None,
) -> None:
    """上报人事件的外部下发：逐通道发送，按真实结果落库（sent / failed）。

    绝不上抛：任何异常只记日志，不阻断上报主流程；未尝试（未就绪/频控）不写行。
    """
    ev = str(event)
    if ev not in REPORTER_EVENTS:
        return
    try:
        channels = _route_channels("reporter")
        if not channels:
            return
        user = db.get(AppUser, user_id)
        if not user:
            return
        if _rate_limited(db, user_id):
            logger.warning("通知频控生效（user=%s 超过 notify.rate_limit_per_hour），本次仅站内信", user_id)
            return
        msg = NotifyMessage(event=ev, title=title, content=content, audience="reporter", report_id=report_id)
        recipient = NotifyRecipient(
            user_id=user.id,
            phone=user.phone,
            email=getattr(user, "email", None),
            name=user.real_name or user.username or "",
        )
        for provider in channels:
            try:
                ok, detail = provider.send(msg, recipient)
            except Exception as exc:  # noqa: BLE001 —— 单个通道异常不影响其他通道
                ok, detail = False, f"通道异常：{exc}"
            notify(
                db,
                user_id=user_id,
                event=ev,
                title=title,
                content=content,
                report_id=report_id,
                channel=provider.channel,
                sent_status="sent" if ok else "failed",
            )
            if not ok:
                logger.warning("外部通知未成功（channel=%s）：%s", provider.channel, detail)
    except Exception as exc:  # noqa: BLE001 —— 外部通道异常绝不影响主流程
        logger.warning("外部通知下发异常（已忽略）：%s", exc)


def _dispatch_internal_external(
    event: str | NotificationEvent,
    title: str,
    content: str,
    report_id: int | None,
) -> None:
    """内部事件的外部下发（邮件 → NOTIFY_OPS_EMAIL；Webhook → 群）。

    内部外发无个人目标方，不落用户行；结果仅记日志。
    """
    ev = str(event)
    if ev not in INTERNAL_EVENTS:
        return
    try:
        channels = _route_channels("internal")
        if not channels:
            return
        msg = NotifyMessage(event=ev, title=title, content=content, audience="internal", report_id=report_id)
        for provider in channels:
            if provider.channel == NotificationChannel.EMAIL:
                targets = settings.notify_ops_emails
                if not targets:
                    logger.info("内部邮件未发送：未配置 NOTIFY_OPS_EMAIL")
                    continue
                for addr in targets:
                    try:
                        ok, detail = provider.send(msg, NotifyRecipient(email=addr))
                    except Exception as exc:  # noqa: BLE001
                        ok, detail = False, f"通道异常：{exc}"
                    if not ok:
                        logger.warning("内部邮件未成功（%s）：%s", addr, detail)
            else:
                try:
                    ok, detail = provider.send(msg, NotifyRecipient())
                except Exception as exc:  # noqa: BLE001
                    ok, detail = False, f"通道异常：{exc}"
                if not ok:
                    logger.warning("内部通知未成功（channel=%s）：%s", provider.channel, detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("内部通知下发异常（已忽略）：%s", exc)


# ---------------------------------------------------------------- 上报人节点文案


def notify_submitted(db: Session, report: Report) -> None:
    sla_hours = config_service.get_config_int("review.task_sla_hours", 24)
    days = max(1, round(sla_hours / 24)) if sla_hours < 48 else round(sla_hours / 24)
    title = "上报已受理"
    content = (
        f"您上报的「{report.behavior_type.name if report.behavior_type else '电动车不安全行为'}」已收到，"
        f"受理编号 {report.report_no}。我们正在为您核实，通常在 {days} 个工作日内反馈，感谢您对电动车安全的关注！"
    )
    notify(db, user_id=report.user_id, event=NotificationEvent.SUBMITTED, title=title, content=content, report_id=report.id)
    _dispatch_external(db, report.user_id, NotificationEvent.SUBMITTED, title, content, report.id)


def notify_final_result(db: Session, report: Report) -> None:
    reason_label = REJECT_REASON_LABEL.get(report.reject_reason_code or "", "情况说明")
    if report.status == ReportStatus.APPROVED:
        content = (
            "感谢您对电动车安全的关注！经核实，您上报的情况确实存在安全隐患，"
            f"已转交相关部门跟进处理。受理编号 {report.report_no}。"
        )
        title = "上报已核实通过"
    else:
        appeal_days = config_service.get_config_int("appeal.window_days", 7)
        content = (
            f"您上报的情况我们已认真核实。本次因【{reason_label}】未能受理。"
            f"如仍有疑问，可在 {appeal_days} 天内于「我的上报」中提出申诉，或补充材料后重新上报。"
            f"受理编号 {report.report_no}。"
        )
        title = "上报核实结果"
    notify(db, user_id=report.user_id, event=NotificationEvent.FINAL_RESULT, title=title, content=content, report_id=report.id)
    _dispatch_external(db, report.user_id, NotificationEvent.FINAL_RESULT, title, content, report.id)


def notify_need_supplement(db: Session, report: Report, comment: str | None) -> None:
    hours = config_service.get_config_int("review.supplement_sla_hours", 72)
    need = (comment or "").strip() or "更清晰的现场照片"
    title = "需要您补充材料"
    content = (
        f"为更准确地核实您上报的情况（编号 {report.report_no}），麻烦您补充：{need}。"
        f"请在 {hours} 小时内于「我的上报」中提交，谢谢配合！"
    )
    notify(db, user_id=report.user_id, event=NotificationEvent.NEED_SUPPLEMENT, title=title, content=content, report_id=report.id)
    _dispatch_external(db, report.user_id, NotificationEvent.NEED_SUPPLEMENT, title, content, report.id)


def notify_escalated(db: Session, report: Report) -> None:
    title = "上报已转专家复核"
    content = (
        f"您上报的情况（编号 {report.report_no}）涉及的情形较为复杂，已转由专家复核，"
        "我们会尽快给您反馈，请耐心等待。"
    )
    notify(db, user_id=report.user_id, event=NotificationEvent.ESCALATED, title=title, content=content, report_id=report.id)
    _dispatch_external(db, report.user_id, NotificationEvent.ESCALATED, title, content, report.id)


def notify_appeal_received(db: Session, report: Report) -> None:
    title = "申诉已受理"
    content = f"您对上报（编号 {report.report_no}）的申诉已收到，我们将安排复核后回复您。"
    notify(db, user_id=report.user_id, event=NotificationEvent.APPEAL_RECEIVED, title=title, content=content, report_id=report.id)
    _dispatch_external(db, report.user_id, NotificationEvent.APPEAL_RECEIVED, title, content, report.id)


def notify_appeal_resolved(db: Session, report: Report, accepted: bool, comment: str | None) -> None:
    if accepted:
        content = (
            f"经复核，您对上报（编号 {report.report_no}）的申诉成立，原结论已调整。"
            "感谢您的反馈与我们一同守护电动车安全。"
        )
    else:
        extra = f"复核说明：{comment}。" if (comment or "").strip() else ""
        content = f"经复核，您对上报（编号 {report.report_no}）的申诉暂不成立，维持原结论。{extra}感谢您的理解。"
    title = "申诉复核结果"
    notify(db, user_id=report.user_id, event=NotificationEvent.APPEAL_RESOLVED, title=title, content=content, report_id=report.id)
    _dispatch_external(db, report.user_id, NotificationEvent.APPEAL_RESOLVED, title, content, report.id)


# ---------------------------------------------------------------- 内部运营提醒


def _internal_recipients(db: Session) -> list[AppUser]:
    return (
        db.query(AppUser)
        .filter(AppUser.role.in_([Role.REVIEWER, Role.ADMIN]), AppUser.status == UserStatus.ACTIVE)
        .all()
    )


def _report_brief(report: Report) -> str:
    name = report.behavior_type.name if report.behavior_type else "电动车不安全行为"
    return f"「{name}」（编号 {report.report_no}）"


def _notify_internal(db: Session, report: Report, *, event: NotificationEvent, title: str, content: str) -> None:
    """内部提醒：逐 reviewer/admin 写站内信 + 内部通道外发（不落个人外发行）。"""
    for user in _internal_recipients(db):
        notify(db, user_id=user.id, event=event, title=title, content=content, report_id=report.id)
    _dispatch_internal_external(event, title, content, report.id)


def notify_internal_review_task(db: Session, report: Report) -> None:
    """新工单转入人工审核时提醒审核员/管理员。"""
    _notify_internal(
        db,
        report,
        event=NotificationEvent.REVIEW_TASK,
        title="新工单待审核",
        content=f"{_report_brief(report)}已转人工审核，请在工作台及时认领处理。",
    )


def notify_internal_sla_timeout(db: Session, report: Report) -> None:
    """人工队列超时提醒（由定时任务扫描触发）。"""
    hours = config_service.get_config_int("review.task_sla_hours", 24)
    _notify_internal(
        db,
        report,
        event=NotificationEvent.INTERNAL_SLA_TIMEOUT,
        title="工单超时提醒",
        content=f"{_report_brief(report)}已超过 {hours} 小时未完成人工审核，请尽快处理。",
    )


def notify_internal_appeal_pending(db: Session, report: Report) -> None:
    """上报人发起申诉时提醒内部人员。"""
    _notify_internal(
        db,
        report,
        event=NotificationEvent.INTERNAL_APPEAL_PENDING,
        title="申诉待处理",
        content=f"{_report_brief(report)}收到上报人申诉，请安排复核。",
    )


def notify_internal_escalated(db: Session, report: Report) -> None:
    """疑难升级时提醒双人复核（管理员/审核员）。"""
    _notify_internal(
        db,
        report,
        event=NotificationEvent.INTERNAL_ESCALATED,
        title="疑难工单待复核",
        content=f"{_report_brief(report)}已升级为疑难工单，需双人复核，请关注。",
    )


# ---------------------------------------------------------------- 站内信读取


def unread_count(db: Session, user_id: int) -> int:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.is_read.is_(False), Notification.channel == NotificationChannel.INAPP)
        .count()
    )


def list_notifications(db: Session, user_id: int, page: int, page_size: int, unread_only: bool = False):
    q = db.query(Notification).filter(
        Notification.user_id == user_id, Notification.channel == NotificationChannel.INAPP
    )
    if unread_only:
        q = q.filter(Notification.is_read.is_(False))
    total = q.count()
    rows = q.order_by(Notification.created_at.desc(), Notification.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def mark_read(db: Session, user_id: int, notification_id: int) -> Notification | None:
    row = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user_id)
        .first()
    )
    if row and not row.is_read:
        row.is_read = True
        row.read_at = utc_now_iso()
        db.commit()
    return row


def notification_to_dict(row: Notification) -> dict:
    return {
        "id": row.id,
        "report_id": row.report_id,
        "event_type": row.event_type,
        "title": row.title,
        "content": row.content,
        "channel": row.channel,
        "is_read": row.is_read,
        "created_at": row.created_at,
        "created_at_cn": to_cn(row.created_at),
    }
