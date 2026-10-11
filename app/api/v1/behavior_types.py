"""字典与公开配置路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.constants import REJECT_REASON_LABEL
from app.core.deps import get_current_user, require_reviewer
from app.database import get_db
from app.models import AppUser, BehaviorType
from app.schemas.report import BehaviorTypeOut
from app.services import config_service

router = APIRouter(tags=["字典与配置"])


@router.get("/behavior-types", response_model=list[BehaviorTypeOut], summary="行为类型字典")
def behavior_types(
    include_inactive: bool = False,
    _: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BehaviorTypeOut]:
    q = db.query(BehaviorType)
    if not include_inactive:
        q = q.filter(BehaviorType.is_active.is_(True))
    rows = q.order_by(BehaviorType.sort_order.asc(), BehaviorType.id.asc()).all()
    return [BehaviorTypeOut.model_validate(r) for r in rows]


@router.get("/reject-reasons", summary="驳回原因字典（审核员）")
def reject_reasons(_: AppUser = Depends(require_reviewer)) -> list[dict]:
    return [{"code": code, "label": label} for code, label in REJECT_REASON_LABEL.items()]


@router.get("/config/public", summary="前端公开配置")
def public_config() -> dict:
    return config_service.public_config()
