"""统计与管理路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import REPORT_STATUS_LABEL, ReportStatus
from app.core.deps import ClientInfo, get_client, require_admin, require_reviewer
from app.core.exceptions import BadRequest, NotFound
from app.core.security import hash_password
from app.database import get_db
from app.models import AppUser, Report
from app.schemas.common import OkOut
from app.schemas.notification import OverviewOut
from app.schemas.review import AppealHandleIn
from app.services import (
    audit_log_service,
    audit_orchestrator,
    auth_service,
    config_service,
    report_service,
    review_service,
    stats_service,
)

router = APIRouter(tags=["统计与管理"])


# ---------------------------------------------------------------- 统计


@router.get("/admin/stats/overview", response_model=OverviewOut, summary="总览指标")
def stats_overview(
    _: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> OverviewOut:
    return OverviewOut(**stats_service.overview(db))


@router.get("/admin/stats/by-behavior", summary="按行为类型统计")
def stats_by_behavior(_: AppUser = Depends(require_reviewer), db: Session = Depends(get_db)) -> dict:
    data = stats_service.overview(db)
    return {"items": data["by_behavior"]}


@router.get("/admin/stats/by-region", summary="按区域统计")
def stats_by_region(
    limit: int = Query(30, ge=1, le=200),
    _: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": stats_service.by_region(db, limit)}


@router.get("/admin/stats/heatmap", summary="热力图点位")
def stats_heatmap(
    limit: int = Query(500, ge=1, le=2000),
    _: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": stats_service.heatmap_points(db, limit)}


@router.get("/admin/stats/reviewers", summary="审核员工作量")
def stats_reviewers(_: AppUser = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return {"items": stats_service.reviewer_stats(db)}


@router.get("/admin/stats/ai-metrics", summary="AI 与人工审核工作量对照")
def stats_ai(_: AppUser = Depends(require_reviewer), db: Session = Depends(get_db)) -> dict:
    return stats_service.audit_metrics(db)


# ---------------------------------------------------------------- 配置


@router.get("/admin/config", summary="系统配置列表")
def list_config(
    group: str | None = None,
    _: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    rows = config_service.list_configs(db, group)
    return {
        "items": [
            {
                "key": r.cfg_key,
                "value": r.cfg_value,
                "group": r.cfg_group,
                "description": r.description,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]
    }


@router.put("/admin/config/{key}", response_model=OkOut, summary="修改系统配置")
def set_config(
    key: str,
    value: str = Query(..., description="配置值"),
    user: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> OkOut:
    old = config_service.get_config(key)
    if old is None:
        raise NotFound("配置项不存在")
    config_service.set_config(db, key, value, updated_by=user.id)
    audit_log_service.log_operation(
        db,
        action="CONFIG_UPDATE",
        actor_id=user.id,
        target_type="system_config",
        target_id=key,
        before={"value": old},
        after={"value": value},
        ip=client.ip,
        user_agent=client.user_agent,
    )
    return OkOut(message="配置已更新")


# ---------------------------------------------------------------- 用户管理


@router.get("/admin/users", summary="用户列表")
def list_users(
    role: str | None = None,
    _: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(AppUser)
    if role:
        q = q.filter(AppUser.role == role)
    rows = q.order_by(AppUser.id.asc()).all()
    return {
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "phone": u.phone,
                "real_name": u.real_name,
                "role": u.role,
                "org_code": u.org_code,
                "status": u.status,
                "last_login_at": u.last_login_at,
            }
            for u in rows
        ]
    }


@router.post("/admin/users", response_model=OkOut, summary="创建审核员/管理员账号")
def create_user(
    username: str = Query(...),
    password: str = Query(..., min_length=8),
    real_name: str | None = Query(None),
    role: str = Query("reviewer"),
    org_code: str | None = Query(None),
    operator: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> OkOut:
    if role not in ("reviewer", "admin"):
        raise BadRequest("角色只能是 reviewer 或 admin")
    if db.query(AppUser).filter(AppUser.username == username).first():
        raise BadRequest("用户名已存在")
    user = AppUser(
        username=username,
        password_hash=hash_password(password),
        real_name=real_name,
        role=role,
        org_code=org_code,
        status="active",
    )
    db.add(user)
    db.commit()
    audit_log_service.log_operation(
        db,
        action="USER_CREATE",
        actor_id=operator.id,
        target_type="app_user",
        target_id=str(user.id),
        after={"username": username, "role": role},
        ip=client.ip,
        user_agent=client.user_agent,
    )
    return OkOut(message=f"账号 {username} 已创建")


@router.put("/admin/users/{user_id}/status", response_model=OkOut, summary="启用/停用账号")
def set_user_status(
    user_id: int,
    status: str = Query(..., description="active/disabled/locked"),
    operator: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> OkOut:
    if status not in ("active", "disabled", "locked"):
        raise BadRequest("状态取值不合法")
    user = db.get(AppUser, user_id)
    if not user:
        raise NotFound("用户不存在")
    before = user.status
    user.status = status
    user.token_version += 1  # 立即失效其登录凭证
    db.commit()
    audit_log_service.log_operation(
        db,
        action="USER_STATUS_CHANGE",
        actor_id=operator.id,
        target_type="app_user",
        target_id=str(user_id),
        before={"status": before},
        after={"status": status},
        ip=client.ip,
        user_agent=client.user_agent,
    )
    return OkOut(message="账号状态已更新")


# ---------------------------------------------------------------- 申诉与运维


@router.get("/admin/appeals", summary="申诉列表")
def list_appeals(
    status: str | None = None,
    _: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    rows = review_service.list_appeals(db, status)
    return {
        "items": [
            {
                "id": a.id,
                "report_id": a.report_id,
                "reason": a.reason,
                "status": a.status,
                "from_conclusion": a.from_conclusion,
                "created_at": a.created_at,
                "handled_at": a.handled_at,
                "handle_comment": a.handle_comment,
            }
            for a in rows
        ]
    }


@router.post("/admin/appeals/{appeal_id}/handle", response_model=OkOut, summary="处理申诉")
def handle_appeal(
    appeal_id: int,
    payload: AppealHandleIn,
    admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> OkOut:
    if payload.status not in ("ACCEPTED", "REJECTED"):
        raise BadRequest("status 只能是 ACCEPTED 或 REJECTED")
    review_service.handle_appeal(
        db,
        appeal_id,
        admin,
        accepted=payload.status == "ACCEPTED",
        comment=payload.comment,
        ip=client.ip,
        ua=client.user_agent,
    )
    return OkOut(message="申诉已处理")


@router.post("/admin/reports/{report_id}/ai-rerun", summary="重跑 AI 审核")
def rerun_ai(
    report_id: int,
    _: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    report_service.get_report_or_404(db, report_id)
    audit_orchestrator.run_ai_audit(report_id, force=True)
    db.expire_all()
    report = db.get(Report, report_id)
    return {"ok": True, "status": report.status, "ai_conclusion": report.ai_conclusion}


@router.post("/admin/reports/{report_id}/cancel", response_model=OkOut, summary="作废上报")
def cancel_report(
    report_id: int,
    admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> OkOut:
    from app.core.constants import ActorType, AuditAction
    from app.services import state_machine

    report = report_service.get_report_or_404(db, report_id)
    state_machine.apply_transition(
        db,
        report,
        AuditAction.CANCEL,
        actor_id=admin.id,
        actor_type=ActorType.ADMIN,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    db.commit()
    return OkOut(message="已作废")


@router.get("/admin/audit-logs", summary="操作审计日志")
def audit_logs(
    limit: int = Query(100, ge=1, le=500),
    _: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    from app.models import OperationAuditLog

    rows = db.query(OperationAuditLog).order_by(OperationAuditLog.id.desc()).limit(limit).all()
    return {
        "items": [
            {
                "id": r.id,
                "actor_id": r.actor_id,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "before": r.before_json,
                "after": r.after_json,
                "result": r.result,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get("/admin/status-labels", summary="状态字典")
def status_labels(_: AppUser = Depends(require_reviewer)) -> dict:
    return {k: v for k, v in REPORT_STATUS_LABEL.items()}


@router.get("/admin/reports", summary="全部上报（管理视图）")
def admin_reports(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(Report)
    if status:
        q = q.filter(Report.status == status)
    total = q.count()
    rows = (
        q.order_by(Report.submitted_at.desc(), Report.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [report_service.to_list_item(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "statuses": list(ReportStatus),
    }


# 供 auth_service 复用的占位（避免未使用告警）
_ = auth_service
