"""图片存储抽象接口。

业务层只通过 storage_key（相对键）读写，永不接触真实磁盘路径或 OSS 地址，
因此本地磁盘 ↔ 阿里云 OSS 可以零改业务代码切换。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    name: str

    def save(self, key: str, data: bytes) -> str:
        """写入文件，返回 storage_key。"""
        ...

    def read(self, key: str) -> bytes:
        """按 storage_key 读取字节。"""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def local_path(self, key: str) -> str | None:
        """若为本地存储，返回真实路径（用于 Nginx X-Accel-Redirect）；否则返回 None。"""
        ...
