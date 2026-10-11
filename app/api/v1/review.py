"""人工审核路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import ClientInfo, get_client, require_reviewer
from app.database import get_db
from app.models import AppUser
from app.schemas.common import OkOut, Page
from app.schemas.review import ClaimOut, DecisionIn, ReviewDetailOut, ReviewQueueItem
from app.services import report_service, review_service

router = APIRouter(tags=["人工审核"], dependencies=[Depends(require_reviewer)])


@router.get("/review/queue", response_model=Page[ReviewQueueItem], summary="待审队列")
def queue(
    status: str | None = Query(None, description="筛选状态，逗号分隔"),
    behavior_type_id: int | None = None,
    ai_conclusion: str | None = None,
    keyword: str | None = None,
    assigned_to_me: bool = False,
    order: str = Query("oldest", description="oldest/newest/ai_confidence"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> Page[ReviewQueueItem]:
    statuses = [s.strip() for s in status.split(",")] if status else None
    rows, total = review_service.review_queue(
        db,
        statuses=statuses,
        behavior_type_id=behavior_type_id,
        ai_conclusion=ai_conclusion,
        keyword=keyword,
        assigned_to_me=user.id if assigned_to_me else None,
        page=page,
        page_size=page_size,
        order=order,
    )
    return Page[ReviewQueueItem](
        items=[ReviewQueueItem(**review_service.to_queue_item(db, r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/review/{report_id}", response_model=ReviewDetailOut, summary="审核详情（含 AI 建议）")
def detail(
    report_id: int,
    user: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> ReviewDetailOut:
    report = report_service.get_report_or_404(db, report_id)
    return ReviewDetailOut(**review_service.review_detail(db, report, user))


@router.post("/review/{report_id}/claim", response_model=ClaimOut, summary="认领任务")
def claim(
    report_id: int,
    user: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> ClaimOut:
    report = report_service.get_report_or_404(db, report_id)
    return ClaimOut(**review_service.claim(db, report, user, ip=client.ip, ua=client.user_agent))


@router.post("/review/{report_id}/decision", summary="提交审核结论")
def decide(
    report_id: int,
    payload: DecisionIn,
    user: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> dict:
    try:
        payload.validate_semantics()
    except ValueError as exc:
        from app.core.exceptions import BadRequest

        raise BadRequest(str(exc)) from exc

    report = report_service.get_report_or_404(db, report_id)
    report = review_service.decide(
        db,
        report,
        user,
        decision=str(payload.decision),
        reason_code=str(payload.reason_code) if payload.reason_code else None,
        comment=payload.comment,
        ip=client.ip,
        ua=client.user_agent,
    )
    return {"ok": True, "status": report.status, "final_conclusion": report.final_conclusion}


@router.get("/review/{report_id}/history", summary="审核历史")
def history(
    report_id: int,
    _: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
) -> dict:
    report = report_service.get_report_or_404(db, report_id)
    return {
        "report_no": report.report_no,
        "status": report.status,
        "timeline": report_service.build_timeline(db, report),
        "human_records": review_service.review_history(db, report_id),
    }


@router.post("/review/{report_id}/assign", response_model=OkOut, summary="指派审核员（管理员）")
def assign(
    report_id: int,
    reviewer_id: int = Query(..., description="审核员用户 ID"),
    user: AppUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
    client: ClientInfo = Depends(get_client),
) -> OkOut:
    report = report_service.get_report_or_404(db, report_id)
    review_service.assign(db, report, reviewer_id, user, ip=client.ip, ua=client.user_agent)
    return OkOut(message="指派成功")
