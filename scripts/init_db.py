"""数据库初始化脚本：建表 + 写入种子数据（行为类型字典、系统配置）+ 管理端账号。

用法：
    python scripts/init_db.py            # 建表 + 种子（幂等，已存在则跳过）
    python scripts/init_db.py --reset    # 危险：删除现有库后重建（仅演示环境使用）

账号密码来源（2026-10-15 起不再硬编码明文）：
    优先读 .env / 环境变量（SEED_ADMIN_PASSWORD、SEED_REVIEWER1_PASSWORD、SEED_REVIEWER2_PASSWORD）；
    未配置的账号则**随机生成强密码**并在本次运行中打印一次，请立即记录。
    修改既有账号密码请用：python scripts/set_password.py --username admin --generate
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
from app.core.utils import gen_password, utc_now_iso  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402,F401  （确保所有模型被注册）
from app.models import AppUser, BehaviorType, SystemConfig  # noqa: E402

SEED_DIR = ROOT / "seeds"

# username, real_name, role, org_code, phone, settings 中的密码字段名
SEED_USERS = [
    ("admin", "系统管理员", Role.ADMIN, "XZ_STATE", "13900000001", "seed_admin_password"),
    ("reviewer1", "审核员一", Role.REVIEWER, "XZ_STATE", "13900000002", "seed_reviewer1_password"),
    ("reviewer2", "审核员二", Role.REVIEWER, "XZ_STATE", "13900000003", "seed_reviewer2_password"),
]


def migrate_sqlite_columns() -> None:
    """轻量幂等迁移：为既有 SQLite 库补充新增列（create_all 不会修改已存在的表）。"""
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "app_user" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("app_user")}
    if "email" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE app_user ADD COLUMN email VARCHAR(128)"))
        print("[ok] 迁移：app_user.email 列已补充")


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


def seed_users(db) -> tuple[int, list[tuple[str, str]]]:
    """创建管理端账号（仅账号不存在时）。

    返回 (新增数量, [(账号, 随机生成的明文密码)])——第二个元素仅包含
    「本次随机生成」的密码，供调用方打印一次；配置中显式给出的密码不回显。
    """
    added = 0
    generated: list[tuple[str, str]] = []
    for username, real_name, role, org, phone, pwd_field in SEED_USERS:
        exists = db.query(AppUser).filter(AppUser.username == username).first()
        if exists:
            continue
        configured = (getattr(settings, pwd_field, "") or "").strip()
        if configured:
            password = configured
        else:
            password = gen_password(12)
            generated.append((username, password))
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
    return added, generated


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
    migrate_sqlite_columns()
    print("[ok] 数据表已创建")

    with SessionLocal() as db:
        n1 = seed_behavior_types(db)
        n2 = seed_system_config(db)
        n3, generated = seed_users(db)
        print(f"[ok] 行为类型新增 {n1} 条；系统配置新增 {n2} 条；管理端账号新增 {n3} 个")

    if generated:
        print("\n" + "=" * 46)
        print("以下账号为本次新建，密码为随机生成（仅本次显示，请立即记录）：")
        print("=" * 46)
        for username, password in generated:
            print(f"  {username:<12} {password}")
        print("=" * 46)
        print("提示：如希望改用固定密码，可在 .env 中设置 SEED_ADMIN_PASSWORD 等变量后重建账号，")
        print("      或用 python scripts/set_password.py --username <账号> --password '<新密码>' 修改。")

    print("\n管理端账号：admin（管理员）、reviewer1 / reviewer2（审核员）")
    print("上报人登录：手机号 + 短信验证码（Mock 环境固定验证码，见 .env 的 SMS_MOCK_CODE）")
    print("数据库文件：", settings.database_url)


if __name__ == "__main__":
    main()
