"""图片鉴权访问路由。

访问控制采用「双重校验」：
1. **短时签名**（e/s 查询参数）——由后端在已鉴权的响应中签发，默认 10 分钟有效，
   即使链接被转发，过期即失效；
2. **登录态 + 数据级权限**——若请求带 Bearer 头，则进一步校验
   （上报人仅能看自己的图片，审核员/管理员可看全部）。

因此 `<img src="...">` 可直接使用签名 URL 加载；而直接猜测或抓取磁盘路径一律不可达。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.exceptions import Forbidden, NotFound
from app.database import get_db
from app.models import AppUser, Report, ReportImage
from app.services import auth_service, image_service

router = APIRouter(tags=["图片"])

_CACHE_HEADER = {"Cache-Control": "private, max-age=300"}


def _resolve_user(request: Request, db: Session) -> AppUser | None:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    try:
        return auth_service.load_user_from_token(db, header.split(" ", 1)[1].strip())
    except Exception:  # noqa: BLE001 - 无有效登录态时回退到签名校验
        return None


def _load_image(db: Session, image_id: str) -> ReportImage:
    image = db.get(ReportImage, image_id)
    if not image:
        raise NotFound("图片不存在")
    return image


def _assert_permission(db: Session, image: ReportImage, user: AppUser) -> None:
    if user.role in ("reviewer", "admin"):
        return
    report = db.get(Report, image.report_id)
    if not report or report.user_id != user.id:
        raise Forbidden("无权访问该图片")


def _serve(db: Session, image_id: str, thumb: bool, e: int | None, s: str | None, request: Request) -> Response:
    image = _load_image(db, image_id)
    user = _resolve_user(request, db)
    if user is not None:
        # 已登录：走数据级权限校验（无需签名）
        _assert_permission(db, image, user)
    else:
        # 未登录：必须携带有效的短时签名（供 <img> 标签直接加载）
        if not (e and s):
            raise Forbidden("请先登录后访问图片")
        image_service.assert_image_token(image_id, thumb, e, s)
    key = (image.thumb_key or image.storage_key) if thumb else image.storage_key
    data = image_service.read_image_bytes(key)
    return Response(content=data, media_type="image/jpeg", headers=_CACHE_HEADER)


@router.get("/images/{image_id}", summary="获取原图（需登录或短时签名）")
def get_image(
    image_id: str,
    request: Request,
    e: int | None = Query(None, description="签名过期时间戳"),
    s: str | None = Query(None, description="签名"),
    db: Session = Depends(get_db),
) -> Response:
    return _serve(db, image_id, False, e, s, request)


@router.get("/images/{image_id}/thumb", summary="获取缩略图（需登录或短时签名）")
def get_thumb(
    image_id: str,
    request: Request,
    e: int | None = Query(None),
    s: str | None = Query(None),
    db: Session = Depends(get_db),
) -> Response:
    return _serve(db, image_id, True, e, s, request)
