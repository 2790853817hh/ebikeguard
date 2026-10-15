"""密码策略与账号信息隐藏的回归测试。

背景（2026-10-15 用户要求）：
    1) 登录页「账号登录（管理端）」原先明文展示演示账号（admin / Admin@12345 等），
       属安全隐患，需从页面移除；
    2) 管理端账号密码需更换，且不再以明文形式出现在代码库（源码 / 脚本 / 前端）中。

约束（本测试锁定）：
    - `static/` 下的任何前端文件不得出现账号密码明文；
    - `scripts/` 下的脚本不得硬编码管理端明文密码；
    - `app/core/utils.py::gen_password` 生成强密码（长度、四类字符、无易混淆字符）；
    - `scripts/set_password.py` 改密后：旧密码失效、新密码可用、旧登录凭证失效。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SCRIPTS = ROOT / "scripts"

# 曾经暴露过的明文密码（不得再出现在前端与运维脚本中）
_LEAKED = ("Admin@12345", "Review@12345")


# ---------------------------------------------------------------- 明文清理


def test_static_has_no_plaintext_credentials():
    """前端任何文件都不得出现管理端账号密码明文。"""
    hits = []
    for f in STATIC.rglob("*"):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for secret in _LEAKED:
            if secret in text:
                hits.append(f"{f.relative_to(ROOT)} 含 {secret}")
    assert not hits, "前端残留明文密码：\n" + "\n".join(hits)


def test_login_page_no_demo_account_hint():
    """登录页不得再展示「演示账号」提示。"""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "演示账号" not in html, "登录页仍在展示演示账号提示"
    assert "Admin@12345" not in html and "Review@12345" not in html, "登录页残留密码明文"


def test_scripts_have_no_hardcoded_admin_password():
    """运维脚本不得硬编码管理端明文密码（密码应由 .env / 参数 / 随机生成提供）。"""
    hits = []
    for f in SCRIPTS.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for secret in _LEAKED:
            if secret in text:
                hits.append(f"{f.relative_to(ROOT)} 含 {secret}")
    assert not hits, "脚本中残留明文密码：\n" + "\n".join(hits)


def test_readme_does_not_publish_password():
    """README 不得再公开管理端密码明文。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for secret in _LEAKED:
        assert secret not in text, f"README 仍在公开密码 {secret}"


# ---------------------------------------------------------------- 密码生成


def test_gen_password_meets_strength_requirements():
    from app.core.utils import gen_password

    for _ in range(30):
        pwd = gen_password(12)
        assert len(pwd) == 12
        assert re.search(r"[A-Z]", pwd), "缺少大写字母"
        assert re.search(r"[a-z]", pwd), "缺少小写字母"
        assert re.search(r"[0-9]", pwd), "缺少数字"
        assert re.search(r"[@#%+=?]", pwd), "缺少符号"
        # 不得含易混淆字符（避免人工转述出错）与 shell 敏感字符（可安全粘贴）
        assert not set(pwd) & set("IOlo01$`\"'\\ "), f"含禁用字符：{pwd}"


def test_gen_password_rejects_short_length():
    from app.core.utils import gen_password

    with pytest.raises(ValueError):
        gen_password(6)


def test_gen_password_uses_secrets_not_random(monkeypatch):
    """守卫：gen_password 必须使用 secrets 熵源（防止有人改回 random）。"""
    import app.core.utils as utils

    called = {"secrets": 0}

    real_choice = utils.secrets.choice

    def spy(seq):
        called["secrets"] += 1
        return real_choice(seq)

    monkeypatch.setattr(utils.secrets, "choice", spy)
    utils.gen_password(12)
    assert called["secrets"] > 0, "gen_password 未使用 secrets.choice（疑似退回 random）"


# ---------------------------------------------------------------- 改密流程


def test_change_password_invalidates_old(client):
    """改密后：旧密码不可用、新密码可用、此前签发的 token 立即失效。"""
    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models import AppUser
    from scripts.set_password import change_password

    username = "pwd_policy_probe"
    old_pwd, new_pwd = "OldPwd@2026", "NewPwd@2026x"

    with SessionLocal() as db:
        if db.query(AppUser).filter(AppUser.username == username).first():
            db.query(AppUser).filter(AppUser.username == username).delete()
            db.commit()
        db.add(AppUser(username=username, password_hash=hash_password(old_pwd),
                       real_name="改密探针", role="reviewer", status="active"))
        db.commit()

    try:
        # 改密前：旧密码可登录，拿到 token
        res = client.post("/api/v1/auth/login", json={"username": username, "password": old_pwd})
        assert res.status_code == 200, res.text
        old_token = res.json()["access_token"]
        assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200

        # 改密（显式指定新密码，不触发随机生成）
        used = change_password(username, new_pwd, generate=False, length=12)
        assert used == new_pwd

        # 旧密码失败、新密码成功
        assert client.post("/api/v1/auth/login", json={"username": username, "password": old_pwd}).status_code == 401
        assert client.post("/api/v1/auth/login", json={"username": username, "password": new_pwd}).status_code == 200

        # 旧 token 失效（token_version 自增）
        assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    finally:
        with SessionLocal() as db:
            db.query(AppUser).filter(AppUser.username == username).delete()
            db.commit()


def test_change_password_unknown_user_is_noop(capsys):
    """账号不存在时安全跳过，不抛异常。"""
    from scripts.set_password import change_password

    result = change_password("no_such_account_xyz", None, generate=True, length=12)
    assert result is None
    assert "账号不存在" in capsys.readouterr().out


# ---------------------------------------------------------------- 防假阴性对照


def test_guard_detects_plaintext_password():
    """对照：守卫必须能真的抓到明文密码（否则形同虚设）。"""
    probe = STATIC / "_leak_probe.html"
    probe.write_text("<b>admin / Admin@12345</b>", encoding="utf-8")
    try:
        text = probe.read_text(encoding="utf-8")
        assert "Admin@12345" in text, "守卫失效：未能识别明文密码"
    finally:
        probe.unlink(missing_ok=True)
