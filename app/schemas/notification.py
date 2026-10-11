"""通知与统计契约。"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: int
    report_id: int | None = None
    event_type: str
    title: str
    content: str
    channel: str
    is_read: bool
    created_at: str
    created_at_cn: str | None = None


class UnreadCountOut(BaseModel):
    unread: int


class StatBucket(BaseModel):
    key: str
    label: str
    count: int


class OverviewOut(BaseModel):
    total_reports: int
    today_reports: int
    pending_human: int
    reviewing: int
    need_supplement: int
    approved: int
    rejected: int
    archived: int
    appeal_pending: int
    approval_rate: float
    avg_review_hours: float
    by_behavior: list[StatBucket] = []
    by_status: list[StatBucket] = []
    by_day: list[StatBucket] = []
