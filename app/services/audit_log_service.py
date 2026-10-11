"""审核留痕服务：状态流转日志 + 操作审计日志。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditActionLog, OperationAuditLog


def _dump(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)


def log_action(
    db: Session,
    *,
    report_id: int,
    action: str,
    actor_id: int | None = None,
    actor_type: str = "system",
    from_status: str | None = None,
    to_status: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    commit: bool = False,
) -> AuditActionLog:
    row = AuditActionLog(
        report_id=report_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=str(action),
        from_status=str(from_status) if from_status else None,
        to_status=str(to_status) if to_status else None,
        detail_json=_dump(detail),
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(row)
    if commit:
        db.commit()
    return row


def log_operation(
    db: Session,
    *,
    action: str,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    before: Any = None,
    after: Any = None,
    ip: str | None = None,
    user_agent: str | None = None,
    result: str = "success",
    commit: bool = True,
) -> OperationAuditLog:
    row = OperationAuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        before_json=_dump(before),
        after_json=_dump(after),
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        result=result,
    )
    db.add(row)
    if commit:
        db.commit()
    return row


def report_timeline(db: Session, report_id: int) -> list[AuditActionLog]:
    return (
        db.query(AuditActionLog)
        .filter(AuditActionLog.report_id == report_id)
        .order_by(AuditActionLog.created_at.asc(), AuditActionLog.id.asc())
        .all()
    )
