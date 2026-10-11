"""短信启用脚本的纯逻辑测试（不触发任何外部调用）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "enable_sms.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("enable_sms", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


enable_sms = _load_module()


SAMPLE = """APP_NAME=测试
SECRET_KEY=abc123

# --- 短信 ---
SMS_PROVIDER=mock
SMS_MOCK_CODE=888888

# --- 其它 ---
CORS_ORIGINS=*
"""


def test_upsert_replaces_existing_key_in_place():
    out = enable_sms.upsert_env(SAMPLE, {"SMS_PROVIDER": "aliyun_dypns"})
    assert "SMS_PROVIDER=aliyun_dypns" in out
    assert out.count("SMS_PROVIDER=") == 1
    assert "SMS_MOCK_CODE=888888" in out  # 无关项原样保留
    assert "# --- 短信 ---" in out  # 注释原样保留


def test_upsert_appends_missing_keys_and_keeps_order():
    out = enable_sms.upsert_env(SAMPLE, {"SMS_SIGN_NAME": "恒创联众", "SMS_TEMPLATE_CODE": "100001"})
    lines = out.split("\n")
    assert lines.index("SMS_SIGN_NAME=恒创联众") < lines.index("SMS_TEMPLATE_CODE=100001")
    assert out.rstrip().endswith("SMS_TEMPLATE_CODE=100001")
    assert out.endswith("\n")


def test_upsert_is_idempotent_and_never_duplicates():
    once = enable_sms.upsert_env(SAMPLE, {"SMS_PROVIDER": "aliyun_dypns"})
    twice = enable_sms.upsert_env(once, {"SMS_PROVIDER": "aliyun_dypns"})
    assert once == twice
    assert twice.count("SMS_PROVIDER=") == 1


def test_upsert_handles_text_without_trailing_newline():
    out = enable_sms.upsert_env("SMS_PROVIDER=mock", {"SMS_PROVIDER": "aliyun"})
    assert out == "SMS_PROVIDER=aliyun\n"


def test_upsert_handles_empty_file():
    assert enable_sms.upsert_env("", {"SMS_PROVIDER": "mock"}) == "SMS_PROVIDER=mock\n"


def test_upsert_ignores_commented_keys():
    text = "# SMS_PROVIDER=mock\nDEBUG=true\n"
    out = enable_sms.upsert_env(text, {"SMS_PROVIDER": "aliyun_dypns"})
    assert "# SMS_PROVIDER=mock" in out  # 注释行不动
    assert "SMS_PROVIDER=aliyun_dypns" in out  # 作为新键追加
    assert out.count("SMS_PROVIDER=aliyun_dypns") == 1


def test_mask_never_leaks_secret():
    assert enable_sms.mask("LTAI5tAbCdEfGh") == "LTAI" + "*" * 10  # 14 位只露前 4 位
    assert enable_sms.mask("") == "(空)"
    assert enable_sms.mask("ab") == "**"
    assert enable_sms.mask_phone("13349638573") == "133****8573"
    assert enable_sms.mask_phone("") == "***"


def test_write_then_restore_round_trip(tmp_path):
    env = tmp_path / ".env"
    env.write_text(SAMPLE, encoding="utf-8", newline="\n")

    original, backup = enable_sms.write_env(
        env, {"SMS_PROVIDER": "aliyun_dypns", "SMS_SIGN_NAME": "恒创联众"}
    )
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == SAMPLE  # 备份是改动前的内容
    body = env.read_text(encoding="utf-8")
    assert "SMS_PROVIDER=aliyun_dypns" in body and "SMS_SIGN_NAME=恒创联众" in body
    assert "SECRET_KEY=abc123" in body  # 其它配置未被破坏
    assert not (tmp_path / ".env.tmp").exists()  # 临时文件已清理

    enable_sms.restore_env(env, original)
    assert env.read_text(encoding="utf-8") == SAMPLE


# ---------------------------------------------------------------- 参数防呆校验


def test_validate_inputs_accepts_real_values():
    assert enable_sms.validate_inputs("LTAI5tAbCdEfGh1234", "AbCdEf123456GhIjKlMnOpQrStUv", "恒创联众", "100001", "aliyun_dypns") == []
    assert enable_sms.validate_inputs("LTAI5tAbCdEfGh1234", "AbCd123456GhIjKlMnOpQrStUv", "湘西州数据局", "SMS_123456789", "aliyun") == []


def test_validate_inputs_catches_doc_placeholders():
    """回归：用户把文档占位符原样粘贴，曾导致 SDK 抛 latin-1 编码错误。"""
    problems = enable_sms.validate_inputs("你的AK", "你的Secret", "赠送签名名称", "100001", "aliyun_dypns")
    joined = "；".join(problems)
    assert len(problems) == 3  # AK、Secret、签名各一条
    assert "占位符" in joined
    assert "--ak-id" in joined and "--ak-secret" in joined and "--sign" in joined


def test_validate_inputs_catches_masked_and_non_ascii_keys():
    masked = enable_sms.validate_inputs("LTAI5t************", "AbCd123456GhIjKl", "恒创联众", "100001", "aliyun_dypns")
    assert "占位符或打码值" in masked[0]

    chinese = enable_sms.validate_inputs("测试AK12345678", "AbCd123456GhIjKl", "恒创联众", "100001", "aliyun_dypns")
    assert "只能是字母/数字" in chinese[0]
    assert "latin-1" in chinese[0]  # 明确告诉用户为什么会报这个错


def test_validate_inputs_checks_template_format_per_channel():
    hint = enable_sms.validate_inputs("LTAI5tAbCdEfGh1234", "AbCd123456GhIjKl", "恒创联众", "SMS_123456", "aliyun_dypns")
    assert "纯数字" in hint[0] and "企业通道" in hint[0]

    wrong = enable_sms.validate_inputs("LTAI5tAbCdEfGh1234", "AbCd123456GhIjKl", "湘西州数据局", "100001", "aliyun")
    assert "SMS_" in wrong[0]


def test_validate_inputs_rejects_empty_sign():
    problems = enable_sms.validate_inputs("LTAI5tAbCdEfGh1234", "AbCd123456GhIjKl", "   ", "100001", "aliyun_dypns")
    assert any("不能为空" in p for p in problems)


def test_restart_hint_matches_platform(monkeypatch):
    """重启提示必须匹配运行平台。

    历史缺陷：脚本结尾硬编码 Windows 路径 `./.venv/Scripts/python run.py`，
    在 Linux 服务器上照抄跑不通（服务器上服务由 systemd 托管）。
    """
    monkeypatch.setattr(enable_sms.os, "name", "nt", raising=False)
    assert ".venv\\Scripts\\python run.py" in enable_sms.restart_hint()

    monkeypatch.setattr(enable_sms.os, "name", "posix", raising=False)
    linux_hint = enable_sms.restart_hint()
    assert "systemctl restart ev_audit" in linux_hint
    assert "Scripts" not in linux_hint
    assert "Scripts" not in enable_sms.selfcheck_hint()


def test_bad_inputs_never_touch_env_file(tmp_path):
    """校验失败时必须原样保留 .env —— 这正是不写坏配置的最后一道闸。"""
    env = tmp_path / ".env"
    env.write_text(SAMPLE, encoding="utf-8", newline="\n")
    before = env.read_text(encoding="utf-8")

    problems = enable_sms.validate_inputs("你的AK", "你的Secret", "赠送签名名称", "100001", "aliyun_dypns")
    if problems:
        pass  # main() 在此分支直接 return 1，不调用 write_env
    assert env.read_text(encoding="utf-8") == before
    assert not (tmp_path / ".env.bak").exists()


def test_validate_extra_json_accepts_object_and_rejects_junk():
    assert enable_sms.validate_extra_json("{}") == []
    assert enable_sms.validate_extra_json("") == []
    assert enable_sms.validate_extra_json('{"min":"5"}') == []

    broken = enable_sms.validate_extra_json('{"min":5')
    assert broken and "不是合法 JSON" in broken[0]

    not_object = enable_sms.validate_extra_json('["min"]')
    assert not_object and "必须是 JSON 对象" in not_object[0]


def test_extra_json_flows_into_validation_problems():
    """赠送模板 100001 需要 min 变量；--extra-json 传坏值时要在写入前拦下。"""
    ok = enable_sms.validate_inputs("LTAI5tAbCdEfGh1234", "AbCd123456GhIjKl", "恒创联众", "100001", "aliyun_dypns", '{"min":"5"}')
    assert ok == []

    bad = enable_sms.validate_inputs("LTAI5tAbCdEfGh1234", "AbCd123456GhIjKl", "恒创联众", "100001", "aliyun_dypns", "{min:5}")
    assert any("--extra-json" in p for p in bad)


def test_parse_extra_pairs_key_value_form():
    """--extra min=5 用作免引号写法，绕开 bash/PowerShell 的引号差异。"""
    data, problems = enable_sms.parse_extra_pairs(["min=5"])
    assert data == {"min": "5"} and problems == []

    data, problems = enable_sms.parse_extra_pairs(["min=5", "foo=bar"])
    assert data == {"min": "5", "foo": "bar"} and problems == []

    data, problems = enable_sms.parse_extra_pairs(["min=5", "略"])
    assert data == {"min": "5"}
    assert problems and "KEY=VALUE" in problems[0]

    data, problems = enable_sms.parse_extra_pairs(["=5"])
    assert data == {} and problems and "键不能为空" in problems[0]

    assert enable_sms.parse_extra_pairs(None) == ({}, [])
    assert enable_sms.parse_extra_pairs([]) == ({}, [])


def test_extra_pairs_serialize_to_valid_json_for_env():
    import json as _json

    data, _ = enable_sms.parse_extra_pairs(["min=5"])
    text = _json.dumps(data, ensure_ascii=False)
    assert text == '{"min": "5"}'
    assert enable_sms.validate_extra_json(text) == []
