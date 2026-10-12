"""enable_notify.py 单测（离线）：参数防呆、复用 enable_sms 工具、绝不触碰真 .env。

沿用 enable_sms / enable_ai 的测试风格：importlib 直接加载脚本模块，纯函数级断言，
所有写入都指向临时文件；涉及的全局 settings 属性用夹具快照/复原。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


enable_notify = _load("enable_notify_script", "enable_notify.py")


@pytest.fixture(autouse=True)
def _restore_settings():
    keys = [
        "email_provider", "smtp_host", "smtp_port", "smtp_security", "smtp_username",
        "smtp_password", "smtp_from", "notify_ops_email", "webhook_kind", "webhook_url",
    ]
    snapshot = {k: getattr(settings, k) for k in keys}
    yield
    for key, value in snapshot.items():
        setattr(settings, key, value)


# ---------------------------------------------------------------- 复用与校验


def test_reuses_enable_sms_utilities_by_import():
    source = (SCRIPTS / "enable_notify.py").read_text(encoding="utf-8")
    assert "from enable_sms import" in source
    # 复用来的工具函数在模块内可直接使用
    assert enable_notify.mask("abcdef123456") == "abcd" + "*" * 8


def test_validate_email_inputs():
    good = enable_notify.validate_email_inputs(
        "smtp.189.cn", 465, "ssl", "user@189.cn", "AbCdEfGh1234", "to@example.com"
    )
    assert good == []

    bad = enable_notify.validate_email_inputs("你的SMTP", "abc", "plain", "user", "授权码", "not-an-email")
    joined = "；".join(bad)
    assert "占位符" in joined
    assert "--smtp-port" in joined
    assert "--smtp-security" in joined
    assert "--smtp-user" in joined
    assert "授权码" in joined
    assert "--to" in joined

    assert any("不能为空" in p for p in enable_notify.validate_email_inputs("", 465, "ssl", "", "", ""))


def test_validate_webhook_inputs():
    ok1 = enable_notify.validate_webhook_inputs(
        "wecom", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123456"
    )
    assert ok1 == []
    ok2 = enable_notify.validate_webhook_inputs(
        "dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=abc123456"
    )
    assert ok2 == []

    joined = "；".join(enable_notify.validate_webhook_inputs("slack", "http://x.cn/hook"))
    assert "wecom" in joined and "https" in joined

    joined2 = "；".join(
        enable_notify.validate_webhook_inputs("wecom", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send")
    )
    assert "key=" in joined2


# ---------------------------------------------------------------- 主流程（临时 .env）


def test_bad_inputs_never_touch_env_file(tmp_path, monkeypatch):
    """校验失败时必须原样保留 .env —— 写入前的最后一道闸。"""
    env = tmp_path / ".env"
    original = "SMS_PROVIDER=mock\n"
    env.write_text(original, encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "enable_notify.py", "--mode", "email",
            "--smtp-host", "你的SMTP", "--smtp-user", "u@x.cn", "--smtp-pass", "授权码",
            "--env-file", str(env),
        ],
    )
    assert enable_notify.main() == 1
    assert env.read_text(encoding="utf-8") == original
    assert not (tmp_path / ".env.bak").exists()


def test_webhook_write_and_static_check_masks_secret(tmp_path, monkeypatch, capsys):
    """合法 webhook 参数 + --no-probe：写入 .env、输出脱敏、不产生真实发送。"""
    env = tmp_path / ".env"
    env.write_text("SMS_PROVIDER=mock\n", encoding="utf-8", newline="\n")
    url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc1234567890"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "enable_notify.py", "--mode", "webhook", "--webhook-kind", "wecom",
            "--webhook-url", url, "--no-probe", "--env-file", str(env),
        ],
    )
    assert enable_notify.main() == 0

    text = env.read_text(encoding="utf-8")
    assert f"WEBHOOK_URL={url}" in text
    assert "WEBHOOK_KIND=wecom" in text
    assert (tmp_path / ".env.bak").exists()

    out = capsys.readouterr().out
    assert "abc1234567890" not in out  # 密钥只允许露尾部 4 位
    assert "7890" in out


def test_email_write_hot_updates_settings(tmp_path, monkeypatch):
    """合法邮件参数 + --no-probe：写入成功并热更新当前进程 settings。"""
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "enable_notify.py", "--mode", "email",
            "--smtp-host", "smtp.189.cn", "--smtp-user", "u@189.cn", "--smtp-pass", "AbCdEf123456",
            "--smtp-from", "notice@189.cn", "--ops-email", "ops@189.cn",
            "--no-probe", "--env-file", str(env),
        ],
    )
    assert enable_notify.main() == 0
    assert settings.email_provider == "smtp"
    assert settings.smtp_host == "smtp.189.cn"
    assert settings.notify_ops_email == "ops@189.cn"
    text = env.read_text(encoding="utf-8")
    assert "SMTP_PASSWORD=AbCdEf123456" in text
    assert "NOTIFY_OPS_EMAIL=ops@189.cn" in text
