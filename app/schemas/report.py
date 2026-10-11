"""上报记录契约。"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class BehaviorTypeOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None = None
    risk_weight: float
    night_sensitive: bool
    sort_order: int


class AuditReasonOut(ORMModel):
    code: str
    text: str
    weight: float


class AiResultOut(ORMModel):
    conclusion: str
    confidence: float
    reasons: list[AuditReasonOut] = Field(default_factory=list)
    model_name: str
    model_version: str | None = None
    latency_ms: int | None = None
    is_auto_closed: bool = False
    created_at: str


class ImageOut(ORMModel):
    id: str
    width: int | None = None
    height: int | None = None
    size_bytes: int
    mime_type: str
    taken_at: str | None = None
    sort_order: int = 0
    url: str | None = None
    thumb_url: str | None = None


class TimelineItem(BaseModel):
    action: str
    label: str
    actor: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    comment: str | None = None
    at: str


class ReportListItem(ORMModel):
    id: int
    report_no: str
    behavior_type_name: str | None = None
    address_text: str
    status: str
    status_label: str
    ai_conclusion: str | None = None
    ai_confidence: float | None = None
    final_conclusion: str | None = None
    cover_url: str | None = None
    submitted_at: str
    submitted_at_cn: str | None = None


class ReportDetailOut(ReportListItem):
    longitude: float | None = None
    latitude: float | None = None
    location_source: str | None = None
    location_conflict: bool = False
    shoot_time: str | None = None
    remark: str | None = None
    reject_reason_code: str | None = None
    reject_reason_label: str | None = None
    final_comment: str | None = None
    images: list[ImageOut] = Field(default_factory=list)
    ai_result: AiResultOut | None = None
    timeline: list[TimelineItem] = Field(default_factory=list)
    can_withdraw: bool = False
    can_supplement: bool = False
    can_appeal: bool = False
    supplement_deadline: str | None = None


class SupplementIn(BaseModel):
    remark: str | None = Field(None, max_length=500)


class ReverseGeoIn(BaseModel):
    longitude: float
    latitude: float


class ReverseGeoOut(BaseModel):
    address: str | None = None
    source: str
    raw: dict | None = None
