"""修改管理端账号（管理员 / 审核员）密码。

用途：本机与服务器通用。改密后账号的 token_version 会自增，
      该账号此前签发的所有登录凭证立即失效（需重新登录）。

用法：
    # 交互式随机生成强密码（推荐；密码仅打印一次，请自行记录）
    python scripts/set_password.py --username admin --generate

    # 指定新密码
    python scripts/set_password.py --username admin --password 'Xxxx#1234'

    # 批量：把三个管理端账号都换成各自的随机强密码
    python scripts/set_password.py --all --generate

安全约定：
    - 本脚本不向任何项目文件写入明文密码；
    - 随机密码由 app/core/utils.py::gen_password 生成（secrets 熵源，
      字符集已排除易混淆字符与 shell 敏感字符，可直接粘贴到终端）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.utils import gen_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AppUser  # noqa: E402
from app.services import auth_service  # noqa: E402

DEFAULT_ACCOUNTS = ("admin", "reviewer1", "reviewer2")


def change_password(username: str, password: str | None, generate: bool, length: int) -> str | None:
    """修改单个账号密码；返回实际使用的新密码（供调用方打印）。"""
    with SessionLocal() as db:
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            print(f"[skip] 账号不存在：{username}")
            return None
        new_pwd = gen_password(length) if (generate or not password) else password
        auth_service.set_password(db, user, new_pwd)
        print(f"[ok] {username}（{user.real_name or user.role}）密码已更新，旧登录凭证已失效")
        return new_pwd


def main() -> None:
    parser = argparse.ArgumentParser(description="修改管理端账号密码（本机 / 服务器通用）")
    parser.add_argument("--username", help="账号名，如 admin")
    parser.add_argument("--all", action="store_true", help=f"批量修改：{', '.join(DEFAULT_ACCOUNTS)}")
    parser.add_argument("--password", help="新密码；不传则随机生成")
    parser.add_argument("--generate", action="store_true", help="强制随机生成强密码")
    parser.add_argument("--length", type=int, default=12, help="随机密码长度（默认 12）")
    args = parser.parse_args()

    if args.all:
        targets = list(DEFAULT_ACCOUNTS)
    elif args.username:
        targets = [args.username]
    else:
        parser.error("请指定 --username 或 --all")

    if args.password and len(targets) > 1:
        parser.error("批量修改时不允许统一指定 --password（各账号应使用各自独立的随机密码）")

    results: list[tuple[str, str]] = []
    for name in targets:
        pwd = change_password(name, args.password, args.generate, args.length)
        if pwd:
            results.append((name, pwd))

    if results:
        print("\n" + "=" * 46)
        print("新密码（仅本次显示，请立即记录并妥善保管）：")
        print("=" * 46)
        for name, pwd in results:
            print(f"  {name:<12} {pwd}")
        print("=" * 46)
        print("提示：浏览器如已登录，需重新登录；旧登录凭证已全部失效。")


if __name__ == "__main__":
    main()
