"""阿里云 OSS 存储实现（预留骨架）。

启用方式：
    pip install oss2
    .env 中设置 STORAGE_BACKEND=oss、OSS_ENDPOINT/OSS_BUCKET/OSS_ACCESS_KEY_ID/OSS_ACCESS_KEY_SECRET

安全要求：Bucket 必须为**私有**（ACL=private），禁止公共读；
前端访问走签名 URL（`sign_url`）或后端代理，不可直出公网地址。
"""
from __future__ import annotations

from app.adapters.storage.base import StorageBackend
from app.config import settings
from app.core.exceptions import AppError, NotFound


class AliyunOSSStorage(StorageBackend):
    name = "oss"

    def __init__(self) -> None:
        if not (settings.oss_endpoint and settings.oss_bucket and settings.oss_access_key_id):
            raise AppError("OSS 未配置完整，请检查 OSS_ENDPOINT / OSS_BUCKET / OSS_ACCESS_KEY_ID")
        try:
            import oss2  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AppError("未安装 oss2，请执行 pip install oss2 后再启用 OSS 存储") from exc

        auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
        self.bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)
        self._oss2 = oss2

    def save(self, key: str, data: bytes) -> str:
        self.bucket.put_object(key, data)
        return key

    def read(self, key: str) -> bytes:
        try:
            return self.bucket.get_object(key).read()
        except Exception as exc:  # noqa: BLE001
            raise NotFound("图片不存在") from exc

    def exists(self, key: str) -> bool:
        return bool(self.bucket.object_exists(key))

    def delete(self, key: str) -> None:
        self.bucket.delete_object(key)

    def local_path(self, key: str) -> str | None:
        return None  # OSS 场景由后端代理读取或返回签名 URL

    def sign_url(self, key: str, expires_seconds: int = 600) -> str:
        return self.bucket.sign_url("GET", key, expires_seconds)
