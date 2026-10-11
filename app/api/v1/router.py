"""API v1 路由聚合。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin, auth, behavior_types, images, notifications, reports, review

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(behavior_types.router)
api_router.include_router(reports.router)
api_router.include_router(images.router)
api_router.include_router(review.router)
api_router.include_router(notifications.router)
api_router.include_router(admin.router)
