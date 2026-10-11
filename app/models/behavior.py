"""行为类型字典表。"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utc_now_iso
from app.database import Base


class BehaviorType(Base):
    __tablename__ = "behavior_type"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    risk_weight: Mapped[float] = mapped_column(Float, default=0.5, nullable=False, comment="风险权重 0~1，供规则引擎使用")
    night_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="夜间高发，夜间拍摄风险加权")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, onupdate=utc_now_iso, nullable=False)
