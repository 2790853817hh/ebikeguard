"""人工审核服务：待审队列、认领、审核结论、疑难复核、审核历史。"""
from __future__ import annotations

from datetime import timezone

from sqlalchemy.orm import Session

from app.core.constants import (
    REPORT_STATUS_LABEL,
    ActorType,
    AuditAction,
    Decision,
    ReportStatus,
)
from app.core.exceptions import Conflict, Forbidden, NotFound
from app.core.logging import get_logger
from app.core.utils import parse_iso, to_cn, utc_now, utc_now_iso
from app.models import AppUser, HumanAuditRecord, Report
from app.services import audit_log_service, notification_service, state_machine
from app.services.report_service import ai_result_to_dict, image_to_dict, latest_ai_result

logger = get_logger(__name__)

REVIEW_STATUSES = (ReportStatus.AI_DONE_PENDING_HUMAN, ReportStatus.HUMAN_REVIEWING, ReportStatus.ESCALATED)


def _actor_type(user: AppUser) -> str:
    return ActorType.ADMIN if user.role == "admin" else ActorType.REVIEWER


def waiting_hours(report: Report) -> float:
    start = parse_iso(report.claimed_at or report.submitted_at)
    if not start:
        return 0.0
    delta = utc_now() - start.astimezone(timezone.utc)
    return round(delta.total_seconds() / 3600.0, 1)


# ---------------------------------------------------------------- 队列


def review_queue(
    db: Session,
    *,
    statuses: list[str] | None = None,
    behavior_type_id: int | None = None,
    ai_conclusion: str | None = None,
    keyword: str | None = None,
    assigned_to_me: int | None = None,
    page: int = 1,
    page_size: int = 20,
    order: str = "oldest",
):
    q = db.query(Report).filter(Report.status.in_(statuses or list(REVIEW_STATUSES)))
    if behavior_type_id:
        q = q.filter(Report.behavior_type_id == behavior_type_id)
    if ai_conclusion:
        q = q.filter(Report.ai_conclusion == ai_conclusion)
    if keyword:
        q = q.filter(Report.report_no.like(f"%{keyword}%") | Report.address_text.like(f"%{keyword}%"))
    if assigned_to_me:
        q = q.filter(Report.assigned_reviewer_id == assigned_to_me)

    total = q.count()
    if order == "newest":
        q = q.order_by(Report.submitted_at.desc(), Report.id.desc())
    elif order == "ai_confidence":
        q = q.order_by(Report.ai_confidence.desc().nullslast(), Report.submitted_at.asc())
    else:
        q = q.order_by(Report.submitted_at.asc(), Report.id.asc())
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def to_queue_item(db: Session, report: Report) -> dict:
    from app.services import image_service

    reviewer_name = None
    if report.assigned_reviewer_id:
        u = db.get(AppUser, report.assigned_reviewer_id)
        reviewer_name = (u.real_name or u.username) if u else None
    cover = image_service.build_image_url(report.images[0].id, thumb=True) if report.images else None
    return {
        "id": report.id,
        "report_no": report.report_no,
        "behavior_type_name": report.behavior_type.name if report.behavior_type else None,
        "address_text": report.address_text,
        "status": report.status,
        "status_label": REPORT_STATUS_LABEL.get(report.status, report.status),
        "ai_conclusion": report.ai_conclusion,
        "ai_confidence": report.ai_confidence,
        "location_conflict": report.location_conflict,
        "submitted_at": report.submitted_at,
        "submitted_at_cn": to_cn(report.submitted_at),
        "waiting_hours": waiting_hours(report),
        "cover_url": cover,
        "assigned_reviewer_name": reviewer_name,
    }


