"""通知分发路由单测（离线）：多通道并行、三重过滤、状态真实化、频控、内部逐人。

缺陷回归：旧实现无论发送成败都写一条 channel=sms、sent_status="sent" 的假记录；
新实现必须「真正尝试后才写行、失败写 failed、未尝试不写行」。
"""
from __future__ import annotations

import pytest

from app.core.constants import NotificationEvent, Role, UserStatus
from app.core.utils import utc_now_iso
from app.database import SessionLocal
from app.models import AppUser, BehaviorType, Notification, Report
from app.services import notification_service as ns


class FakeProvider:
    def __init__(self, channel: str, ok: bool = True, detail: str = "ok", raises: Exception | None = None):
        self.channel = channel
        self.name = channel
        self.ok = ok
        self.detail = detail
        self.raises = raises
        self.calls: list = []

    def ready(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"provider": self.name, "ready": True}

    def send(self, msg, recipient):
        self.calls.append((msg, recipient))
        if self.raises is not None:
            raise self.raises
        return self.ok, self.detail


def _make_reporter(db, phone: str, email: str | None = None) -> AppUser:
    user = AppUser(phone=phone, role=Role.REPORTER, status=UserStatus.ACTIVE)
    if email:
        user.email = email
    db.add(user)
    db.commit()
    return user


def _make_report(db, user: AppUser, report_no: str) -> Report:
    behavior = db.query(BehaviorType).first()
    report = Report(report_no=report_no, user_id=user.id, behavior_type_id=behavior.id, address_text="测试地址")
    db.add(report)
    db.commit()
    return report


def _rows(db, user_id: int) -> list[Notification]:
    return db.query(Notification).filter(Notification.user_id == user_id).all()


def test_dispatch_multi_channel_true_status(monkeypatch):
    with SessionLocal() as db:
        user = _make_reporter(db, "13300009901", "citizen@test.cn")
        ok_p = FakeProvider("email", ok=True)
        fail_p = FakeProvider("webhook", ok=False, detail="机器人返回 500")
        monkeypatch.setattr(ns, "_route_channels", lambda audience: [ok_p, fail_p])

        ns._dispatch_external(db, user.id, "FINAL_RESULT", "标题", "内容", None)
        db.commit()

        by_channel = {r.channel: r for r in _rows(db, user.id)}
        assert set(by_channel) == {"email", "webhook"}
        assert by_channel["email"].sent_status == "sent"
        # 旧缺陷回归：失败绝不能写假 "sent"
        assert by_channel["webhook"].sent_status == "failed"
        assert len(ok_p.calls) == 1 and ok_p.calls[0][1].email == "citizen@test.cn"
        assert len(fail_p.calls) == 1


def test_no_attempt_no_row(monkeypatch):
    with SessionLocal() as db:
        user = _make_reporter(db, "13300009902")
        monkeypatch.setattr(ns, "_route_channels", lambda audience: [])
        ns._dispatch_external(db, user.id, "FINAL_RESULT", "标题", "内容", None)
        db.commit()
        assert _rows(db, user.id) == []


def test_dispatch_ignores_non_reporter_event(monkeypatch):
    with SessionLocal() as db:
        user = _make_reporter(db, "13300009906")
        fake = FakeProvider("email")
        monkeypatch.setattr(ns, "_route_channels", lambda audience: [fake])
        ns._dispatch_external(db, user.id, "REVIEW_TASK", "t", "c", None)
        db.commit()
        assert fake.calls == []
        assert _rows(db, user.id) == []


def test_provider_exception_recorded_and_never_raises(monkeypatch):
    with SessionLocal() as db:
        user = _make_reporter(db, "13300009903", "a@b.cn")
        bad = FakeProvider("email", raises=RuntimeError("boom"))
        good = FakeProvider("webhook", ok=True)
        monkeypatch.setattr(ns, "_route_channels", lambda audience: [bad, good])

        ns._dispatch_external(db, user.id, "NEED_SUPPLEMENT", "t", "c", None)  # 不应抛出
        db.commit()

        by_channel = {r.channel: r for r in _rows(db, user.id)}
        assert by_channel["email"].sent_status == "failed"
        assert by_channel["webhook"].sent_status == "sent"  # 单通道异常不影响后续通道


