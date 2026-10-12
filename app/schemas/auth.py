"""认证与用户契约。"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class SmsSendIn(BaseModel):
    phone: str = Field(..., description="手机号")

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str) -> str:
        v = (v or "").strip()
        if not (v.isdigit() and len(v) == 11 and v.startswith("1")):
            raise ValueError("请输入有效的 11 位手机号")
        return v


class SmsLoginIn(SmsSendIn):
    code: str = Field(..., min_length=4, max_length=8)


class LoginIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(ORMModel):
    id: int
    phone: str | None = None
    email: str | None = None
    username: str | None = None
    real_name: str | None = None
    role: str
    org_code: str | None = None
    status: str
    created_at: str
    last_login_at: str | None = None


class EmailUpdateIn(BaseModel):
    """修改接收通知的邮箱（空字符串 = 清除）。"""

    email: str = Field("", max_length=128)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        v = (v or "").strip()
        if v and ("@" not in v or "." not in v.rsplit("@", 1)[-1] or len(v) < 6):
            raise ValueError("请输入有效的邮箱地址")
        return v


TokenOut.model_rebuild()
