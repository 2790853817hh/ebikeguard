"""上报记录表。"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ReportStatus
from app.core.utils import utc_now_iso
from app.database import Base


class Report(Base):
    __tablename__ = "report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False)
    behavior_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("behavior_type.id"), nullable=False)

    # 行为信息与定位
    address_text: Mapped[str] = mapped_column(String(255), nullable=False)
    longitude: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    location_source: Mapped[str | None] = mapped_column(String(16), comment="geolocation/exif/manual/map")
    location_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="浏览器定位与 EXIF GPS 不一致")
    shoot_time: Mapped[str | None] = mapped_column(String(32), comment="拍摄时间（用户填写或 EXIF）")
    remark: Mapped[str | None] = mapped_column(Text)

    # 状态与结论
    status: Mapped[str] = mapped_column(String(32), default=ReportStatus.SUBMITTED, nullable=False)
    ai_conclusion: Mapped[str | None] = mapped_column(String(16), comment="最近一次 AI 结论（便于列表展示）")
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    final_conclusion: Mapped[str | None] = mapped_column(String(16), comment="最终结论 APPROVED/REJECTED")
    reject_reason_code: Mapped[str | None] = mapped_column(String(32))
    final_comment: Mapped[str | None] = mapped_column(Text, comment="终审意见（对上报人可见）")

    # 流转字段
    assigned_reviewer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    claimed_at: Mapped[str | None] = mapped_column(String(32))
    second_reviewer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    client_uuid: Mapped[str | None] = mapped_column(String(64), unique=True, comment="客户端幂等键")
    submitted_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(32))
    archived_at: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, onupdate=utc_now_iso, nullable=False)

    behavior_type = relationship("BehaviorType", lazy="joined")
    images = relationship(
        "ReportImage",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportImage.sort_order",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_report_status", "status"),
        Index("idx_report_user", "user_id", "submitted_at"),
        Index("idx_report_behavior", "behavior_type_id"),
        Index("idx_report_geo", "latitude", "longitude"),
    )
