"""用户表。"""
from __future__ import annotations

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import Role, UserStatus
from app.core.utils import utc_now_iso
from app.database import Base


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    real_name: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default=Role.REPORTER, nullable=False)
    org_code: Mapped[str | None] = mapped_column(String(64), comment="所属机构 / 辖区编码")
    status: Mapped[str] = mapped_column(String(16), default=UserStatus.ACTIVE, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="登出/改密后自增，用于吊销历史 token")
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, onupdate=utc_now_iso, nullable=False)
    last_login_at: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("idx_user_role", "role"),
        Index("idx_user_org", "org_code"),
    )
