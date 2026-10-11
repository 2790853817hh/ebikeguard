"""统计与运营服务。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.constants import REPORT_STATUS_LABEL, ReportStatus
from app.core.utils import CN_TZ, parse_iso, report_no_day, utc_now
from app.models import Appeal, BehaviorType, HumanAuditRecord, Report


def _today_prefix() -> str:
    return utc_now().astimezone(CN_TZ).strftime("%Y-%m-%d")


def overview(db: Session) -> dict:
    total = db.query(func.count(Report.id)).scalar() or 0

    today = report_no_day()
    today_reports = db.query(func.count(Report.id)).filter(Report.report_no.like(f"EV{today}%")).scalar() or 0

    def count_status(status: str) -> int:
        return db.query(func.count(Report.id)).filter(Report.status == status).scalar() or 0

    approved = count_status(ReportStatus.APPROVED)
    rejected = count_status(ReportStatus.REJECTED)
    finished = approved + rejected
    approval_rate = round(approved / finished, 4) if finished else 0.0

    appeal_pending = db.query(func.count(Appeal.id)).filter(Appeal.status == "PENDING").scalar() or 0

    # 平均审核时长（小时）：从提交到人工终审
    rows = (
        db.query(Report.submitted_at, Report.finished_at)
        .filter(Report.finished_at.is_not(None))
        .all()
    )
    durations = []
    for submitted, finished in rows:
        s, f = parse_iso(submitted), parse_iso(finished)
        if s and f:
            durations.append((f - s).total_seconds() / 3600.0)
    avg_hours = round(sum(durations) / len(durations), 2) if durations else 0.0

    by_behavior = (
        db.query(BehaviorType.name, func.count(Report.id))
        .join(Report, Report.behavior_type_id == BehaviorType.id)
        .group_by(BehaviorType.name)
        .order_by(func.count(Report.id).desc())
        .all()
    )

    by_status_rows = db.query(Report.status, func.count(Report.id)).group_by(Report.status).all()

    since = (utc_now() - timedelta(days=13)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = db.query(Report.report_no, Report.submitted_at).filter(Report.submitted_at >= since).all()
    day_counter: dict[str, int] = {}
    for _, submitted in recent:
        dt = parse_iso(submitted)
        if dt:
            key = dt.astimezone(CN_TZ).strftime("%Y-%m-%d")
            day_counter[key] = day_counter.get(key, 0) + 1
    by_day = [
        {"key": key, "label": key[5:], "count": day_counter[key]}
        for key in sorted(day_counter.keys())
    ]

    return {
        "total_reports": total,
        "today_reports": today_reports,
        "pending_human": count_status(ReportStatus.AI_DONE_PENDING_HUMAN),
        "reviewing": count_status(ReportStatus.HUMAN_REVIEWING),
        "need_supplement": count_status(ReportStatus.NEED_SUPPLEMENT),
        "approved": approved,
        "rejected": rejected,
        "archived": count_status(ReportStatus.ARCHIVED),
        "appeal_pending": appeal_pending,
        "approval_rate": approval_rate,
        "avg_review_hours": avg_hours,
        "by_behavior": [
            {"key": name, "label": name, "count": cnt} for name, cnt in by_behavior
        ],
        "by_status": [
            {"key": status, "label": REPORT_STATUS_LABEL.get(status, status), "count": cnt}
            for status, cnt in sorted(by_status_rows, key=lambda x: -x[1])
        ],
        "by_day": by_day,
    }


def by_region(db: Session, limit: int = 30) -> list[dict]:
    rows = (
        db.query(Report.address_text, func.count(Report.id))
        .group_by(Report.address_text)
        .order_by(func.count(Report.id).desc())
        .limit(limit)
        .all()
    )
    return [{"key": addr, "label": addr, "count": cnt} for addr, cnt in rows]


def heatmap_points(db: Session, limit: int = 500) -> list[dict]:
    rows = (
        db.query(Report.latitude, Report.longitude, Report.behavior_type_id, Report.status)
        .filter(Report.latitude.is_not(None), Report.longitude.is_not(None))
        .order_by(Report.submitted_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {"lat": lat, "lng": lng, "behavior_type_id": bt, "status": st}
        for lat, lng, bt, st in rows
    ]


def reviewer_stats(db: Session) -> list[dict]:
    from app.services.review_service import reviewer_workload

    return reviewer_workload(db)


def audit_metrics(db: Session) -> dict:
    """AI 与人工审核工作量对照。"""
    from app.models import AiAuditResult

    ai_total = db.query(func.count(AiAuditResult.id)).scalar() or 0
    ai_auto_closed = db.query(func.count(AiAuditResult.id)).filter(AiAuditResult.is_auto_closed.is_(True)).scalar() or 0
    human_total = db.query(func.count(HumanAuditRecord.id)).scalar() or 0
    human_second = db.query(func.count(HumanAuditRecord.id)).filter(HumanAuditRecord.is_second_review.is_(True)).scalar() or 0
    ai_conclusion_rows = db.query(AiAuditResult.conclusion, func.count(AiAuditResult.id)).group_by(AiAuditResult.conclusion).all()
    return {
        "ai_total": ai_total,
        "ai_auto_closed": ai_auto_closed,
        "human_total": human_total,
        "human_second_review": human_second,
        "human_reduced_by_ai": ai_auto_closed,
        "ai_conclusion_distribution": [
            {"key": c, "label": c, "count": n} for c, n in ai_conclusion_rows
        ],
    }
