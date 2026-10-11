"""通知与反馈服务。

统一入口 `notify()`，渠道由 NotificationChannel 抽象；本期落地站内信，
短信/微信为 P1（已在 `_dispatch_external` 预留开关）。
反馈文案遵循「得体、缓和、给出口」原则，避免生硬。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters.sms.factory import get_sms_provider
from app.core.constants import (
    REJECT_REASON_LABEL,
    NotificationChannel,
    NotificationEvent,
    ReportStatus,
)
from app.core.logging import get_logger
from app.core.utils import to_cn, utc_now_iso
from app.models import Notification, Report, AppUser
from app.services import config_service

logger = get_logger(__name__)

#: 是否把关键节点同步下发短信（P1；需配置真实短信通道后开启）
SMS_ON_FINAL_RESULT = False


def notify(
    db: Session,
    *,
    user_id: int,
    event: NotificationEvent | str,
    title: str,
    content: str,
    report_id: int | None = None,
    channel: NotificationChannel | str = NotificationChannel.INAPP,
    commit: bool = False,
) -> Notification:
    row = Notification(
        user_id=user_id,
        report_id=report_id,
        event_type=str(event),
        title=title,
        content=content,
        channel=str(channel),
        sent_status="sent",
        created_at=utc_now_iso(),
    )
    db.add(row)
    if commit:
        db.commit()
    return row


def _dispatch_external(db: Session, user_id: int, event: str, title: str, content: str) -> None:
    """关键节点可扩展下发短信/微信（P1）。"""
    if not SMS_ON_FINAL_RESULT or event != NotificationEvent.FINAL_RESULT:
        return
    user = db.get(AppUser, user_id)
    if not user or not user.phone:
        return
    try:
        ok, msg = get_sms_provider().send_notice(user.phone, f"【电动车安全上报】{content}")
        notify(
            db,
            user_id=user_id,
            event=event,
            title=title,
            content=content,
            channel=NotificationChannel.SMS,
        )
        if not ok:
            logger.warning("短信下发失败：%s", msg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("短信下发异常（忽略）：%s", exc)


# ---------------------------------------------------------------- 各节点文案


def notify_submitted(db: Session, report: Report) -> None:
    sla_hours = config_service.get_config_int("review.task_sla_hours", 24)
    days = max(1, round(sla_hours / 24)) if sla_hours < 48 else round(sla_hours / 24)
    content = (
        f"您上报的「{report.behavior_type.name if report.behavior_type else '电动车不安全行为'}」已收到，"
        f"受理编号 {report.report_no}。我们正在为您核实，通常在 {days} 个工作日内反馈，感谢您对电动车安全的关注！"
    )
    notify(
        db,
        user_id=report.user_id,
        event=NotificationEvent.SUBMITTED,
        title="上报已受理",
        content=content,
        report_id=report.id,
    )


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
    notify(
        db,
        user_id=report.user_id,
        event=NotificationEvent.FINAL_RESULT,
        title=title,
        content=content,
        report_id=report.id,
    )
    _dispatch_external(db, report.user_id, NotificationEvent.FINAL_RESULT, title, content)


def notify_need_supplement(db: Session, report: Report, comment: str | None) -> None:
    hours = config_service.get_config_int("review.supplement_sla_hours", 72)
    need = (comment or "").strip() or "更清晰的现场照片"
    content = (
        f"为更准确地核实您上报的情况（编号 {report.report_no}），麻烦您补充：{need}。"
        f"请在 {hours} 小时内于「我的上报」中提交，谢谢配合！"
    )
    notify(
        db,
        user_id=report.user_id,
        event=NotificationEvent.NEED_SUPPLEMENT,
        title="需要您补充材料",
        content=content,
        report_id=report.id,
    )


def notify_escalated(db: Session, report: Report) -> None:
    notify(
        db,
        user_id=report.user_id,
        event=NotificationEvent.ESCALATED,
        title="上报已转专家复核",
        content=(
            f"您上报的情况（编号 {report.report_no}）涉及的情形较为复杂，已转由专家复核，"
            "我们会尽快给您反馈，请耐心等待。"
        ),
        report_id=report.id,
    )


def notify_appeal_received(db: Session, report: Report) -> None:
    notify(
        db,
        user_id=report.user_id,
        event=NotificationEvent.APPEAL_RECEIVED,
        title="申诉已受理",
        content=f"您对上报（编号 {report.report_no}）的申诉已收到，我们将安排复核后回复您。",
        report_id=report.id,
    )


def notify_appeal_resolved(db: Session, report: Report, accepted: bool, comment: str | None) -> None:
    if accepted:
        content = (
            f"经复核，您对上报（编号 {report.report_no}）的申诉成立，原结论已调整。"
            "感谢您的反馈与我们一同守护电动车安全。"
        )
        title = "申诉复核结果"
    else:
        extra = f"复核说明：{comment}。" if (comment or "").strip() else ""
        content = f"经复核，您对上报（编号 {report.report_no}）的申诉暂不成立，维持原结论。{extra}感谢您的理解。"
        title = "申诉复核结果"
    notify(
        db,
        user_id=report.user_id,
        event=NotificationEvent.APPEAL_RESOLVED,
        title=title,
        content=content,
        report_id=report.id,
    )


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
