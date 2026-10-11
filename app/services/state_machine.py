"""审核状态机 —— 系统唯一的合法流转入口。

所有涉及 report.status 的变更都必须经过 `apply_transition()`，
以保证「流转规则校验 + 状态写入 + 全量留痕」三件事原子完成。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.constants import AuditAction as A
from app.core.constants import ActorType, ReportStatus as S
from app.core.exceptions import IllegalTransition
from app.core.utils import utc_now_iso
from app.models import Report
from app.services import audit_log_service

#: (当前状态, 动作) -> 目标状态；值为 None 表示目标状态由调用方传入（多分支）
TRANSITIONS: dict[tuple[str, str], str | None] = {
    # ---- AI 审核阶段 ----
    (S.SUBMITTED, A.START_AI): S.PENDING_AI,
    (S.PENDING_AI, A.AI_DONE): None,  # 常态 -> AI_DONE_PENDING_HUMAN；自动结案 -> REJECTED
    # ---- 人工审核阶段 ----
    (S.AI_DONE_PENDING_HUMAN, A.CLAIM): S.HUMAN_REVIEWING,
    (S.HUMAN_REVIEWING, A.APPROVE): S.APPROVED,
    (S.HUMAN_REVIEWING, A.REJECT): S.REJECTED,
    (S.HUMAN_REVIEWING, A.NEED_SUPPLEMENT): S.NEED_SUPPLEMENT,
    (S.HUMAN_REVIEWING, A.ESCALATE): S.ESCALATED,
    # ---- 补充材料 ----
    (S.NEED_SUPPLEMENT, A.SUPPLEMENT): S.PENDING_AI,
    (S.NEED_SUPPLEMENT, A.SLA_REJECT): S.REJECTED,
    # ---- 疑难复核 ----
    (S.ESCALATED, A.SECOND_APPROVE): S.APPROVED,
    (S.ESCALATED, A.SECOND_REJECT): S.REJECTED,
    # ---- 申诉 ----
    (S.APPROVED, A.APPEAL): S.APPEALING,
    (S.REJECTED, A.APPEAL): S.APPEALING,
    (S.APPEALING, A.APPEAL_RESOLVE): None,  # APPROVED / REJECTED / ARCHIVED
    # ---- 归档 ----
    (S.APPROVED, A.ARCHIVE): S.ARCHIVED,
    (S.REJECTED, A.ARCHIVE): S.ARCHIVED,
    # ---- 撤回（进入人工终态前均可） ----
    (S.SUBMITTED, A.WITHDRAW): S.WITHDRAWN,
    (S.PENDING_AI, A.WITHDRAW): S.WITHDRAWN,
    (S.AI_DONE_PENDING_HUMAN, A.WITHDRAW): S.WITHDRAWN,
    (S.NEED_SUPPLEMENT, A.WITHDRAW): S.WITHDRAWN,
    # ---- 作废（管理员） ----
    (S.SUBMITTED, A.CANCEL): S.CANCELLED,
    (S.PENDING_AI, A.CANCEL): S.CANCELLED,
    (S.AI_DONE_PENDING_HUMAN, A.CANCEL): S.CANCELLED,
    (S.HUMAN_REVIEWING, A.CANCEL): S.CANCELLED,
    (S.NEED_SUPPLEMENT, A.CANCEL): S.CANCELLED,
    (S.ESCALATED, A.CANCEL): S.CANCELLED,
    (S.APPEALING, A.CANCEL): S.CANCELLED,
}

#: 允许指定目标状态的多分支动作及其合法取值
TARGET_CHOICES: dict[str, set[str]] = {
    A.AI_DONE: {S.AI_DONE_PENDING_HUMAN, S.REJECTED, S.APPROVED},
    A.APPEAL_RESOLVE: {S.APPROVED, S.REJECTED, S.ARCHIVED},
}

#: 终态（除申诉/归档外不再流转）
FINAL_FOR_ACTOR: dict[str, set[str]] = {
    "system": {S.PENDING_AI, S.AI_DONE_PENDING_HUMAN, S.REJECTED, S.ARCHIVED, S.WITHDRAWN, S.CANCELLED},
}

#: 动作 -> 允许触发的角色类型
ACTION_ACTORS: dict[str, set[str]] = {
    A.START_AI: {ActorType.SYSTEM},
    A.AI_DONE: {ActorType.SYSTEM},
    A.CLAIM: {ActorType.REVIEWER, ActorType.ADMIN},
    A.APPROVE: {ActorType.REVIEWER, ActorType.ADMIN},
    A.REJECT: {ActorType.REVIEWER, ActorType.ADMIN},
    A.NEED_SUPPLEMENT: {ActorType.REVIEWER, ActorType.ADMIN},
    A.ESCALATE: {ActorType.REVIEWER, ActorType.ADMIN},
    A.SUPPLEMENT: {ActorType.USER},
    A.SECOND_APPROVE: {ActorType.REVIEWER, ActorType.ADMIN},
    A.SECOND_REJECT: {ActorType.REVIEWER, ActorType.ADMIN},
    A.APPEAL: {ActorType.USER},
    A.APPEAL_RESOLVE: {ActorType.ADMIN},
    A.ARCHIVE: {ActorType.SYSTEM, ActorType.ADMIN},
    A.WITHDRAW: {ActorType.USER},
    A.CANCEL: {ActorType.ADMIN},
    A.SLA_REJECT: {ActorType.SYSTEM},
}


def resolve_target(from_status: str, action: str, target: str | None = None) -> str:
    """校验并解析目标状态；非法流转抛 IllegalTransition。"""
    key = (str(from_status), str(action))
    if key not in TRANSITIONS:
        raise IllegalTransition(f"当前状态「{from_status}」下不允许执行动作「{action}」")

    expected = TRANSITIONS[key]
    if expected is None:
        choices = TARGET_CHOICES.get(str(action), set())
        if not target:
            raise IllegalTransition(f"动作「{action}」必须指定目标状态，可选：{sorted(choices)}")
        if target not in choices:
            raise IllegalTransition(f"动作「{action}」的目标状态「{target}」不合法，可选：{sorted(choices)}")
        return target

    if target and target != expected:
        raise IllegalTransition(f"动作「{action}」的目标状态应为「{expected}」，收到「{target}」")
    return expected


def check_actor(action: str, actor_type: str) -> None:
    allowed = ACTION_ACTORS.get(str(action))
    if allowed is not None and actor_type not in allowed:
        raise IllegalTransition(f"角色「{actor_type}」无权执行动作「{action}」")


def apply_transition(
    db: Session,
    report: Report,
    action: str,
    *,
    actor_id: int | None = None,
    actor_type: str = ActorType.SYSTEM,
    target: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    touch_finished: bool = True,
) -> Report:
    """执行一次合法流转：校验 -> 更新状态 -> 写留痕。"""
    check_actor(action, actor_type)
    from_status = report.status
    to_status = resolve_target(from_status, action, target)

    report.status = to_status
    report.updated_at = utc_now_iso()
    if touch_finished and to_status in (S.APPROVED, S.REJECTED, S.WITHDRAWN, S.CANCELLED, S.ARCHIVED):
        report.finished_at = report.finished_at or utc_now_iso()
    if to_status == S.ARCHIVED:
        report.archived_at = utc_now_iso()

    audit_log_service.log_action(
        db,
        report_id=report.id,
        action=str(action),
        actor_id=actor_id,
        actor_type=str(actor_type),
        from_status=from_status,
        to_status=to_status,
        detail=detail,
        ip=ip,
        user_agent=user_agent,
    )
    return report


def can_do(status: str, action: str) -> bool:
    return (str(status), str(action)) in TRANSITIONS


def allowed_actions(status: str, actor_type: str) -> list[str]:
    result = []
    for (from_status, action), _ in TRANSITIONS.items():
        if from_status != str(status):
            continue
        if actor_type in ACTION_ACTORS.get(action, set()):
            result.append(action)
    return sorted(result)
