"""AI 审核编排器：组装审计请求 -> 调用 Provider -> 落库 -> 按策略分流状态。

这是「AI 审核」与「人工审核」的衔接点，职责边界清晰：
- 本模块只做编排，不做判定（判定在 VisionAuditProvider 内）；
- 状态流转一律通过 state_machine，禁止直接改 report.status。
"""
from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adapters.ai.base import AuditImage, AuditReason, AuditRequest, GeoPoint, AuditResult
from app.adapters.ai.factory import get_ai_provider
from app.core.constants import ActorType, AiConclusion, AuditAction, ReportStatus
from app.core.logging import get_logger
from app.core.utils import hamming_distance, utc_now_iso
from app.database import SessionLocal
from app.models import AiAuditResult, Report, ReportImage
from app.services import config_service, notification_service, state_machine

logger = get_logger(__name__)


def _duplicate_history(db: Session, report: Report) -> int:
    """检测本记录与历史上报的重复/相似图片数量（排除自身）。"""
    if not report.images:
        return 0
    shas = [i.sha256 for i in report.images]
    exact_reports = (
        db.query(func.count(func.distinct(ReportImage.report_id)))
        .filter(ReportImage.sha256.in_(shas), ReportImage.report_id != report.id)
        .scalar()
        or 0
    )
    buckets = {i.phash_bucket for i in report.images if i.phash_bucket is not None}
    similar_reports = 0
    if buckets:
        candidates = (
            db.query(ReportImage.report_id, ReportImage.phash)
            .filter(ReportImage.phash_bucket.in_(buckets), ReportImage.report_id != report.id)
            .all()
        )
        seen: set[int] = set()
        for rid, phash in candidates:
            if rid in seen or not phash:
                continue
            if any(hamming_distance(phash, img.phash) <= 6 for img in report.images if img.phash):
                seen.add(rid)
        similar_reports = len(seen)
    return int(exact_reports) + int(similar_reports)


def build_audit_request(report: Report, duplicate_history: int = 0) -> AuditRequest:
    behavior = report.behavior_type
    location = None
    if report.longitude is not None and report.latitude is not None:
        location = GeoPoint(longitude=report.longitude, latitude=report.latitude)
    return AuditRequest(
        report_id=report.id,
        behavior_type_code=behavior.code if behavior else "",
        behavior_type_name=behavior.name if behavior else "",
        behavior_risk_weight=behavior.risk_weight if behavior else 0.5,
        behavior_night_sensitive=bool(behavior.night_sensitive) if behavior else False,
        remark=report.remark,
        address_text=report.address_text,
        location=location,
        location_conflict=report.location_conflict,
        shoot_time=report.shoot_time,
        images=[
            AuditImage(
                image_id=i.id,
                storage_key=i.storage_key,
                sha256=i.sha256,
                width=i.width,
                height=i.height,
                taken_at=i.taken_at,
                gps_lat=i.gps_lat,
                gps_lng=i.gps_lng,
                phash=i.phash,
            )
            for i in report.images
        ],
        duplicate_history=duplicate_history,
    )


def _persist_result(db: Session, report: Report, result: AuditResult, routed_state: str, auto_closed: bool) -> AiAuditResult:
    row = AiAuditResult(
        report_id=report.id,
        conclusion=str(result.conclusion),
        confidence=result.confidence,
        reasons_json=json.dumps([r.model_dump() for r in result.reasons], ensure_ascii=False),
        model_name=result.model_name,
        model_version=result.model_version,
        raw_json=json.dumps(result.raw, ensure_ascii=False, default=str) if result.raw else None,
        latency_ms=result.latency_ms,
        routed_state=str(routed_state),
        is_auto_closed=auto_closed,
    )
    db.add(row)
    report.ai_conclusion = str(result.conclusion)
    report.ai_confidence = result.confidence
    db.flush()
    return row


def _decide_routing(result: AuditResult) -> tuple[str, bool]:
    """保守档分流策略。

    - 仅当配置开启「自动放行」且结论为 NORMAL 且置信度 >= 高置信阈值时，才自动结案；
    - 其余一切情况（含所有 VIOLATION）一律进入人工审核队列。
    """
    auto_close_enabled = config_service.get_config_bool("ai.auto_close_normal_enabled", False)
    high = config_service.get_config_float("ai.high_confidence_threshold", 0.90)
    if auto_close_enabled and result.conclusion == AiConclusion.NORMAL and result.confidence >= high:
        return ReportStatus.REJECTED, True
    return ReportStatus.AI_DONE_PENDING_HUMAN, False


