"""统一异常与错误码。"""
from __future__ import annotations

from fastapi import HTTPException, status


class AppError(HTTPException):
    """业务异常基类。"""

    code = "APP_ERROR"
    http_status = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None, detail: dict | None = None):
        payload = {"code": code or self.code, "message": message}
        if detail:
            payload["detail"] = detail
        super().__init__(status_code=http_status or self.http_status, detail=payload)


class BadRequest(AppError):
    code = "BAD_REQUEST"
    http_status = status.HTTP_400_BAD_REQUEST


class Unauthorized(AppError):
    code = "UNAUTHORIZED"
    http_status = status.HTTP_401_UNAUTHORIZED


class Forbidden(AppError):
    code = "FORBIDDEN"
    http_status = status.HTTP_403_FORBIDDEN


class NotFound(AppError):
    code = "NOT_FOUND"
    http_status = status.HTTP_404_NOT_FOUND


class Conflict(AppError):
    code = "CONFLICT"
    http_status = status.HTTP_409_CONFLICT


class PayloadTooLarge(AppError):
    code = "PAYLOAD_TOO_LARGE"
    http_status = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


class UnprocessableEntity(AppError):
    code = "UNPROCESSABLE_ENTITY"
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY


class IllegalTransition(AppError):
    """非法状态流转。"""

    code = "ILLEGAL_TRANSITION"
    http_status = status.HTTP_409_CONFLICT
