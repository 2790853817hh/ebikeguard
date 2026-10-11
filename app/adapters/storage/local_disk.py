"""本地磁盘存储实现。

目录结构（不对外暴露）：
    data/images/origin/2026/10/10/{report_id}/{image_uuid}.jpg
    data/images/thumb/ 2026/10/10/{report_id}/{image_uuid}.jpg
"""
from __future__ import annotations

from pathlib import Path

from app.adapters.storage.base import StorageBackend
from app.config import settings
from app.core.exceptions import NotFound


class LocalDiskStorage(StorageBackend):
    name = "local"

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or settings.images_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 内部 ----
    def _abs(self, key: str) -> Path:
        # 防止路径穿越
        safe = key.replace("\\", "/").lstrip("/")
        if ".." in safe.split("/"):
            raise NotFound("非法的存储键")
        return self.root / safe

    # ---- 接口 ----
    def save(self, key: str, data: bytes) -> str:
        path = self._abs(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return key

    def read(self, key: str) -> bytes:
        path = self._abs(key)
        if not path.exists():
            raise NotFound("图片不存在")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._abs(key).exists()

    def delete(self, key: str) -> None:
        path = self._abs(key)
        if path.exists():
            path.unlink()

    def local_path(self, key: str) -> str | None:
        return str(self._abs(key))
