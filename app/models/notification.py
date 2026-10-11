"""通知表。"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import NotificationChannel
from app.core.utils import utc_now_iso
from app.database import Base


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False)
    report_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("report.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default=NotificationChannel.INAPP, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_status: Mapped[str] = mapped_column(String(16), default="sent", comment="pending/sent/failed")
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)
    read_at: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (Index("idx_notify_user", "user_id", "is_read", "created_at"),)