def run_ai_audit(report_id: int, *, force: bool = False) -> None:
    """执行一次 AI 审核（同步；由 BackgroundTasks 或管理端重跑调用）。"""
    with SessionLocal() as db:
        report = db.get(Report, report_id)
        if not report:
            logger.warning("AI 审核跳过：上报 %s 不存在", report_id)
            return

        if report.status == ReportStatus.PENDING_AI:
            pass
        elif force and report.status in (
            ReportStatus.AI_DONE_PENDING_HUMAN,
            ReportStatus.HUMAN_REVIEWING,
            ReportStatus.ESCALATED,
        ):
            pass
        else:
            logger.info("AI 审核跳过：上报 %s 当前状态为 %s", report_id, report.status)
            return

        dup = _duplicate_history(db, report)
        req = build_audit_request(report, duplicate_history=dup)

        provider_name = "unknown"
        try:
            provider = get_ai_provider()
            provider_name = getattr(provider, "name", "unknown")
            result = provider.audit(req)
        except Exception as exc:  # noqa: BLE001
            logger.exception("AI 审核执行失败，转人工处理：%s", exc)
            result = AuditResult(
                conclusion=AiConclusion.UNCERTAIN,
                confidence=0.0,
                reasons=[
                    AuditReason(
                        code="AI_ERROR",
                        text=f"自动初审执行异常（{type(exc).__name__}），已直接转人工审核",
                        weight=0.0,
                    )
                ],
                model_name=provider_name,
                model_version="error",
            )

        target, auto_closed = _decide_routing(result)
        _persist_result(db, report, result, routed_state=target, auto_closed=auto_closed)

        # 仅当记录仍处于 PENDING_AI 时才推进状态；force 重跑（已在人工阶段）只刷新 AI 结果
        if report.status != ReportStatus.PENDING_AI:
            db.commit()
            logger.info("AI 审核结果已刷新（不改变状态） 上报=%s 结论=%s", report.report_no, result.conclusion)
            return

        if auto_closed:
            report.final_conclusion = ReportStatus.REJECTED
            report.reject_reason_code = "NO_VIOLATION"
            report.final_comment = "自动初审判定为正常情形，未发现违规，已自动结案（可人工复核）。"
            state_machine.apply_transition(
                db,
                report,
                AuditAction.AI_DONE,
                actor_type=ActorType.SYSTEM,
                target=ReportStatus.REJECTED,
                detail={
                    "ai_conclusion": str(result.conclusion),
                    "ai_confidence": result.confidence,
                    "auto_closed": True,
                },
            )
            notification_service.notify_final_result(db, report)
        else:
            state_machine.apply_transition(
                db,
                report,
                AuditAction.AI_DONE,
                actor_type=ActorType.SYSTEM,
                target=ReportStatus.AI_DONE_PENDING_HUMAN,
                detail={"ai_conclusion": str(result.conclusion), "ai_confidence": result.confidence},
            )
            notification_service.notify_internal_review_task(db, report)

        db.commit()
        logger.info(
            "AI 审核完成 上报=%s 结论=%s 置信度=%.3f 分流=%s",
            report.report_no,
            result.conclusion,
            result.confidence,
            target,
        )


def run_ai_audit_in_session(db: Session, report: Report, *, force: bool = False) -> AuditResult:
    """在调用方已有会话中执行（供测试与内部流程使用）。"""
    dup = _duplicate_history(db, report)
    req = build_audit_request(report, duplicate_history=dup)
    result = get_ai_provider().audit(req)
    target, auto_closed = _decide_routing(result)
    _persist_result(db, report, result, routed_state=target, auto_closed=auto_closed)
    if report.status == ReportStatus.PENDING_AI:
        state_machine.apply_transition(
            db,
            report,
            AuditAction.AI_DONE,
            actor_type=ActorType.SYSTEM,
            target=target,
            detail={"ai_conclusion": str(result.conclusion), "ai_confidence": result.confidence, "auto_closed": auto_closed},
        )
    report.updated_at = utc_now_iso()
    db.commit()
    return result
