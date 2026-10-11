"""通用响应契约。"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class OkOut(BaseModel):
    ok: bool = True
    message: str | None = None


class ErrorOut(BaseModel):
    code: str
    message: str
    detail: dict | None = None
