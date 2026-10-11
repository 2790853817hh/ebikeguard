"""系统配置服务：读取/写入 system_config，带进程内缓存（写后失效）。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database import SessionLocal
from app.models import SystemConfig

logger = get_logger(__name__)

_cache: dict[str, str] = {}


def _load(key: str) -> str | None:
    if key in _cache:
        return _cache[key]
    with SessionLocal() as db:
        row = db.query(SystemConfig).filter(SystemConfig.cfg_key == key).first()
        if row:
            _cache[key] = row.cfg_value
            return row.cfg_value
    return None


def get_config(key: str, default: str | None = None) -> str | None:
    value = _load(key)
    return default if value is None else value


def get_config_float(key: str, default: float) -> float:
    raw = _load(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("配置 %s 的值 %r 无法解析为浮点数，使用默认值 %s", key, raw, default)
        return default


def get_config_int(key: str, default: int) -> int:
    raw = _load(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_config_bool(key: str, default: bool = False) -> bool:
    raw = _load(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def set_config(db: Session, key: str, value: str, *, updated_by: int | None = None) -> SystemConfig:
    row = db.query(SystemConfig).filter(SystemConfig.cfg_key == key).first()
    if row is None:
        from app.core.constants import DEFAULT_SYSTEM_CONFIG

        group = DEFAULT_SYSTEM_CONFIG.get(key, (None, "custom", None))[1]
        desc = DEFAULT_SYSTEM_CONFIG.get(key, (None, None, None))[2]
        row = SystemConfig(cfg_key=key, cfg_value=value, cfg_group=group, description=desc, updated_by=updated_by)
        db.add(row)
    else:
        row.cfg_value = value
        row.updated_by = updated_by
    db.commit()
    _cache[key] = value
    return row


def invalidate_cache() -> None:
    _cache.clear()


def list_configs(db: Session, group: str | None = None) -> list[SystemConfig]:
    q = db.query(SystemConfig)
    if group:
        q = q.filter(SystemConfig.cfg_group == group)
    return q.order_by(SystemConfig.cfg_group, SystemConfig.cfg_key).all()


def public_config() -> dict:
    """前端需要的公开配置（不含敏感项）。"""
    return {
        "max_images": get_config_int("upload.max_images", 9),
        "privacy_notice": get_config("privacy.notice", ""),
        "appeal_window_days": get_config_int("appeal.window_days", 7),
        "supplement_sla_hours": get_config_int("review.supplement_sla_hours", 72),
    }