def review_detail(db: Session, report: Report, user: AppUser) -> dict:
    report_dict = {
        "id": report.id,
        "report_no": report.report_no,
        "behavior_type_name": report.behavior_type.name if report.behavior_type else None,
        "behavior_type_id": report.behavior_type_id,
        "address_text": report.address_text,
        "longitude": report.longitude,
        "latitude": report.latitude,
        "location_source": report.location_source,
        "location_conflict": report.location_conflict,
        "shoot_time": report.shoot_time,
        "remark": report.remark,
        "status": report.status,
        "status_label": REPORT_STATUS_LABEL.get(report.status, report.status),
        "submitted_at": report.submitted_at,
        "submitted_at_cn": to_cn(report.submitted_at),
        "assigned_reviewer_id": report.assigned_reviewer_id,
        "waiting_hours": waiting_hours(report),
        "reporter_masked": _mask_reporter(db, report),
    }
    return {
        "report": report_dict,
        "images": [image_to_dict(i) for i in report.images],
        "ai_result": ai_result_to_dict(latest_ai_result(db, report.id)),
        "history": review_history(db, report.id),
        "claimed_by_me": report.assigned_reviewer_id == user.id,
        "can_review": state_machine.can_do(report.status, AuditAction.APPROVE) or state_machine.can_do(
            report.status, AuditAction.SECOND_APPROVE
        ),
    }


def _mask_reporter(db: Session, report: Report) -> str | None:
    from app.core.utils import mask_phone

    reporter = db.get(AppUser, report.user_id)
    if not reporter:
        return None
    return mask_phone(reporter.phone) or reporter.username


# ---------------------------------------------------------------- 认领


