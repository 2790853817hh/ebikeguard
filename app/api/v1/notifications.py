"""通知路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.exceptions import NotFound
from app.database import get_db
from app.models import AppUser
from app.schemas.common import OkOut, Page
from app.schemas.notification import NotificationOut, UnreadCountOut
from app.services import notification_service

router = APIRouter(tags=["通知"])


@router.get("/notifications", response_model=Page[NotificationOut], summary="我的通知")
def list_notifications(
    unread_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[NotificationOut]:
    rows, total = notification_service.list_notifications(db, user.id, page, page_size, unread_only)
    return Page[NotificationOut](
        items=[NotificationOut(**notification_service.notification_to_dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/notifications/unread-count", response_model=UnreadCountOut, summary="未读通知数")
def unread_count(user: AppUser = Depends(get_current_user), db: Session = Depends(get_db)) -> UnreadCountOut:
    return UnreadCountOut(unread=notification_service.unread_count(db, user.id))


@router.post("/notifications/{notification_id}/read", response_model=OkOut, summary="标记已读")
def mark_read(
    notification_id: int,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OkOut:
    row = notification_service.mark_read(db, user.id, notification_id)
    if not row:
        raise NotFound("通知不存在")
    return OkOut(message="已标记为已读")
