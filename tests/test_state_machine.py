"""审核状态机单元测试：覆盖合法流转、非法流转、角色权限。"""
from __future__ import annotations

import pytest

from app.core.constants import ActorType, AuditAction as A, ReportStatus as S
from app.core.exceptions import IllegalTransition
from app.models import Report
from app.services import state_machine


def _report(status: str = S.SUBMITTED) -> Report:
    r = Report(report_no="EV202601010001", user_id=1, behavior_type_id=1, address_text="测试地址", status=status)
    r.id = 1
    return r


@pytest.mark.parametrize(
    "from_status,action,target,expected",
    [
        (S.SUBMITTED, A.START_AI, None, S.PENDING_AI),
        (S.PENDING_AI, A.AI_DONE, S.AI_DONE_PENDING_HUMAN, S.AI_DONE_PENDING_HUMAN),
        (S.PENDING_AI, A.AI_DONE, S.REJECTED, S.REJECTED),
        (S.AI_DONE_PENDING_HUMAN, A.CLAIM, None, S.HUMAN_REVIEWING),
        (S.HUMAN_REVIEWING, A.APPROVE, None, S.APPROVED),
        (S.HUMAN_REVIEWING, A.REJECT, None, S.REJECTED),
        (S.HUMAN_REVIEWING, A.NEED_SUPPLEMENT, None, S.NEED_SUPPLEMENT),
        (S.HUMAN_REVIEWING, A.ESCALATE, None, S.ESCALATED),
        (S.NEED_SUPPLEMENT, A.SUPPLEMENT, None, S.PENDING_AI),
        (S.NEED_SUPPLEMENT, A.SLA_REJECT, None, S.REJECTED),
        (S.ESCALATED, A.SECOND_APPROVE, None, S.APPROVED),
        (S.ESCALATED, A.SECOND_REJECT, None, S.REJECTED),
        (S.APPROVED, A.APPEAL, None, S.APPEALING),
        (S.APPEALING, A.APPEAL_RESOLVE, S.APPROVED, S.APPROVED),
        (S.APPROVED, A.ARCHIVE, None, S.ARCHIVED),
        (S.SUBMITTED, A.WITHDRAW, None, S.WITHDRAWN),
        (S.AI_DONE_PENDING_HUMAN, A.WITHDRAW, None, S.WITHDRAWN),
    ],
)
def test_resolve_target_legal(from_status, action, target, expected):
    assert state_machine.resolve_target(from_status, action, target) == expected


@pytest.mark.parametrize(
    "from_status,action",
    [
        (S.SUBMITTED, A.APPROVE),          # 未审核不可直接通过
        (S.APPROVED, A.APPROVE),           # 终态不可重复通过
        (S.ARCHIVED, A.WITHDRAW),          # 已归档不可撤回
        (S.HUMAN_REVIEWING, A.APPEAL),     # 审核中不可申诉
        (S.WITHDRAWN, A.CLAIM),            # 已撤回不可认领
    ],
)
def test_resolve_target_illegal(from_status, action):
    with pytest.raises(IllegalTransition):
        state_machine.resolve_target(from_status, action)


def test_resolve_target_requires_explicit_target():
    with pytest.raises(IllegalTransition):
        state_machine.resolve_target(S.APPEALING, A.APPEAL_RESOLVE, None)


def test_resolve_target_rejects_bad_target():
    with pytest.raises(IllegalTransition):
        state_machine.resolve_target(S.APPEALING, A.APPEAL_RESOLVE, S.HUMAN_REVIEWING)


def test_actor_permission():
    with pytest.raises(IllegalTransition):
        state_machine.check_actor(A.APPROVE, ActorType.USER)
    with pytest.raises(IllegalTransition):
        state_machine.check_actor(A.APPEAL, ActorType.REVIEWER)
    with pytest.raises(IllegalTransition):
        state_machine.check_actor(A.START_AI, ActorType.USER)
    state_machine.check_actor(A.APPROVE, ActorType.REVIEWER)  # 不抛异常


def test_can_do_and_allowed_actions():
    assert state_machine.can_do(S.HUMAN_REVIEWING, A.APPROVE)
    assert not state_machine.can_do(S.SUBMITTED, A.APPROVE)
    actions = state_machine.allowed_actions(S.HUMAN_REVIEWING, ActorType.REVIEWER)
    assert A.APPROVE in actions and A.REJECT in actions and A.ESCALATE in actions
    reporter_actions = state_machine.allowed_actions(S.SUBMITTED, ActorType.USER)
    assert reporter_actions == [A.WITHDRAW]