def claim(db: Session, report: Report, user: AppUser, *, ip: str | None = None, ua: str | None = None) -> dict:
    """原子认领，避免多人并发抢单。"""
    if report.status == ReportStatus.HUMAN_REVIEWING and report.assigned_reviewer_id == user.id:
        return {"ok": True, "report_id": report.id, "assigned_reviewer_id": user.id, "status": report.status, "message": "您已认领该任务"}

    if report.status not in (ReportStatus.AI_DONE_PENDING_HUMAN, ReportStatus.ESCALATED):
        raise Conflict(f"当前状态「{report.status}」不可认领")

    now = utc_now_iso()
    updated = (
        db.query(Report)
        .filter(
            Report.id == report.id,
            Report.status == ReportStatus.AI_DONE_PENDING_HUMAN,
            Report.assigned_reviewer_id.is_(None),
        )
        .update(
            {
                Report.status: ReportStatus.HUMAN_REVIEWING,
                Report.assigned_reviewer_id: user.id,
                Report.claimed_at: now,
                Report.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    if updated == 0:
        db.rollback()
        raise Conflict("该任务已被其他审核员认领")

    db.expire(report)
    report = db.get(Report, report.id)
    audit_log_service.log_action(
        db,
        report_id=report.id,
        action=AuditAction.CLAIM,
        actor_id=user.id,
        actor_type=_actor_type(user),
        from_status=ReportStatus.AI_DONE_PENDING_HUMAN,
        to_status=ReportStatus.HUMAN_REVIEWING,
        ip=ip,
        user_agent=ua,
    )
    db.commit()
    return {"ok": True, "report_id": report.id, "assigned_reviewer_id": user.id, "status": report.status, "message": "认领成功"}


def assign(db: Session, report: Report, reviewer_id: int, operator: AppUser, *, ip: str | None = None, ua: str | None = None) -> Report:
    """管理员指派审核员。"""
    if operator.role != "admin":
        raise Forbidden("仅管理员可指派审核员")
    reviewer = db.get(AppUser, reviewer_id)
    if not reviewer or reviewer.role not in ("reviewer", "admin"):
        raise NotFound("审核员不存在")
    if report.status not in (ReportStatus.AI_DONE_PENDING_HUMAN, ReportStatus.ESCALATED):
        raise Conflict("当前状态不可指派")

    from_status = report.status
    report.assigned_reviewer_id = reviewer.id
    report.claimed_at = report.claimed_at or utc_now_iso()
    if from_status == ReportStatus.AI_DONE_PENDING_HUMAN:
        report.status = ReportStatus.HUMAN_REVIEWING
    report.updated_at = utc_now_iso()
    audit_log_service.log_action(
        db,
        report_id=report.id,
        action=AuditAction.CLAIM,
        actor_id=operator.id,
        actor_type=ActorType.ADMIN,
        from_status=from_status,
        to_status=report.status,
        detail={"assigned_to": reviewer.id, "assigned_name": reviewer.real_name or reviewer.username},
        ip=ip,
        user_agent=ua,
    )
    db.commit()
    db.refresh(report)
    return report


# ---------------------------------------------------------------- 结论


def _last_human_record(db: Session, report_id: int) -> HumanAuditRecord | None:
    return (
        db.query(HumanAuditRecord)
        .filter(HumanAuditRecord.report_id == report_id)
        .order_by(HumanAuditRecord.id.desc())
        .first()
    )


def decide(
    db: Session,
    report: Report,
    user: AppUser,
    *,
    decision: str,
    reason_code: str | None = None,
    comment: str | None = None,
    ip: str | None = None,
    ua: str | None = None,
) -> Report:
    """提交人工审核结论。"""
    actor = _actor_type(user)

    # 未认领的先自动认领（管理员可直接裁决）
    if report.status == ReportStatus.AI_DONE_PENDING_HUMAN:
        if user.role == "admin":
            report.status = ReportStatus.HUMAN_REVIEWING
            report.assigned_reviewer_id = report.assigned_reviewer_id or user.id
            report.claimed_at = report.claimed_at or utc_now_iso()
        else:
            claim(db, report, user, ip=ip, ua=ua)
            db.refresh(report)

    if report.status not in (ReportStatus.HUMAN_REVIEWING, ReportStatus.ESCALATED):
        raise Conflict(f"当前状态「{report.status}」不可提交审核结论")

    is_second = report.status == ReportStatus.ESCALATED
    if is_second and report.assigned_reviewer_id == user.id and user.role != "admin":
        raise Forbidden("疑难复核需由其他审核员或管理员完成")

    if not is_second and user.role != "admin" and report.assigned_reviewer_id != user.id:
        raise Forbidden("请先认领该任务后再提交结论")

    from_status = report.status

    if decision == Decision.APPROVED:
        action = AuditAction.SECOND_APPROVE if is_second else AuditAction.APPROVE
        target = ReportStatus.APPROVED
        report.final_conclusion = ReportStatus.APPROVED
        report.reject_reason_code = None
        report.final_comment = (comment or "").strip() or None
    elif decision == Decision.REJECTED:
        action = AuditAction.SECOND_REJECT if is_second else AuditAction.REJECT
        target = ReportStatus.REJECTED
        report.final_conclusion = ReportStatus.REJECTED
        report.reject_reason_code = reason_code
        report.final_comment = (comment or "").strip() or None
    elif decision == Decision.NEED_SUPPLEMENT:
        if is_second:
            raise Conflict("疑难复核阶段不支持退回补充材料")
        action = AuditAction.NEED_SUPPLEMENT
        target = ReportStatus.NEED_SUPPLEMENT
    elif decision == Decision.ESCALATED:
        if is_second:
            raise Conflict("该记录已处于疑难复核阶段")
        action = AuditAction.ESCALATE
        target = ReportStatus.ESCALATED
    else:
        raise Conflict(f"不支持的审核结论：{decision}")

    parent = _last_human_record(db, report.id)
    record = HumanAuditRecord(
        report_id=report.id,
        reviewer_id=user.id,
        decision=str(decision),
        reason_code=reason_code,
        comment=(comment or "").strip() or None,
        is_second_review=is_second,
        parent_review_id=parent.id if parent else None,
        from_state=from_status,
        to_state=target,
        created_at=utc_now_iso(),
    )
    db.add(record)

    state_machine.apply_transition(
        db,
        report,
        action,
        actor_id=user.id,
        actor_type=actor,
        target=target,
        detail={
            "decision": str(decision),
            "reason_code": reason_code,
            "comment": (comment or "").strip() or None,
        },
        ip=ip,
        user_agent=ua,
    )

    # 反馈上报人
    if target == ReportStatus.APPROVED or target == ReportStatus.REJECTED:
        notification_service.notify_final_result(db, report)
    elif target == ReportStatus.NEED_SUPPLEMENT:
        notification_service.notify_need_supplement(db, report, comment)
    elif target == ReportStatus.ESCALATED:
        notification_service.notify_escalated(db, report)
        notification_service.notify_internal_escalated(db, report)

    db.commit()
    db.refresh(report)
    logger.info("人工审核完成 上报=%s 结论=%s 审核员=%s", report.report_no, decision, user.id)
    return report


# ---------------------------------------------------------------- 申诉复议


def list_appeals(db: Session, status: str | None = None):
    from app.models import Appeal

    q = db.query(Appeal)
    if status:
        q = q.filter(Appeal.status == status)
    return q.order_by(Appeal.created_at.desc()).all()


def handle_appeal(
    db: Session,
    appeal_id: int,
    admin: AppUser,
    *,
    accepted: bool,
    comment: str | None = None,
    ip: str | None = None,
    ua: str | None = None,
):
    from app.models import Appeal

    appeal = db.get(Appeal, appeal_id)
    if not appeal:
        raise NotFound("申诉记录不存在")
    if appeal.status != "PENDING":
        raise Conflict("该申诉已处理")

    report = db.get(Report, appeal.report_id)
    if not report or report.status != ReportStatus.APPEALING:
        raise Conflict("该上报当前不处于申诉状态")
    if report.assigned_reviewer_id == admin.id:
        raise Forbidden("申诉复核需由非原审核员处理")

    appeal.status = "ACCEPTED" if accepted else "REJECTED"
    appeal.handler_id = admin.id
    appeal.handle_comment = (comment or "").strip() or None
    appeal.handled_at = utc_now_iso()

    target = _appeal_target(report, accepted)
    state_machine.apply_transition(
        db,
        report,
        AuditAction.APPEAL_RESOLVE,
        actor_id=admin.id,
        actor_type=ActorType.ADMIN,
        target=target,
        detail={"appeal_id": appeal.id, "accepted": accepted, "comment": (comment or "").strip() or None},
        ip=ip,
        user_agent=ua,
    )
    if accepted:
        report.final_conclusion = target
        report.final_comment = (comment or "").strip() or "经复核调整结论"

    notification_service.notify_appeal_resolved(db, report, accepted, comment)
    db.commit()
    db.refresh(appeal)
    return appeal


def _appeal_target(report: Report, accepted: bool) -> str:
    """申诉成立 -> 改判；申诉不成立 -> 维持原判并归档。"""
    if not accepted:
        return ReportStatus.ARCHIVED
    if report.final_conclusion == ReportStatus.APPROVED:
        return ReportStatus.REJECTED
    return ReportStatus.APPROVED


# ---------------------------------------------------------------- 历史与统计


def review_history(db: Session, report_id: int) -> list[dict]:
    rows = (
        db.query(HumanAuditRecord)
        .filter(HumanAuditRecord.report_id == report_id)
        .order_by(HumanAuditRecord.id.asc())
        .all()
    )
    names: dict[int, str] = {}
    ids = {r.reviewer_id for r in rows}
    if ids:
        for uid, real_name, username in db.query(AppUser.id, AppUser.real_name, AppUser.username).filter(AppUser.id.in_(ids)):
            names[uid] = real_name or username or f"用户{uid}"
    from app.core.constants import REJECT_REASON_LABEL

    return [
        {
            "id": r.id,
            "reviewer": names.get(r.reviewer_id, f"用户{r.reviewer_id}"),
            "decision": r.decision,
            "reason_code": r.reason_code,
            "reason_label": REJECT_REASON_LABEL.get(r.reason_code or ""),
            "comment": r.comment,
            "is_second_review": r.is_second_review,
            "from_state": r.from_state,
            "to_state": r.to_state,
            "created_at": r.created_at,
            "created_at_cn": to_cn(r.created_at),
        }
        for r in rows
    ]


def reviewer_workload(db: Session, since_iso: str | None = None) -> list[dict]:
    from sqlalchemy import func

    q = db.query(HumanAuditRecord.reviewer_id, func.count(HumanAuditRecord.id))
    if since_iso:
        q = q.filter(HumanAuditRecord.created_at >= since_iso)
    rows = q.group_by(HumanAuditRecord.reviewer_id).all()
    result = []
    for reviewer_id, count in rows:
        u = db.get(AppUser, reviewer_id)
        result.append(
            {
                "reviewer_id": reviewer_id,
                "reviewer": (u.real_name or u.username) if u else f"用户{reviewer_id}",
                "count": count,
            }
        )
    return sorted(result, key=lambda x: x["count"], reverse=True)


def overdue_tasks(db: Session, sla_hours: int) -> list[Report]:
    rows = db.query(Report).filter(Report.status.in_(list(REVIEW_STATUSES))).all()
    return [r for r in rows if waiting_hours(r) > sla_hours]
