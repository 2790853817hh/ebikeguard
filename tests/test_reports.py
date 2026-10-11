"""上报流程集成测试：登录 → 上传 → AI 分流 → 列表/详情 → 撤回 → 图片权限。"""
from __future__ import annotations

from tests.conftest import API, auth, make_image


def _submit(client, token, *, behavior="FLYING_WIRE_CHARGING", adapter=None, remark="楼道里拉线充电", client_uuid=None):
    files = adapter or [
        ("images", ("a.jpg", make_image((200, 60, 60)), "image/jpeg")),
        ("images", ("b.jpg", make_image((60, 120, 200)), "image/jpeg")),
    ]
    data = {
        "behavior_type_code": behavior,
        "address_text": "湘西州某小区 3 栋 2 单元楼道",
        "longitude": "109.739",
        "latitude": "28.314",
        "location_source": "geolocation",
        "remark": remark,
        "shoot_time": "2026-10-09T23:10:00",
    }
    if client_uuid:
        data["client_uuid"] = client_uuid
    return client.post(f"{API}/reports", files=files, data=data, headers=auth(token))


def test_sms_login_and_me(client, reporter_token):
    res = client.get(f"{API}/users/me", headers=auth(reporter_token))
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "reporter"
    assert body["phone"] == "13300000001"


def test_behavior_types_available(client, reporter_token):
    res = client.get(f"{API}/behavior-types", headers=auth(reporter_token))
    assert res.status_code == 200
    codes = {i["code"] for i in res.json()}
    assert "FLYING_WIRE_CHARGING" in codes and "ENTER_BUILDING" in codes


def test_upload_creates_report_and_ai_routes_to_human(client, reporter_token):
    res = _submit(client, reporter_token)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["report_no"].startswith("EV")
    assert body["created"] is True

    detail = client.get(f"{API}/reports/{body['id']}", headers=auth(reporter_token)).json()
    # 保守档：AI 判为疑似违规 -> 进入人工队列
    assert detail["status"] == "AI_DONE_PENDING_HUMAN"
    assert detail["ai_result"] is not None
    assert detail["ai_result"]["conclusion"] == "VIOLATION"
    assert detail["ai_result"]["is_auto_closed"] is False
    assert len(detail["images"]) == 2
    assert detail["images"][0]["url"].startswith("/api/v1/images/")
    # 时间线含提交与 AI 完成
    actions = [t["action"] for t in detail["timeline"]]
    assert "START_AI" in actions and "AI_DONE" in actions
    assert detail["can_withdraw"] is True
    assert detail["can_appeal"] is False


def test_idempotent_client_uuid(client, reporter_token):
    cu = "dup-0001"
    first = _submit(client, reporter_token, client_uuid=cu)
    assert first.status_code == 201
    second = _submit(client, reporter_token, client_uuid=cu)
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert second.json()["id"] == first.json()["id"]


def test_reject_bad_payload(client, reporter_token):
    # 无图片
    res = client.post(
        f"{API}/reports",
        data={"behavior_type_code": "FLYING_WIRE_CHARGING", "address_text": "x"},
        headers=auth(reporter_token),
    )
    assert res.status_code == 422

    # 无地址
    res = client.post(
        f"{API}/reports",
        files=[("images", ("a.jpg", make_image(), "image/jpeg"))],
        data={"behavior_type_code": "FLYING_WIRE_CHARGING", "address_text": "  "},
        headers=auth(reporter_token),
    )
    assert res.status_code == 400

    # 非法图片内容
    res = client.post(
        f"{API}/reports",
        files=[("images", ("a.txt", b"not-an-image", "text/plain"))],
        data={"behavior_type_code": "FLYING_WIRE_CHARGING", "address_text": "x"},
        headers=auth(reporter_token),
    )
    assert res.status_code == 400


def test_list_pagination_and_status_filter(client, reporter_token):
    res = client.get(f"{API}/reports", params={"page": 1, "page_size": 5}, headers=auth(reporter_token))
    assert res.status_code == 200
    body = res.json()
    assert body["page"] == 1 and body["page_size"] == 5
    assert body["total"] >= 1
    for item in body["items"]:
        assert item["status_label"]

    res2 = client.get(f"{API}/reports", params={"status": "AI_DONE_PENDING_HUMAN"}, headers=auth(reporter_token))
    assert all(i["status"] == "AI_DONE_PENDING_HUMAN" for i in res2.json()["items"])


def test_image_access_control(client, reporter_token, reviewer_token):
    rid = _submit(client, reporter_token).json()["id"]
    detail = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    img = detail["images"][0]

    # 1) 携带签名即可访问（供 <img> 标签直接加载）
    res = client.get(img["url"])
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/")

    # 2) 缩略图
    assert client.get(img["thumb_url"]).status_code == 200

    # 3) 去掉签名且未登录 -> 403
    bare = img["url"].split("?")[0]
    assert client.get(bare).status_code == 403

    # 4) 审核员登录后可访问任意图片
    assert client.get(bare, headers=auth(reviewer_token)).status_code == 200

    # 5) 伪造签名 -> 403
    assert client.get(bare + "?e=99999999999&s=deadbeef").status_code == 403


def test_reporter_cannot_see_others_report(client, reporter_token, admin_token):
    # 另一个上报人
    client.post(f"{API}/auth/sms/send", json={"phone": "13300000009"})
    other = client.post(f"{API}/auth/sms/login", json={"phone": "13300000009", "code": "888888"}).json()["access_token"]

    rid = _submit(client, reporter_token).json()["id"]
    assert client.get(f"{API}/reports/{rid}", headers=auth(other)).status_code == 403
    # 管理员可以看
    assert client.get(f"{API}/reports/{rid}", headers=auth(admin_token)).status_code == 200


def test_withdraw_flow(client, reporter_token):
    rid = _submit(client, reporter_token).json()["id"]
    res = client.post(f"{API}/reports/{rid}/withdraw", headers=auth(reporter_token))
    assert res.status_code == 200
    assert res.json()["status"] == "WITHDRAWN"

    detail = client.get(f"{API}/reports/{rid}", headers=auth(reporter_token)).json()
    assert detail["status"] == "WITHDRAWN"
    assert detail["can_withdraw"] is False

    # 撤回后不可再次撤回
    assert client.post(f"{API}/reports/{rid}/withdraw", headers=auth(reporter_token)).status_code == 409


def test_reverse_geocoding_stub(client, reporter_token):
    res = client.post(f"{API}/geo/reverse", json={"longitude": 109.7, "latitude": 28.3}, headers=auth(reporter_token))
    assert res.status_code == 200
    assert res.json()["source"] == "stub"


def test_unauthenticated_requests_rejected(client):
    assert client.get(f"{API}/reports").status_code == 401
    assert client.post(f"{API}/reports", data={"address_text": "x"}).status_code == 401
    assert client.get(f"{API}/review/queue").status_code == 401
