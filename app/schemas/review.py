"""人工审核契约。"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core.constants import Decision, RejectReasonCode
from app.schemas.common import ORMModel
from app.schemas.report import AiResultOut, ImageOut


class ReviewQueueItem(ORMModel):
    id: int
    report_no: str
    behavior_type_name: str | None = None
    address_text: str
    status: str
    status_label: str
    ai_conclusion: str | None = None
    ai_confidence: float | None = None
    location_conflict: bool = False
    submitted_at: str
    submitted_at_cn: str | None = None
    waiting_hours: float = 0.0
    cover_url: str | None = None
    assigned_reviewer_name: str | None = None


class ReviewDetailOut(BaseModel):
    report: dict
    images: list[ImageOut] = Field(default_factory=list)
    ai_result: AiResultOut | None = None
    history: list[dict] = Field(default_factory=list)
    claimed_by_me: bool = False
    can_review: bool = False


class DecisionIn(BaseModel):
    decision: Decision
    reason_code: RejectReasonCode | None = None
    comment: str | None = Field(None, max_length=1000)

    @field_validator("reason_code")
    @classmethod
    def _norm_reason(cls, v):  # noqa: ANN001
        return v

    def validate_semantics(self) -> None:
        """业务校验：驳回必须带原因码；退回补充必须说明缺什么。"""
        if self.decision == Decision.REJECTED and not self.reason_code:
            raise ValueError("判定“不通过”时必须选择驳回原因")
        if self.decision == Decision.NEED_SUPPLEMENT and not (self.comment or "").strip():
            raise ValueError("退回补充时必须说明需要补充的内容")


class ClaimOut(BaseModel):
    ok: bool
    report_id: int
    assigned_reviewer_id: int | None = None
    status: str
    message: str | None = None


class AppealIn(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


class AppealHandleIn(BaseModel):
    status: str = Field(..., description="ACCEPTED / REJECTED")
    comment: str | None = Field(None, max_length=1000)
