"""前端通知中心静态约束（离线）：页面接线、头部入口与未读接口、上报表单选填邮箱。

背景（2026-10-12）：站内信后端接口早已就绪，但前端从未接入（用户看不到站内信）；
本次补齐通知中心页 + 未读红点，并新增上报表单的「接收审核结果邮箱」选填项。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def _read(relative: str) -> str:
    return (STATIC / relative).read_text(encoding="utf-8")


def test_notification_center_page_exists_and_wired():
    html = _read("notifications.html")
    assert "通知中心" in html and "notifications.js" in html
    js = _read("js/notifications.js")
    assert "'/notifications?page='" in js
    assert "/notifications/' + el.dataset.id + '/read'" in js
    assert "/users/me/email" in js


def test_header_has_notify_entry_and_unread_badge():
    api_js = _read("js/api.js")
    assert "notifications.html" in api_js
    assert "navBadge" in api_js
    assert "/notifications/unread-count" in api_js
    assert "refreshUnreadBadge" in api_js


def test_upload_page_collects_optional_email():
    html = _read("upload.html")
    assert 'id="contactEmail"' in html
    js = _read("js/upload.js")
    assert "contact_email" in js


def test_badge_styles_defined():
    css = _read("css/base.css")
    assert ".badge {" in css and ".badge-dot {" in css
    assert 'input[type="email"]' in css
