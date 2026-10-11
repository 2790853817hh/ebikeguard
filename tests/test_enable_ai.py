"""AI 启用脚本的纯逻辑测试（不触发任何外部调用）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "enable_ai.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("enable_ai", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


enable_ai = _load_module()


def test_validate_key_rejects_empty_and_placeholders():
    assert enable_ai.validate_key("")
    assert any("占位符" in p for p in enable_ai.validate_key("你的Key"))
    assert any("占位符" in p for p in enable_ai.validate_key("sk-xxxx"))
    assert any("占位符" in p for p in enable_ai.validate_key("sk-a****b"))
    assert any("长度" in p for p in enable_ai.validate_key("sk-短"))


def test_validate_key_accepts_real_looking_key():
    assert enable_ai.validate_key("sk-real1234567890abcdef12345678") == []


def test_validate_model_rejects_empty_and_spaces():
    assert enable_ai.validate_model("")
    assert enable_ai.validate_model("qwen3 vl plus")
    assert enable_ai.validate_model("qwen3-vl-plus") == []


def test_placeholder_key_never_touches_env(tmp_path, monkeypatch):
    """防呆拦截时必须原样保留 .env —— 不写坏配置的最后一道闸。"""
    env = tmp_path / ".env"
    env.write_text("AI_PROVIDER=rule\nDASHSCOPE_API_KEY=\n", encoding="utf-8", newline="\n")
    before = env.read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["enable_ai.py", "--key", "sk-xxxx", "--env-file", str(env)])
    assert enable_ai.main() == 1
    assert env.read_text(encoding="utf-8") == before
    assert not (tmp_path / ".env.bak").exists()


@pytest.fixture()
def _restore_ai_settings():
    """main() 会 setattr 全局 settings，测后恢复避免污染其它用例。"""
    from app.config import settings

    snapshot = {
        "ai_provider": settings.ai_provider,
        "dashscope_api_key": settings.dashscope_api_key,
        "ai_vl_model": settings.ai_vl_model,
        "ai_vl_base_url": settings.ai_vl_base_url,
    }
    yield
    for key, value in snapshot.items():
        setattr(settings, key, value)


def test_no_probe_writes_config_and_backs_up(tmp_path, monkeypatch, _restore_ai_settings):
    env = tmp_path / ".env"
    env.write_text("AI_PROVIDER=rule\nDASHSCOPE_API_KEY=\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "enable_ai.py",
            "--key",
            "sk-real1234567890abcdef",
            "--model",
            "qwen3-vl-flash",
            "--no-probe",
            "--env-file",
            str(env),
        ],
    )
    assert enable_ai.main() == 0
    text = env.read_text(encoding="utf-8")
    assert "AI_PROVIDER=qwenvl" in text
    assert "DASHSCOPE_API_KEY=sk-real1234567890abcdef" in text
    assert "AI_VL_MODEL=qwen3-vl-flash" in text
    assert (tmp_path / ".env.bak").exists()


def test_no_probe_updates_existing_keys_in_place(tmp_path, monkeypatch, _restore_ai_settings):
    env = tmp_path / ".env"
    env.write_text(
        "AI_PROVIDER=rule\nDASHSCOPE_API_KEY=old\nAI_VL_MODEL=qwen3-vl-plus\n# 注释保留\nCORS_ORIGINS=*\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["enable_ai.py", "--key", "sk-new1234567890abcdef", "--no-probe", "--env-file", str(env)],
    )
    assert enable_ai.main() == 0
    text = env.read_text(encoding="utf-8")
    assert text.count("AI_PROVIDER=") == 1
    assert "AI_PROVIDER=qwenvl" in text
    assert "DASHSCOPE_API_KEY=sk-new1234567890abcdef" in text
    assert "# 注释保留" in text
    assert "CORS_ORIGINS=*" in text
