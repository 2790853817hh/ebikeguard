"""演示数据脚本：向运行中的服务提交若干示例上报，并走一遍审核，便于演示与验收。

用法（服务需已启动）：
    python scripts/demo_data.py                       # 默认 http://127.0.0.1:8000
    python scripts/demo_data.py --base http://host:8000 --count 4
"""
from __future__ import annotations

import argparse
import io
import random
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = "/api/v1"
SMS_MOCK_CODE = "888888"

SAMPLES = [
    ("FLYING_WIRE_CHARGING", "湘西州吉首市某小区 3 栋 2 单元 1 楼楼道", "从 3 楼窗口拉线到楼下给电动车充电", "飞线", (196, 70, 60)),
    ("ENTER_BUILDING", "湘西州吉首市某小区 5 栋 1 单元门厅", "电动车直接推进单元门厅停放", "入户", (70, 110, 196)),
    ("CORRIDOR_PARKING", "湘西州吉首市某小区 8 栋 3 单元疏散楼梯间", "楼梯间停了两台电动车，通道只剩一半", "楼道", (120, 110, 70)),
    ("BLOCKING_PASSAGE", "湘西州吉首市某小区消防车通道", "电动车占用消防车通道，影响通行", "堵塞", (150, 60, 150)),
    ("INDOOR_CHARGING", "湘西州吉首市某小区 2 栋 1 单元一楼室内", "室内给电动车电池充电，插线板还接着取暖器", "充电", (60, 150, 130)),
]


def make_photo(text: str, color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (1440, 1080), color)
    d = ImageDraw.Draw(img)
    for i in range(0, 1440, 120):
        d.line([(i, 0), (i, 1080)], fill=(255, 255, 255, 30), width=2)
    for j in range(0, 1080, 120):
        d.line([(0, j), (1440, j)], fill=(255, 255, 255, 30), width=2)
    d.rectangle([80, 420, 1360, 660], outline=(255, 255, 255), width=5)
    d.text((110, 500), f"DEMO PHOTO - {text} (sample, not a real capture)", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--phone", default="13349638573", help="上报人手机号（默认使用本机演示号码）")
    parser.add_argument("--count", type=int, default=3, help="生成多少条上报（最多 5 条）")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    n = max(1, min(len(SAMPLES), args.count))

    with httpx.Client(base_url=base, timeout=60.0) as c:
        # 连通性
        try:
            c.get("/healthz").raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"无法连接服务 {base}：{exc}")
            print("请先启动：.venv/Scripts/python run.py")
            sys.exit(1)

        # 1) 上报人登录
        r = c.post(f"{API}/auth/sms/send", json={"phone": args.phone})
        if r.status_code != 200:
            print("发送验证码失败：", r.text)
            sys.exit(1)
        code = (r.json().get("dev_code") or SMS_MOCK_CODE)
        r = c.post(f"{API}/auth/sms/login", json={"phone": args.phone, "code": code})
        r.raise_for_status()
        reporter = r.json()
        rh = {"Authorization": f"Bearer {reporter['access_token']}"}
        print(f"[ok] 上报人已登录：{reporter['user']['phone']}")

        created = []
        for i in range(n):
            code_, address, remark, tag, color = SAMPLES[i]
            files = [("images", (f"demo_{i}.jpg", make_photo(f"{tag} #{i + 1}", color), "image/jpeg"))]
            if random.random() > 0.4:
                files.append(("images", (f"demo_{i}_b.jpg", make_photo(f"{tag} close-up", color), "image/jpeg")))
            data = {
                "behavior_type_code": code_,
                "address_text": address,
                "remark": remark,
                "shoot_time": time.strftime("%Y-%m-%dT22:%M:%S"),
                "location_source": "geolocation",
                "longitude": 109.739 + i * 0.002,
                "latitude": 28.314 + i * 0.002,
                "client_uuid": f"demo-{int(time.time())}-{i}",
            }
            res = c.post(f"{API}/reports", files=files, data=data, headers=rh)
            if res.status_code != 201:
                print(f"   提交失败：{res.status_code} {res.text[:200]}")
                continue
            body = res.json()
            created.append(body)
            print(f"[ok] 已提交 #{i + 1}：{body['report_no']}  状态={body['status']}")

        if not created:
            sys.exit(0)

        # 2) 审核员处理
        r = c.post(f"{API}/auth/login", json={"username": "reviewer1", "password": "Review@12345"})
        if r.status_code != 200:
            print("审核员登录失败（是否已执行 scripts/init_db.py？）")
            sys.exit(0)
        vh = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # 第 1 条：认领并通过
        first = created[0]
        c.post(f"{API}/review/{first['id']}/claim", headers=vh)
        c.post(
            f"{API}/review/{first['id']}/decision",
            json={"decision": "APPROVED", "comment": "现场照片清晰，确认存在飞线充电，已转消防部门跟进。"},
            headers=vh,
        )
        print(f"[ok] 已审核通过：{first['report_no']}")

        # 第 2 条：退回补充材料
        if len(created) > 1:
            second = created[1]
            c.post(
                f"{API}/review/{second['id']}/decision",
                json={"decision": "NEED_SUPPLEMENT", "comment": "请补充能看清门牌号或楼栋号的远景照片"},
                headers=vh,
            )
            print(f"[ok] 已退回补充材料：{second['report_no']}")

        # 第 3 条：不通过
        if len(created) > 2:
            third = created[2]
            c.post(
                f"{API}/review/{third['id']}/decision",
                json={"decision": "REJECTED", "reason_code": "UNCLEAR_IMAGE", "comment": "夜间拍摄较为模糊，难以确认车辆状态。"},
                headers=vh,
            )
            print(f"[ok] 已判定不通过：{third['report_no']}")

    print("\n演示数据生成完成。")
    print(f"  上报人登录：手机号 {args.phone} + 验证码 {SMS_MOCK_CODE}")
    print("  审核端登录：reviewer1 / Review@12345")
    print(f"  打开页面：{base}/")


if __name__ == "__main__":
    main()
