"""上报记录服务：提交、查询、撤回、补充材料、申诉。"""
from __future__ import annotations

import json
import math
from typing import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.constants import (
    APPEALABLE_STATUSES,
    REPORT_STATUS_LABEL,
    REJECT_REASON_LABEL,
    ActorType,
    AuditAction,
    Decision,
    ReportStatus,
)
from app.core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from app.core.logging import get_logger
from app.core.utils import hamming_distance, parse_iso, to_cn, utc_now
from app.core.utils import utc_now_iso
from app.models import AppUser, Appeal, AuditActionLog, BehaviorType, Report, ReportImage
from app.services import audit_log_service, config_service, image_service, notification_service, state_machine

logger = get_logger(__name__)

ACTION_LABEL: dict[str, str] = {
    AuditAction.START_AI: "提交成功，进入自动初审",
    AuditAction.AI_DONE: "自动初审完成",
    AuditAction.CLAIM: "审核员认领",
    AuditAction.APPROVE: "审核通过",
    AuditAction.REJECT: "审核不通过",
    AuditAction.NEED_SUPPLEMENT: "退回补充材料",
    AuditAction.ESCALATE: "转专家复核",
    AuditAction.SUPPLEMENT: "上报人补充材料",
    AuditAction.SECOND_APPROVE: "复核通过",
    AuditAction.SECOND_REJECT: "复核不通过",
    AuditAction.APPEAL: "上报人提出申诉",
    AuditAction.APPEAL_RESOLVE: "申诉复核完成",
    AuditAction.ARCHIVE: "记录已归档",
    AuditAction.WITHDRAW: "上报人撤回",
    AuditAction.CANCEL: "管理员作废",
    AuditAction.SLA_REJECT: "超期未补充，自动不通过",
}

EARTH_RADIUS_M = 6371000.0


# ---------------------------------------------------------------- 工具


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def generate_report_no(db: Session) -> str:
    from app.core.utils import new_report_no, report_no_day

    # 前缀必须与 new_report_no 同源同时区（北京）：曾用 UTC 日期拼前缀，
    # 北京时间 00:00~08:00 期间前缀查不到记录 → 流水号恒为 1 → 连续上报重号。
    prefix = f"EV{report_no_day()}"
    last = (
        db.query(Report.report_no)
        .filter(Report.report_no.like(f"{prefix}%"))
        .order_by(Report.report_no.desc())
        .first()
    )
    seq = 1
    if last and last[0]:
        try:
            seq = int(last[0][-4:]) + 1
        except ValueError:
            seq = 1
    return new_report_no(seq)


def get_behavior(db: Session, *, behavior_type_id: int | None = None, code: str | None = None) -> BehaviorType:
    q = db.query(BehaviorType)
    if behavior_type_id is not None:
        q = q.filter(BehaviorType.id == behavior_type_id)
    elif code:
        q = q.filter(BehaviorType.code == code)
    else:
        raise BadRequest("必须指定行为类型")
    behavior = q.first()
    if not behavior or not behavior.is_active:
        raise BadRequest("所选行为类型不存在或已停用")
    return behavior


def compute_duplicate_history(db: Session, images: Sequence[image_service.ProcessedImage]) -> tuple[int, int]:
    """检测与历史上报的重复/高度相似图片。

    返回 (完全相同哈希的图片数, 感知哈希相近的图片数)。
    """
    if not images:
        return 0, 0
    shas = [i.sha256 for i in images]
    exact = (
        db.query(func.count(func.distinct(ReportImage.report_id)))
        .filter(ReportImage.sha256.in_(shas))
        .scalar()
        or 0
    )

    similar_reports: set[int] = set()
    buckets = {i.phash_bucket for i in images if i.phash_bucket is not None}
    if buckets:
        candidates = db.query(ReportImage.report_id, ReportImage.phash).filter(ReportImage.phash_bucket.in_(buckets)).all()
        for report_id, phash in candidates:
            for img in images:
                if phash and hamming_distance(phash, img.phash) <= 6:
                    similar_reports.add(report_id)
                    break
    return int(exact), len(similar_reports)


# ---------------------------------------------------------------- 创建


