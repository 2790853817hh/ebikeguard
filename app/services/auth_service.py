"""认证服务：短信验证码登录（上报人）、账号密码登录（审核/管理）、JWT 签发与吊销。"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import jwt
from sqlalchemy.orm import Session

from app.adapters.sms.factory import get_sms_provider
from app.config import settings
from app.core.constants import Role, UserStatus
from app.core.exceptions import BadRequest, Forbidden, Unauthorized
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.utils import gen_numeric_code, utc_now_iso
from app.models import AppUser, SmsCode

logger = get_logger(__name__)


def issue_tokens(user: AppUser) -> dict:
    access = create_access_token(user.id, user.role, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
        "user": user,
    }


# ---------------------------------------------------------------- 短信验证码


def send_sms_code(db: Session, phone: str) -> dict:
    now_ts = int(time.time())
    latest = (
        db.query(SmsCode)
        .filter(SmsCode.phone == phone, SmsCode.scene == "login")
        .order_by(SmsCode.id.desc())
        .first()
    )
    if latest:
        try:
            created = datetime.strptime(latest.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
            if elapsed < settings.sms_send_interval_seconds:
                raise BadRequest(f"验证码发送过于频繁，请 {int(settings.sms_send_interval_seconds - elapsed)} 秒后再试")
        except ValueError:
            pass

    code = settings.sms_mock_code if settings.sms_provider == "mock" else gen_numeric_code(6)
    db.add(
        SmsCode(
            phone=phone,
            code=code,
            scene="login",
            expire_at=now_ts + settings.sms_code_ttl_seconds,
            created_at=utc_now_iso(),
        )
    )
    db.commit()

    ok, message, echo = get_sms_provider().send_code(phone, code)
    if not ok:
        raise BadRequest(message)
    return {
        "ok": True,
        "message": message,
        "ttl_seconds": settings.sms_code_ttl_seconds,
        "dev_code": echo,  # 仅 DEBUG 环境返回，便于联调
    }


def _verify_sms_code(db: Session, phone: str, code: str) -> None:
    row = (
        db.query(SmsCode)
        .filter(SmsCode.phone == phone, SmsCode.scene == "login", SmsCode.used == 0)
        .order_by(SmsCode.id.desc())
        .first()
    )
    if not row:
        raise BadRequest("请先获取验证码")
    if row.expire_at < int(time.time()):
        raise BadRequest("验证码已过期，请重新获取")
    if row.code != (code or "").strip():
        raise BadRequest("验证码不正确")
    row.used = 1


def get_or_create_reporter(db: Session, phone: str) -> AppUser:
    user = db.query(AppUser).filter(AppUser.phone == phone).first()
    if user:
        if user.status != UserStatus.ACTIVE:
            raise Forbidden("账号已被停用，请联系管理员")
        return user
    user = AppUser(
        phone=phone,
        role=Role.REPORTER,
        status=UserStatus.ACTIVE,
        real_name=None,
    )
    db.add(user)
    db.flush()
    logger.info("新上报人账号已创建：%s", phone)
    return user


def login_by_sms(db: Session, phone: str, code: str) -> AppUser:
    _verify_sms_code(db, phone, code)
    user = get_or_create_reporter(db, phone)
    user.last_login_at = utc_now_iso()
    db.commit()
    db.refresh(user)
    return user


def login_by_password(db: Session, username: str, password: str) -> AppUser:
    user = db.query(AppUser).filter(AppUser.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise Unauthorized("账号或密码不正确")
    if user.status == UserStatus.DISABLED:
        raise Forbidden("账号已被停用，请联系管理员")
    if user.status == UserStatus.LOCKED:
        raise Forbidden("账号已锁定，请联系管理员")
    user.last_login_at = utc_now_iso()
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------- Token


def load_user_from_token(db: Session, token: str) -> AppUser:
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("登录凭证无效") from exc

    if payload.get("typ") != "access":
        raise Unauthorized("登录凭证类型不正确")

    user = db.get(AppUser, int(payload.get("sub", 0)))
    if not user:
        raise Unauthorized("用户不存在")
    if user.status != UserStatus.ACTIVE:
        raise Forbidden("账号已被停用")
    if int(payload.get("ver", 0)) != user.token_version:
        raise Unauthorized("登录凭证已失效，请重新登录")
    return user


def refresh_access_token(db: Session, refresh_token: str) -> AppUser:
    try:
        payload = decode_token(refresh_token)
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("刷新凭证无效") from exc

    if payload.get("typ") != "refresh":
        raise Unauthorized("刷新凭证类型不正确")

    user = db.get(AppUser, int(payload.get("sub", 0)))
    if not user or user.status != UserStatus.ACTIVE:
        raise Unauthorized("用户不存在或已停用")
    if int(payload.get("ver", 0)) != user.token_version:
        raise Unauthorized("登录凭证已失效，请重新登录")
    return user


def logout(db: Session, user: AppUser) -> None:
    """登出：递增 token_version，使所有已签发 token 立即失效。"""
    user.token_version += 1
    db.commit()


def set_password(db: Session, user: AppUser, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    db.commit()
