"""数据库初始化脚本：建表 + 写入种子数据（行为类型字典、系统配置）+ 演示账号。

用法：
    python scripts/init_db.py            # 建表 + 种子（幂等，已存在则跳过）
    python scripts/init_db.py --reset    # 危险：删除现有库后重建（仅演示环境使用）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.core.constants import DEFAULT_SYSTEM_CONFIG, Role, UserStatus  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.utils import utc_now_iso  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402,F401  （确保所有模型被注册）
from app.models import AppUser, BehaviorType, SystemConfig  # noqa: E402

SEED_DIR = ROOT / "seeds"

DEMO_USERS = [
    # username, password, real_name, role, org_code, phone
    ("admin", "Admin@12345", "系统管理员", Role.ADMIN, "XZ_STATE", "13900000001"),
    ("reviewer1", "Review@12345", "审核员一", Role.REVIEWER, "XZ_STATE", "13900000002"),
    ("reviewer2", "Review@12345", "审核员二", Role.REVIEWER, "XZ_STATE", "13900000003"),
]


def seed_behavior_types(db) -> int:
    data = json.loads((SEED_DIR / "behavior_types.json").read_text(encoding="utf-8"))
    added = 0
    for item in data:
        exists = db.query(BehaviorType).filter(BehaviorType.code == item["code"]).first()
        if exists:
            continue
        db.add(BehaviorType(**item, created_at=utc_now_iso(), updated_at=utc_now_iso()))
        added += 1
    db.commit()
    return added


def seed_system_config(db) -> int:
    added = 0
    for key, (value, group, desc) in DEFAULT_SYSTEM_CONFIG.items():
        exists = db.query(SystemConfig).filter(SystemConfig.cfg_key == key).first()
        if exists:
            continue
        db.add(SystemConfig(cfg_key=key, cfg_value=value, cfg_group=group, description=desc))
        added += 1
    db.commit()
    return added


def seed_users(db) -> int:
    added = 0
    for username, password, real_name, role, org, phone in DEMO_USERS:
        exists = db.query(AppUser).filter(AppUser.username == username).first()
        if exists:
            continue
        db.add(
            AppUser(
                username=username,
                phone=phone,
                password_hash=hash_password(password),
                real_name=real_name,
                role=role,
                org_code=org,
                status=UserStatus.ACTIVE,
            )
        )
        added += 1
    db.commit()
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="删除现有数据库文件后重建（仅演示环境）")
    args = parser.parse_args()

    if args.reset and settings.database_url.startswith("sqlite"):
        db_file = Path(settings.database_url.replace("sqlite:///", ""))
        if not db_file.is_absolute():
            db_file = ROOT / db_file
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_file) + suffix)
            if p.exists():
                p.unlink()
        print(f"[reset] 已删除 {db_file}")

    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    print("[ok] 数据表已创建")

    with SessionLocal() as db:
        n1 = seed_behavior_types(db)
        n2 = seed_system_config(db)
        n3 = seed_users(db)
        print(f"[ok] 行为类型新增 {n1} 条；系统配置新增 {n2} 条；演示账号新增 {n3} 个")

    print("\n演示账号：")
    for username, password, real_name, role, *_ in DEMO_USERS:
        print(f"  {username:<10} / {password:<12}  ({role})")
    print("\n上报人登录：手机号 + 短信验证码（Mock 环境固定验证码，见 .env 的 SMS_MOCK_CODE）")
    print("数据库文件：", settings.database_url)


if __name__ == "__main__":
    main()
