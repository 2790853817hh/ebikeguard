"""AI 验收脚本的纯逻辑测试（不触发任何外部调用）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_ai_images.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_ai_images", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_ai_images = _load_module()


def test_parse_expectation_detects_negative_samples():
    assert check_ai_images.parse_expectation("08_NORMAL_规范停放负样本.png") == "NORMAL"
    assert check_ai_images.parse_expectation("12_negative_case.jpg") == "NORMAL"
    assert check_ai_images.parse_expectation("09_规范停放.png") == "NORMAL"
    assert check_ai_images.parse_expectation("10_负样本.png") == "NORMAL"


def test_parse_expectation_defaults_to_violation():
    assert check_ai_images.parse_expectation("01_ENTER_BUILDING_进楼入户.png") == "VIOLATION"
    assert check_ai_images.parse_expectation("03_FLYING_WIRE_CHARGING_飞线充电.png") == "VIOLATION"
    assert check_ai_images.parse_expectation("photo.jpg") == "VIOLATION"


def test_classify_hit_uncertain_miss():
    # 期望违规
    assert check_ai_images.classify("VIOLATION", "VIOLATION") == "hit"
    assert check_ai_images.classify("VIOLATION", "UNCERTAIN") == "uncertain"
    assert check_ai_images.classify("VIOLATION", "NORMAL") == "miss"
    # 期望正常（负样本）：UNCERTAIN 不算错，单独归类
    assert check_ai_images.classify("NORMAL", "NORMAL") == "hit"
    assert check_ai_images.classify("NORMAL", "UNCERTAIN") == "uncertain"
    assert check_ai_images.classify("NORMAL", "VIOLATION") == "miss"


def test_classify_is_case_insensitive_and_tolerates_empty():
    assert check_ai_images.classify("VIOLATION", "violation") == "hit"
    assert check_ai_images.classify("VIOLATION", "") == "miss"
    assert check_ai_images.classify("VIOLATION", None) == "miss"
