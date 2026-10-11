"""审核相关表：AI 审核结果 / 人工审核记录 / 状态流转日志 / 操作审计日志。"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utc_now_iso
from app.database import Base


class AiAuditResult(Base):
    __tablename__ = "ai_audit_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("report.id", ondelete="CASCADE"), nullable=False)
    conclusion: Mapped[str] = mapped_column(String(16), nullable=False, comment="VIOLATION / NORMAL / UNCERTAIN")
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, comment="[{code,text,weight}]")
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="rule-based-v1 / qwen-vl-max ...")
    model_version: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    routed_state: Mapped[str | None] = mapped_column(String(32), comment="分流后的上报状态")
    is_auto_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)

    __table_args__ = (Index("idx_ai_report", "report_id"),)


class HumanAuditRecord(Base):
    __tablename__ = "human_audit_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("report.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False, comment="APPROVED/REJECTED/NEED_SUPPLEMENT/ESCALATED")
    reason_code: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    is_second_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parent_review_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("human_audit_record.id"))
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)

    __table_args__ = (
        Index("idx_human_report", "report_id"),
        Index("idx_human_reviewer", "reviewer_id", "created_at"),
    )


class AuditActionLog(Base):
    """上报记录状态流转全留痕（谁、何时、从什么状态到什么状态）。"""

    __tablename__ = "audit_action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("report.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="user/reviewer/admin/system")
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    detail_json: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)

    __table_args__ = (Index("idx_action_report", "report_id", "created_at"),)


class OperationAuditLog(Base):
    """管理动作与安全审计（改配置、改角色、导出等）。"""

    __tablename__ = "operation_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[str | None] = mapped_column(String(16), comment="success/failed")
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)

    __table_args__ = (Index("idx_oplog_actor", "actor_id", "created_at"),)
