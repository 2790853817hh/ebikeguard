#!/usr/bin/env bash
# ============================================================
# 定时运维任务：超期未补充材料自动驳回 + 终态记录自动归档
# 建议 cron：*/30 * * * *  bash /opt/ev_audit/deploy/scripts/cron_tasks.sh
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ev_audit}"
cd "${APP_DIR}"

echo "[$(date '+%F %T')] 开始执行运维任务"

"./.venv/bin/python" - <<'PY'
"""内联运维脚本：SLA 超期处理 + 归档。"""
from datetime import timedelta

from app.core.constants import ActorType, AuditAction, ReportStatus
from app.core.utils import parse_iso, utc_now, utc_now_iso
from app.database import SessionLocal
from app.models import Report
from app.services import config_service, notification_service, state_machine

with SessionLocal() as db:
    # 1) 退回补充材料超期 -> 自动不通过
    sla_hours = config_service.get_config_int("review.supplement_sla_hours", 72)
    rows = db.query(Report).filter(Report.status == ReportStatus.NEED_SUPPLEMENT).all()
    rejected = 0
    for r in rows:
        base = parse_iso(r.updated_at)
        if base and utc_now() > base + timedelta(hours=sla_hours):
            r.reject_reason_code = "INSUFFICIENT_INFO"
            r.final_conclusion = ReportStatus.REJECTED
            r.final_comment = f"超过 {sla_hours} 小时未补充材料，自动结束本次受理。"
            state_machine.apply_transition(
                db, r, AuditAction.SLA_REJECT, actor_type=ActorType.SYSTEM,
                detail={"reason": "SLA timeout"},
            )
            notification_service.notify_final_result(db, r)
            rejected += 1
    db.commit()
    print(f"超期未补充自动驳回：{rejected} 条")

    # 2) 终态记录自动归档
    archive_days = config_service.get_config_int("report.auto_archive_days", 30)
    rows = (
        db.query(Report)
        .filter(Report.status.in_([ReportStatus.APPROVED, ReportStatus.REJECTED]))
        .all()
    )
    archived = 0
    for r in rows:
        base = parse_iso(r.finished_at)
        if base and utc_now() > base + timedelta(days=archive_days):
            state_machine.apply_transition(
                db, r, AuditAction.ARCHIVE, actor_type=ActorType.SYSTEM,
                detail={"auto": True},
            )
            archived += 1
    db.commit()
    print(f"自动归档：{archived} 条")
PY

echo "[$(date '+%F %T')] 运维任务完成"
