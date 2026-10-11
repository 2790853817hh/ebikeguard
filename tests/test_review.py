"""人工审核闭环集成测试：队列、认领并发、结论校验、疑难复核、申诉、通知、统计。"""
from __future__ import annotations

from tests.conftest import API, auth, make_image


def _submit(client, token, *, behavior="FLYING_WIRE_CHARGING", remark="楼道里拉线充电", address="湘西州某小区 1 栋 1 单元"):
    return client.post(
        f"{API}/reports",
        files=[("images", ("a.jpg", make_image((180, 40, 40)), "image/jpeg"))],
        data={
            "behavior_type_code": behavior,
            "address_text": address,
            "longitude": "109.739",
            "latitude": "28.314",
            "location_source": "geolocation",
            "remark": remark,
            "shoot_time": "2026-10-09T22:30:00",
        },
        headers=auth(token),
    ).json()


def _reviewer2_token(client) -> str:
    return client.post(f"{API}/auth/login", json={"username": "reviewer2", "password": "Review@12345"}).json()["access_token"]


# ---------------------------------------------------------------- 权限


def test_reporter_cannot_access_review_api(client, reporter_token):
    assert client.get(f"{API}/review/queue", headers=auth(reporter_token)).status_code == 403
    assert client.get(f"{API}/admin/stats/overview", headers=auth(reporter_token)).status_code == 403
    assert client.get(f"{API}/admin/config", headers=auth(reporter_token)).status_code == 403


def test_reviewer_cannot_access_admin_only(client, reviewer_token):
    assert client.get(f"{API}/admin/config", headers=auth(reviewer_token)).status_code == 403
    # 审核员可以用统计
    assert client.get(f"{API}/admin/stats/overview", headers=auth(reviewer_token)).status_code == 200


# ---------------------------------------------------------------- 队列与认领


def test_queue_and_claim_conflict(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]

    queue = client.get(f"{API}/review/queue", params={"page_size": 100}, headers=auth(reviewer_token))
    assert queue.status_code == 200
    ids = [i["id"] for i in queue.json()["items"]]
    assert rid in ids

    first = client.post(f"{API}/review/{rid}/claim", headers=auth(reviewer_token))
    assert first.status_code == 200
    assert first.json()["status"] == "HUMAN_REVIEWING"

    # 重复认领（本人）-> 幂等成功
    assert client.post(f"{API}/review/{rid}/claim", headers=auth(reviewer_token)).status_code == 200

    # 另一审核员认领 -> 409
    r2 = _reviewer2_token(client)
    assert client.post(f"{API}/review/{rid}/claim", headers=auth(r2)).status_code == 409


def test_review_detail_contains_ai_suggestion(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    res = client.get(f"{API}/review/{rid}", headers=auth(reviewer_token))
    assert res.status_code == 200
    body = res.json()
    assert body["ai_result"]["conclusion"] == "VIOLATION"
    assert body["report"]["reporter_masked"]
    assert len(body["images"]) == 1
    assert body["can_review"] in (True, False)


# ---------------------------------------------------------------- 结论校验


def test_reject_requires_reason(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "REJECTED"},
        headers=auth(reviewer_token),
    )
    assert res.status_code == 400
    assert "驳回原因" in res.json()["message"]


def test_need_supplement_requires_comment(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "NEED_SUPPLEMENT"},
        headers=auth(reviewer_token),
    )
    assert res.status_code == 400


def test_reviewer_cannot_decide_others_task(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    client.post(f"{API}/review/{rid}/claim", headers=auth(reviewer_token))
    r2 = _reviewer2_token(client)
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "APPROVED", "comment": "越权尝试"},
        headers=auth(r2),
    )
    assert res.status_code == 403


# ---------------------------------------------------------------- 通过 / 不通过


def test_approve_flow_notifies_reporter(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    client.post(f"{API}/review/{rid}/claim", headers=auth(reviewer_token))
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "APPROVED", "comment": "现场确为飞线充电，已转消防处置"},
        headers=auth(reviewer_token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "APPROVED"

    detail = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail["status"] == "APPROVED"
    assert detail["final_conclusion"] == "APPROVED"
    assert detail["can_appeal"] is True

    notices = client.get(f"{API}/notifications", headers=auth(reporter_token)).json()
    events = [n["event_type"] for n in notices["items"]]
    assert "SUBMITTED" in events and "FINAL_RESULT" in events

    # 审核历史留痕
    hist = client.get(f"{API}/review/{rid}/history", headers=auth(reviewer_token)).json()
    assert hist["human_records"][0]["decision"] == "APPROVED"
    assert hist["human_records"][0]["reviewer"] == "审核员一"


def test_reject_flow_with_reason(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "REJECTED", "reason_code": "UNCLEAR_IMAGE", "comment": "夜间照片模糊，无法确认充电行为"},
        headers=auth(reviewer_token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "REJECTED"

    detail = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail["reject_reason_code"] == "UNCLEAR_IMAGE"
    assert detail["reject_reason_label"] == "图片模糊，无法辨认"


# ---------------------------------------------------------------- 补充材料


def test_supplement_flow_returns_to_ai(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "NEED_SUPPLEMENT", "comment": "请补充白天拍摄的门牌号照片"},
        headers=auth(reviewer_token),
    )
    detail = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail["status"] == "NEED_SUPPLEMENT"
    assert detail["can_supplement"] is True
    assert detail["supplement_deadline"]

    res = client.post(
        f"{API}/reports/{rid}/supplement",
        files=[("images", ("c.jpg", make_image((40, 160, 90)), "image/jpeg"))],
        data={"remark": "已补充门牌号照片"},
        headers=auth(reporter_token),
    )
    assert res.status_code == 200

    detail2 = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail2["status"] == "AI_DONE_PENDING_HUMAN"  # 重新走 AI 后回到人工队列
    assert len(detail2["images"]) == 2


# ---------------------------------------------------------------- 疑难复核


def test_escalate_then_second_review_by_other_reviewer(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    client.post(f"{API}/review/{rid}/claim", headers=auth(reviewer_token))
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "ESCALATED", "comment": "现场情况复杂，需复核"},
        headers=auth(reviewer_token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ESCALATED"

    # 原审核员不能自行复核
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "APPROVED", "comment": "自行复核"},
        headers=auth(reviewer_token),
    )
    assert res.status_code == 403

    # 另一位审核员可以复核
    r2 = _reviewer2_token(client)
    res = client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "APPROVED", "comment": "复核确认违规"},
        headers=auth(r2),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "APPROVED"

    hist = client.get(f"{API}/review/{rid}/history", headers=auth(reviewer_token)).json()
    assert any(r["is_second_review"] for r in hist["human_records"])


