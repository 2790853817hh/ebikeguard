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
    username: str | None = None
    real_name: str | None = None
    role: str
    org_code: str | None = None
    status: str
    created_at: str
    last_login_at: str | None = None


TokenOut.model_rebuild()
