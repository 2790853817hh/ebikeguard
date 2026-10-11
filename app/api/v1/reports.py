"""上报记录路由：拍照上传、我的上报、详情、撤回、补充材料、申诉。"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import ClientInfo, get_client, get_current_user
from app.database import get_db
from app.models import AppUser
from app.schemas.common import Page
from app.schemas.report import (
    ReportDetailOut,
    ReportListItem,
    ReverseGeoIn,
    ReverseGeoOut,
    SupplementIn,
)
from app.schemas.review import AppealIn
from app.services import audit_orchestrator, report_service

router = APIRouter(tags=["上报"])


@router.post("/reports", summary="提交上报（multipart：图片 + 表单信息）", status_code=201)
async def create_report(
    background: BackgroundTasks,
    images: list[UploadFile] = File(..., description="现场照片，1~9 张"),
    behavior_type_id: int | None = Form(None),
    behavior_type_code: str | None = Form(None),
    address_text: str = Form(..., description="行为发生地址"),
    longitude: float | None = Form(None),
    latitude: float | None = Form(None),
    location_source: str | None = Form(None, description="geolocation/exif/manual/map"),
    shoot_time: str | None = Form(None, description="拍摄时间 ISO8601"),
    remark: str | None = Form(None, description="补充说明"),
    client_uuid: str | None = Form(None, description="客户端幂等键"),
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> dict:
    files: list[tuple[str | None, bytes]] = []
    for item in images:
        data = await item.read()
        files.append((item.filename, data))

    report, created = report_service.create_report(
        db,
        user,
        behavior_type_id=behavior_type_id,
        behavior_type_code=behavior_type_code,
        address_text=address_text,
        longitude=longitude,
        latitude=latitude,
        location_source=location_source,
        shoot_time=shoot_time,
        remark=remark,
        files=files,
        client_uuid=client_uuid,
        ip=client.ip,
        user_agent=client.user_agent,
    )
    if created:
        # AI 初审异步执行（失败也不影响上报落库；管理端可重跑）
        background.add_task(audit_orchestrator.run_ai_audit, report.id)

    return {
        "id": report.id,
        "report_no": report.report_no,
        "status": report.status,
        "submitted_at": report.submitted_at,
        "created": created,
    }


@router.get("/reports", response_model=Page[ReportListItem], summary="我的上报 / 上报列表")
def list_reports(
    status: str | None = Query(None, description="按状态过滤，可用英文逗号分隔多个"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[ReportListItem]:
    rows, total = report_service.list_reports(db, user, status=status, page=page, page_size=page_size)
    return Page[ReportListItem](
        items=[ReportListItem(**report_service.to_list_item(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/reports/{report_id}", response_model=ReportDetailOut, summary="上报详情")
def get_report(
    report_id: int,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDetailOut:
    report = report_service.get_report_or_404(db, report_id)
    report_service.assert_can_view(report, user)
    return ReportDetailOut(**report_service.to_detail(db, report))


@router.get("/reports/{report_id}/ai-result", summary="查看 AI 审核结果")
def get_ai_result(
    report_id: int,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    report = report_service.get_report_or_404(db, report_id)
    report_service.assert_can_view(report, user)
    return report_service.ai_result_to_dict(report_service.latest_ai_result(db, report_id)) or {}


@router.post("/reports/{report_id}/withdraw", summary="撤回上报")
def withdraw(
    report_id: int,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> dict:
    report = report_service.get_report_or_404(db, report_id)
    report = report_service.withdraw(db, report, user, ip=client.ip, ua=client.user_agent)
    return {"ok": True, "status": report.status}


@router.post("/reports/{report_id}/supplement", summary="补充材料（退回补充后）")
async def supplement(
    report_id: int,
    background: BackgroundTasks,
    images: list[UploadFile] | None = File(None),
    remark: str | None = Form(None),
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> dict:
    report = report_service.get_report_or_404(db, report_id)
    files: list[tuple[str | None, bytes]] = []
    for item in images or []:
        files.append((item.filename, await item.read()))
    report = report_service.supplement(
        db, report, user, remark=remark, files=files, ip=client.ip, ua=client.user_agent
    )
    background.add_task(audit_orchestrator.run_ai_audit, report.id)
    return {"ok": True, "status": report.status}


@router.post("/reports/{report_id}/appeal", summary="发起申诉")
def appeal(
    report_id: int,
    payload: AppealIn,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> dict:
    report = report_service.get_report_or_404(db, report_id)
    row = report_service.appeal(
        db, report, user, reason=payload.reason, ip=client.ip, ua=client.user_agent
    )
    return {"ok": True, "appeal_id": row.id, "status": row.status}


@router.post("/geo/reverse", response_model=ReverseGeoOut, summary="经纬度逆地理编码（后端代理）")
def reverse_geo(payload: ReverseGeoIn, _: AppUser = Depends(get_current_user)) -> ReverseGeoOut:
    from app.adapters.geocoding.factory import get_geocoding_provider

    data = get_geocoding_provider().reverse(payload.longitude, payload.latitude)
    return ReverseGeoOut(**data)
