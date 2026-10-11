"""FastAPI 依赖：鉴权、角色校验、客户端信息。"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import Forbidden, Unauthorized
from app.database import get_db
from app.models import AppUser
from app.services import auth_service

bearer_scheme = HTTPBearer(auto_error=False, description="Bearer <access_token>")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AppUser:
    if not credentials or not credentials.credentials:
        raise Unauthorized("请先登录")
    return auth_service.load_user_from_token(db, credentials.credentials)


def require_roles(*roles: str) -> Callable[[AppUser], AppUser]:
    allowed = set(roles)

    def _checker(user: AppUser = Depends(get_current_user)) -> AppUser:
        if user.role not in allowed:
            raise Forbidden("当前账号无权访问该功能")
        return user

    return _checker


require_reviewer = require_roles("reviewer", "admin")
require_admin = require_roles("admin")


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def client_ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


class ClientInfo:
    """把 IP / UA 打包进依赖，供路由直接使用。"""

    def __init__(self, request: Request) -> None:
        self.ip = client_ip(request)
        self.user_agent = client_ua(request)


def get_client(request: Request) -> ClientInfo:
    return ClientInfo(request)