# ---------------------------------------------------------------- 申诉


def test_appeal_flow(client, reporter_token, admin_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "REJECTED", "reason_code": "NO_VIOLATION", "comment": "经核实不构成违规"},
        headers=auth(reviewer_token),
    )

    res = client.post(
        f"{API}/reports/{rid}/appeal",
        json={"reason": "照片显示确有飞线充电，请复核"},
        headers=auth(reporter_token),
    )
    assert res.status_code == 200
    appeal_id = res.json()["appeal_id"]

    detail = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail["status"] == "APPEALING"

    # 审核员无权限处理申诉
    assert client.post(
        f"{API}/admin/appeals/{appeal_id}/handle", json={"status": "ACCEPTED"}, headers=auth(reviewer_token)
    ).status_code == 403

    res = client.post(
        f"{API}/admin/appeals/{appeal_id}/handle",
        json={"status": "ACCEPTED", "comment": "复核后认定违规成立"},
        headers=auth(admin_token),
    )
    assert res.status_code == 200

    detail2 = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail2["status"] == "APPROVED"  # 由“不通过”改判为“通过”


def test_appeal_rejected_maintains_original(client, reporter_token, admin_token, reviewer_token):
    rid = _submit(client, reporter_token)["id"]
    client.post(
        f"{API}/review/{rid}/decision",
        json={"decision": "REJECTED", "reason_code": "UNCLEAR_IMAGE", "comment": "照片不清晰"},
        headers=auth(reviewer_token),
    )
    appeal_id = client.post(
        f"{API}/reports/{rid}/appeal", json={"reason": "我认为照片足够清晰"}, headers=auth(reporter_token)
    ).json()["appeal_id"]

    assert client.post(
        f"{API}/admin/appeals/{appeal_id}/handle",
        json={"status": "REJECTED", "comment": "复核后维持原结论"},
        headers=auth(admin_token),
    ).status_code == 200

    detail = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail["status"] == "ARCHIVED"
    assert detail["final_conclusion"] == "REJECTED"


# ---------------------------------------------------------------- 统计与配置


def test_admin_stats_and_config(client, admin_token):
    res = client.get(f"{API}/admin/stats/overview", headers=auth(admin_token))
    assert res.status_code == 200
    body = res.json()
    for key in ("total_reports", "pending_human", "approved", "rejected", "approval_rate", "by_behavior", "by_status"):
        assert key in body
    assert body["total_reports"] >= 1

    assert client.get(f"{API}/admin/stats/by-behavior", headers=auth(admin_token)).status_code == 200
    assert client.get(f"{API}/admin/stats/heatmap", headers=auth(admin_token)).status_code == 200
    assert client.get(f"{API}/admin/stats/ai-metrics", headers=auth(admin_token)).status_code == 200

    cfg = client.get(f"{API}/admin/config", headers=auth(admin_token)).json()
    keys = {c["key"] for c in cfg["items"]}
    assert "ai.auto_close_normal_enabled" in keys

    res = client.put(f"{API}/admin/config/ai.high_confidence_threshold", params={"value": "0.93"}, headers=auth(admin_token))
    assert res.status_code == 200
    cfg2 = client.get(f"{API}/admin/config", headers=auth(admin_token)).json()
    value = {c["key"]: c["value"] for c in cfg2["items"]}["ai.high_confidence_threshold"]
    assert value == "0.93"


def test_ai_rerun_endpoint(client, reporter_token, admin_token):
    rid = _submit(client, reporter_token)["id"]
    res = client.post(f"{API}/admin/reports/{rid}/ai-rerun", headers=auth(admin_token))
    assert res.status_code == 200
    assert res.json()["ai_conclusion"] in ("VIOLATION", "NORMAL", "UNCERTAIN")


def test_meta_endpoint(client):
    res = client.get(f"{API}/meta")
    assert res.status_code == 200
    body = res.json()
    assert body["strategy"]["mode"] == "conservative"
    assert body["ai_provider"] == "rule"


def test_healthz(client):
    assert client.get("/healthz").json()["ok"] is True