def create_report(
    db: Session,
    user: AppUser,
    *,
    behavior_type_id: int | None,
    behavior_type_code: str | None,
    address_text: str,
    longitude: float | None,
    latitude: float | None,
    location_source: str | None,
    shoot_time: str | None,
    remark: str | None,
    files: list[tuple[str | None, bytes]],
    client_uuid: str | None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Report, bool]:
    """提交上报。返回 (report, created)；client_uuid 重复时复用已有记录（幂等）。"""
    if client_uuid:
        existing = db.query(Report).filter(Report.client_uuid == client_uuid).first()
        if existing:
            if existing.user_id != user.id:
                raise Conflict("该提交标识已被占用")
            return existing, False

    if not files:
        raise BadRequest("请至少上传一张现场照片")
    max_images = config_service.get_config_int("upload.max_images", 9)
    if len(files) > max_images:
        raise BadRequest(f"单次最多上传 {max_images} 张照片")

    address_text = (address_text or "").strip()
    if not address_text:
        raise BadRequest("请填写行为发生的地址")

    behavior = get_behavior(db, behavior_type_id=behavior_type_id, code=behavior_type_code)

    processed = [image_service.process_upload(raw, name) for name, raw in files]

    exact_dup, similar_dup = compute_duplicate_history(db, processed)

    # 定位冲突判定：浏览器定位 vs 照片 EXIF GPS
    location_conflict = False
    if longitude is not None and latitude is not None:
        for img in processed:
            if img.gps_lat is not None and img.gps_lng is not None:
                if _haversine_m(latitude, longitude, img.gps_lat, img.gps_lng) > 1000:
                    location_conflict = True
                break

    report = Report(
        report_no=generate_report_no(db),
        user_id=user.id,
        behavior_type_id=behavior.id,
        address_text=address_text,
        longitude=longitude,
        latitude=latitude,
        location_source=location_source or "unknown",
        location_conflict=location_conflict,
        shoot_time=shoot_time,
        remark=(remark or "").strip() or None,
        status=ReportStatus.SUBMITTED,
        client_uuid=client_uuid,
        submitted_at=utc_now_iso(),
    )
    db.add(report)
    db.flush()  # 获取 report.id，用于生成存储键

    for idx, img in enumerate(processed):
        origin_key, thumb_key = image_service.store_image(report.id, img)
        db.add(
            ReportImage(
                id=img.image_id,
                report_id=report.id,
                sha256=img.sha256,
                storage_key=origin_key,
                thumb_key=thumb_key,
                original_name=img.original_name,
                mime_type=img.mime_type,
                size_bytes=img.size_bytes,
                width=img.width,
                height=img.height,
                taken_at=img.taken_at,
                exif_json=json.dumps(img.exif, ensure_ascii=False) if img.exif else None,
                gps_lat=img.gps_lat,
                gps_lng=img.gps_lng,
                phash=img.phash,
                phash_bucket=img.phash_bucket,
                sort_order=idx,
            )
        )
    db.commit()
    db.refresh(report)

    # 进入 AI 审核：SUBMITTED -> PENDING_AI
    state_machine.apply_transition(
        db,
        report,
        AuditAction.START_AI,
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        detail={"exact_duplicate_reports": exact_dup, "similar_duplicate_reports": similar_dup},
    )
    notification_service.notify_submitted(db, report)
    db.commit()
    db.refresh(report)
    logger.info("上报创建成功 %s (user=%s, images=%d)", report.report_no, user.id, len(processed))
    return report, True


# ---------------------------------------------------------------- 查询


def visible_query(db: Session, user: AppUser):
    q = db.query(Report)
    if user.role == "reporter":
        q = q.filter(Report.user_id == user.id)
    return q


