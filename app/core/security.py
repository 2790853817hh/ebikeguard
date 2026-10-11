"""密码哈希与 JWT 签发/校验。

密码哈希使用标准库 PBKDF2-HMAC-SHA256（不引入 bcrypt 编译依赖，跨平台稳定）；
如需切换 argon2/bcrypt，只需替换 hash_password / verify_password 两个函数。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import settings

_PBKDF2_ROUNDS = 260_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"{_ALGO}${_PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, rounds, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def _encode(payload: dict[str, Any], expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, role: str, token_version: int = 0) -> str:
    return _encode(
        {"sub": str(user_id), "role": role, "ver": token_version, "typ": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: int, token_version: int = 0) -> str:
    return _encode(
        {"sub": str(user_id), "ver": token_version, "typ": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    """解码 token；失败抛 jwt.PyJWTError。"""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
