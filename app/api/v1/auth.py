"""认证与用户路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_client, get_current_user
from app.core.deps import ClientInfo
from app.database import get_db
from app.models import AppUser
from app.schemas.auth import LoginIn, RefreshIn, SmsLoginIn, SmsSendIn, TokenOut, UserOut
from app.schemas.common import OkOut
from app.services import auth_service

router = APIRouter(tags=["认证"])


def _token_out(user: AppUser) -> dict:
    data = auth_service.issue_tokens(user)
    data["user"] = UserOut.model_validate(user)
    return data


@router.post("/auth/sms/send", summary="发送短信验证码（上报人登录）")
def send_sms_code(payload: SmsSendIn, db: Session = Depends(get_db)) -> dict:
    return auth_service.send_sms_code(db, payload.phone)


@router.post("/auth/sms/login", response_model=TokenOut, summary="手机号+验证码登录")
def sms_login(payload: SmsLoginIn, db: Session = Depends(get_db)) -> dict:
    user = auth_service.login_by_sms(db, payload.phone, payload.code)
    return _token_out(user)


@router.post("/auth/login", response_model=TokenOut, summary="账号密码登录（审核员/管理员）")
def password_login(payload: LoginIn, db: Session = Depends(get_db)) -> dict:
    user = auth_service.login_by_password(db, payload.username, payload.password)
    return _token_out(user)


@router.post("/auth/refresh", response_model=TokenOut, summary="刷新登录凭证")
def refresh(payload: RefreshIn, db: Session = Depends(get_db)) -> dict:
    user = auth_service.refresh_access_token(db, payload.refresh_token)
    return _token_out(user)


@router.post("/auth/logout", response_model=OkOut, summary="退出登录")
def logout(user: AppUser = Depends(get_current_user), db: Session = Depends(get_db)) -> OkOut:
    auth_service.logout(db, user)
    return OkOut(message="已退出登录")


@router.get("/users/me", response_model=UserOut, summary="当前登录用户")
def me(user: AppUser = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/users/me/client", summary="客户端信息（调试用）")
def me_client(client: ClientInfo = Depends(get_client)) -> dict:
    return {"ip": client.ip, "user_agent": client.user_agent}