def list_reports(db: Session, user: AppUser, *, status: str | None, page: int, page_size: int):
    q = visible_query(db, user)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        q = q.filter(Report.status.in_(statuses))
    total = q.count()
    rows = (
        q.order_by(Report.submitted_at.desc(), Report.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def get_report_or_404(db: Session, report_id: int) -> Report:
    report = db.get(Report, report_id)
    if not report:
        raise NotFound("上报记录不存在")
    return report


def assert_can_view(report: Report, user: AppUser) -> None:
    if user.role == "reporter" and report.user_id != user.id:
        raise Forbidden("无权查看该上报记录")


# ---------------------------------------------------------------- 序列化


def _cover_url(report: Report) -> str | None:
    if not report.images:
        return None
    return image_service.build_image_url(report.images[0].id, thumb=True)


def to_list_item(report: Report) -> dict:
    return {
        "id": report.id,
        "report_no": report.report_no,
        "behavior_type_name": report.behavior_type.name if report.behavior_type else None,
        "address_text": report.address_text,
        "status": report.status,
        "status_label": REPORT_STATUS_LABEL.get(report.status, report.status),
        "ai_conclusion": report.ai_conclusion,
        "ai_confidence": report.ai_confidence,
        "final_conclusion": report.final_conclusion,
        "cover_url": _cover_url(report),
        "submitted_at": report.submitted_at,
        "submitted_at_cn": to_cn(report.submitted_at),
    }


def image_to_dict(image: ReportImage) -> dict:
    return {
        "id": image.id,
        "width": image.width,
        "height": image.height,
        "size_bytes": image.size_bytes,
        "mime_type": image.mime_type,
        "taken_at": image.taken_at,
        "sort_order": image.sort_order,
        "url": image_service.build_image_url(image.id, thumb=False),
        "thumb_url": image_service.build_image_url(image.id, thumb=True),
    }


def latest_ai_result(db: Session, report_id: int):
    from app.models import AiAuditResult

    return (
        db.query(AiAuditResult)
        .filter(AiAuditResult.report_id == report_id)
        .order_by(AiAuditResult.id.desc())
        .first()
    )


def ai_result_to_dict(row) -> dict | None:  # noqa: ANN001
    if not row:
        return None
    try:
        reasons = json.loads(row.reasons_json or "[]")
    except json.JSONDecodeError:
        reasons = []
    return {
        "conclusion": row.conclusion,
        "confidence": row.confidence,
        "reasons": reasons,
        "model_name": row.model_name,
        "model_version": row.model_version,
        "latency_ms": row.latency_ms,
        "is_auto_closed": row.is_auto_closed,
        "created_at": row.created_at,
    }


def build_timeline(db: Session, report: Report) -> list[dict]:
    logs = audit_log_service.report_timeline(db, report.id)
    actor_ids = {log.actor_id for log in logs if log.actor_id}
    names: dict[int, str] = {}
    if actor_ids:
        for uid, real_name, username in db.query(AppUser.id, AppUser.real_name, AppUser.username).filter(AppUser.id.in_(actor_ids)):
            names[uid] = real_name or username or f"用户{uid}"
    items = []
    for log in logs:
        try:
            detail = json.loads(log.detail_json) if log.detail_json else {}
        except json.JSONDecodeError:
            detail = {}
        items.append(
            {
                "action": log.action,
                "label": ACTION_LABEL.get(log.action, log.action),
                "actor": names.get(log.actor_id) if log.actor_id else "系统",
                "from_status": log.from_status,
                "to_status": log.to_status,
                "comment": detail.get("comment") or detail.get("reason_text"),
                "at": log.created_at,
                "at_cn": to_cn(log.created_at),
            }
        )
    return items


def supplement_deadline(report: Report) -> str | None:
    if report.status != ReportStatus.NEED_SUPPLEMENT or not report.updated_at:
        return None
    base = parse_iso(report.updated_at)
    if not base:
        return None
    from datetime import timedelta

    hours = config_service.get_config_int("review.supplement_sla_hours", 72)
    return (base + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_detail(db: Session, report: Report) -> dict:
    data = to_list_item(report)
    data.update(
        {
            "longitude": report.longitude,
            "latitude": report.latitude,
            "location_source": report.location_source,
            "location_conflict": report.location_conflict,
            "shoot_time": report.shoot_time,
            "remark": report.remark,
            "reject_reason_code": report.reject_reason_code,
            "reject_reason_label": REJECT_REASON_LABEL.get(report.reject_reason_code or ""),
            "final_comment": report.final_comment,
            "images": [image_to_dict(i) for i in report.images],
            "ai_result": ai_result_to_dict(latest_ai_result(db, report.id)),
            "timeline": build_timeline(db, report),
            "can_withdraw": state_machine.can_do(report.status, AuditAction.WITHDRAW),
            "can_supplement": report.status == ReportStatus.NEED_SUPPLEMENT,
            "can_appeal": report.status in APPEALABLE_STATUSES,
            "supplement_deadline": supplement_deadline(report),
        }
    )
    return data


# ---------------------------------------------------------------- 上报人动作


def withdraw(db: Session, report: Report, user: AppUser, *, ip: str | None = None, ua: str | None = None) -> Report:
    if report.user_id != user.id:
        raise Forbidden("只能撤回自己的上报")
    state_machine.apply_transition(
        db,
        report,
        AuditAction.WITHDRAW,
        actor_id=user.id,
        actor_type=ActorType.USER,
        ip=ip,
        user_agent=ua,
    )
    db.commit()
    db.refresh(report)
    return report


def supplement(
    db: Session,
    report: Report,
    user: AppUser,
    *,
    remark: str | None,
    files: list[tuple[str | None, bytes]],
    ip: str | None = None,
    ua: str | None = None,
) -> Report:
    if report.user_id != user.id:
        raise Forbidden("只能补充自己的上报")
    if report.status != ReportStatus.NEED_SUPPLEMENT:
        raise Conflict("当前状态不支持补充材料")

    if remark and remark.strip():
        report.remark = f"{report.remark}\n【补充】{remark.strip()}" if report.remark else f"【补充】{remark.strip()}"

    if files:
        max_images = config_service.get_config_int("upload.max_images", 9)
        existing = len(report.images)
        if existing + len(files) > max_images:
            raise BadRequest(f"图片总数不能超过 {max_images} 张")
        processed = [image_service.process_upload(raw, name) for name, raw in files]
        for idx, img in enumerate(processed, start=existing):
            origin_key, thumb_key = image_service.store_image(report.id, img)
            db.add(
                ReportImage(
                    id=img.image_id,
                    report_id=report.id,
                    sha256=img.sha256,
                    storage_key=origin_key,
                    thumb_key=thumb_key,
                    original_name=img.original_name,
                    mime_type=img.mime_type,
                    size_bytes=img.size_bytes,
                    width=img.width,
                    height=img.height,
                    taken_at=img.taken_at,
                    exif_json=json.dumps(img.exif, ensure_ascii=False) if img.exif else None,
                    gps_lat=img.gps_lat,
                    gps_lng=img.gps_lng,
                    phash=img.phash,
                    phash_bucket=img.phash_bucket,
                    sort_order=idx,
                )
            )
    db.flush()

    state_machine.apply_transition(
        db,
        report,
        AuditAction.SUPPLEMENT,
        actor_id=user.id,
        actor_type=ActorType.USER,
        detail={"comment": (remark or "").strip() or None, "added_images": len(files)},
        ip=ip,
        user_agent=ua,
    )
    db.commit()
    db.refresh(report)
    return report


def appeal(
    db: Session,
    report: Report,
    user: AppUser,
    *,
    reason: str,
    ip: str | None = None,
    ua: str | None = None,
) -> Appeal:
    if report.user_id != user.id:
        raise Forbidden("只能对自己的上报提出申诉")
    if report.status not in APPEALABLE_STATUSES:
        raise Conflict("当前状态不支持申诉")

    # 时限校验
    window_days = config_service.get_config_int("appeal.window_days", 7)
    finished = parse_iso(report.finished_at)
    if finished:
        from datetime import timedelta

        if utc_now() > finished + timedelta(days=window_days):
            raise Conflict(f"申诉时限为 {window_days} 天，已超过申诉期")

    row = Appeal(
        report_id=report.id,
        user_id=user.id,
        reason=reason.strip(),
        status="PENDING",
        from_conclusion=report.final_conclusion,
        created_at=utc_now_iso(),
    )
    db.add(row)

    state_machine.apply_transition(
        db,
        report,
        AuditAction.APPEAL,
        actor_id=user.id,
        actor_type=ActorType.USER,
        detail={"comment": reason.strip()},
        ip=ip,
        user_agent=ua,
    )
    notification_service.notify_appeal_received(db, report)
    db.commit()
    db.refresh(row)
    return row
