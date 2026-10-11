"""上报图片表。"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utc_now_iso
from app.database import Base


class ReportImage(Base):
    __tablename__ = "report_image"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="image_uuid，对外唯一标识")
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("report.id", ondelete="CASCADE"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, comment="相对存储键，不暴露磁盘路径")
    thumb_key: Mapped[str | None] = mapped_column(String(255))
    original_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    taken_at: Mapped[str | None] = mapped_column(String(32), comment="EXIF 拍摄时间")
    exif_json: Mapped[str | None] = mapped_column(Text)
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lng: Mapped[float | None] = mapped_column(Float)
    phash: Mapped[str | None] = mapped_column(String(64), comment="感知哈希，用于相似图 / 重复上报检测")
    phash_bucket: Mapped[int | None] = mapped_column(Integer, comment="pHash 前 16 bit，用于加速相似检索")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utc_now_iso, nullable=False)

    report = relationship("Report", back_populates="images")

    __table_args__ = (
        Index("idx_image_report", "report_id"),
        Index("idx_image_sha", "sha256"),
        Index("idx_image_phash", "phash_bucket"),
    )
