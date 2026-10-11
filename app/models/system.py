"""系统配置 / 短信验证码 / 申诉表。"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.utils import utc_now_iso
from app.database import Base


class SystemConfig(Base):
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cfg_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    cfg_value: Mapped[str] = mapped_column(Text, nullable=False)
    cfg_group: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    updated_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, onupdate=utc_now_iso, nullable=False)


class SmsCode(Base):
    """短信验证码（本期 Mock 通道同样落库，便于多进程/重启后仍可用）。"""

    __tablename__ = "sms_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    scene: Mapped[str] = mapped_column(String(16), default="login", nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expire_at: Mapped[int] = mapped_column(Integer, nullable=False, comment="Unix 时间戳")
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)

    __table_args__ = (Index("idx_sms_phone_scene", "phone", "scene", "created_at"),)


class Appeal(Base):
    __tablename__ = "appeal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("report.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", comment="PENDING/ACCEPTED/REJECTED")
    handler_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    handle_comment: Mapped[str | None] = mapped_column(Text)
    from_conclusion: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)
    handled_at: Mapped[str | None] = mapped_column(String(32))
