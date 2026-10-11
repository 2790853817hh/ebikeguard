"""存储后端工厂。"""
from __future__ import annotations

from functools import lru_cache

from app.adapters.storage.base import StorageBackend
from app.adapters.storage.local_disk import LocalDiskStorage
from app.config import settings


@lru_cache
def get_storage() -> StorageBackend:
    backend = (settings.storage_backend or "local").lower()
    if backend == "oss":
        from app.adapters.storage.aliyun_oss import AliyunOSSStorage

        return AliyunOSSStorage()
    return LocalDiskStorage()