def test_route_channels_three_layer_filter(monkeypatch):
    import app.services.config_service as cs

    monkeypatch.setattr(cs, "get_config_bool", lambda key, default=False: default)
    monkeypatch.setattr(cs, "get_config", lambda key, default=None: default)

    # 1) email 未就绪（settings 默认 mock）→ 无通道
    assert ns._route_channels("reporter") == []

    # 2) email 就绪 → 通道出现
    fake_email = FakeProvider("email")
    monkeypatch.setattr(ns, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(ns.settings, "email_provider", "smtp")
    monkeypatch.setattr(ns.settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(ns.settings, "smtp_username", "u@test")
    monkeypatch.setattr(ns.settings, "smtp_password", "p")
    monkeypatch.setattr(ns.settings, "smtp_security", "ssl")
    assert ns._route_channels("reporter") == [fake_email]

    # 3) 总闸关闭 → 空
    monkeypatch.setattr(
        cs, "get_config_bool", lambda key, default=False: False if key == "notify.enabled" else True
    )
    assert ns._route_channels("reporter") == []

    # 4) 邮件通道开关关闭 → 空
    monkeypatch.setattr(
        cs, "get_config_bool", lambda key, default=False: False if key == "notify.email_enabled" else True
    )
    assert ns._route_channels("reporter") == []

    # 5) 名单含 email,webhook,slack：未知名被跳过；webhook 未配置 URL 不出现
    monkeypatch.setattr(cs, "get_config_bool", lambda key, default=False: True)
    monkeypatch.setattr(cs, "get_config", lambda key, default=None: "email,webhook,slack")
    monkeypatch.setattr(ns.settings, "webhook_url", "")
    assert ns._route_channels("reporter") == [fake_email]

    fake_hook = FakeProvider("webhook")
    monkeypatch.setattr(ns, "get_webhook_provider", lambda: fake_hook)
    monkeypatch.setattr(ns.settings, "webhook_kind", "wecom")
    monkeypatch.setattr(ns.settings, "webhook_url", "https://example.com/hook")
    assert ns._route_channels("reporter") == [fake_email, fake_hook]


def test_rate_limit_blocks_after_threshold(monkeypatch):
    with SessionLocal() as db:
        user = _make_reporter(db, "13300009904", "rl@b.cn")
        for _ in range(20):
            db.add(
                Notification(
                    user_id=user.id,
                    event_type="FINAL_RESULT",
                    title="t",
                    content="c",
                    channel="email",
                    sent_status="sent",
                    created_at=utc_now_iso(),
                )
            )
        db.commit()

        fake = FakeProvider("email")
        monkeypatch.setattr(ns, "_route_channels", lambda audience: [fake])
        ns._dispatch_external(db, user.id, "FINAL_RESULT", "t", "c", None)
        db.commit()

        assert fake.calls == []  # 频控：未发送
        assert len(_rows(db, user.id)) == 20  # 也未写新行


def test_internal_fanout_creates_per_user_rows_and_ops_email(monkeypatch):
    ops = FakeProvider("email")
    monkeypatch.setattr(ns, "_route_channels", lambda audience: [ops] if audience == "internal" else [])
    monkeypatch.setattr(ns.settings, "notify_ops_email", "ops1@test.cn, ops2@test.cn")

    with SessionLocal() as db:
        reporter = _make_reporter(db, "13300009905")
        report = _make_report(db, reporter, "EVTEST9001")
        ns.notify_internal_review_task(db, report)
        db.commit()

        staff = (
            db.query(AppUser)
            .filter(AppUser.role.in_([Role.REVIEWER, Role.ADMIN]), AppUser.status == UserStatus.ACTIVE)
            .all()
        )
        assert len(staff) >= 3
        for member in staff:
            rows = (
                db.query(Notification)
                .filter(
                    Notification.user_id == member.id,
                    Notification.report_id == report.id,
                    Notification.event_type == str(NotificationEvent.REVIEW_TASK),
                )
                .all()
            )
            assert len(rows) == 1 and rows[0].channel == "inapp"

        # 内部邮件发往两个值班地址
        assert [c[1].email for c in ops.calls] == ["ops1@test.cn", "ops2@test.cn"]


def test_internal_without_ops_email_skips_quietly(monkeypatch):
    ops = FakeProvider("email")
    monkeypatch.setattr(ns, "_route_channels", lambda audience: [ops])
    monkeypatch.setattr(ns.settings, "notify_ops_email", "")
    ns._dispatch_internal_external(NotificationEvent.REVIEW_TASK, "t", "c", None)
    assert ops.calls == []


def test_internal_dispatch_ignores_reporter_events(monkeypatch):
    ops = FakeProvider("email")
    monkeypatch.setattr(ns, "_route_channels", lambda audience: [ops])
    monkeypatch.setattr(ns.settings, "notify_ops_email", "ops@test.cn")
    ns._dispatch_internal_external(NotificationEvent.FINAL_RESULT, "t", "c", None)
    assert ops.calls == []
