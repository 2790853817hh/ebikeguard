"""规则占位引擎测试：结论可解释、阈值可配置、契约稳定。"""
from __future__ import annotations

from app.adapters.ai.base import AuditImage, AuditRequest, GeoPoint
from app.adapters.ai.factory import get_ai_provider
from app.adapters.ai.rule_based import RuleBasedProvider
from app.core.constants import AiConclusion


def _req(**kwargs) -> AuditRequest:
    base = dict(
        report_id=1,
        behavior_type_code="ENTER_BUILDING",
        behavior_type_name="电动车进楼入户",
        behavior_risk_weight=0.88,
        behavior_night_sensitive=True,
        remark="楼道里拉线充电",
        address_text="某小区 3 栋 2 单元",
        location=GeoPoint(longitude=109.7, latitude=28.3),
        images=[
            AuditImage(image_id="i1", storage_key="k1", sha256="a" * 64, width=1600, height=1200, phash="ffff0000ffff0000"),
            AuditImage(image_id="i2", storage_key="k2", sha256="b" * 64, width=1600, height=1200, phash="ffff0000ffff0000"),
        ],
        shoot_time="2026-10-09T23:10:00",
    )
    base.update(kwargs)
    return AuditRequest(**base)


def test_high_risk_is_violation_with_reasons():
    result = RuleBasedProvider().audit(_req())
    assert result.conclusion == AiConclusion.VIOLATION
    assert 0.0 <= result.confidence <= 1.0
    codes = {r.code for r in result.reasons}
    assert {"BEHAVIOR_RISK", "MULTI_IMAGE", "RESOLUTION_OK", "NIGHT_TIME", "PLACEHOLDER_ENGINE"} <= codes
    assert result.risk_score is not None and result.risk_score >= 0.62
    assert result.model_name == "rule-based"
    assert result.latency_ms >= 0


def test_strong_denial_becomes_normal():
    result = RuleBasedProvider().audit(_req(remark="现场未发现违规，车辆已停放在指定区域并已整改"))
    assert result.conclusion == AiConclusion.NORMAL
    codes = {r.code for r in result.reasons}
    assert "REMARK_DENY_STRONG" in codes


def test_grey_zone_uncertain():
    result = RuleBasedProvider(violation_threshold=0.62, normal_threshold=0.34).audit(
        _req(behavior_risk_weight=0.5, behavior_night_sensitive=False, remark=None, shoot_time=None,
             location=None, images=[AuditImage(image_id="i1", storage_key="k1", sha256="a" * 64, width=800, height=600)])
    )
    assert result.conclusion == AiConclusion.UNCERTAIN
    assert result.confidence == 0.5


def test_location_conflict_and_duplicate_push_risk_down():
    high = RuleBasedProvider().audit(_req())
    low = RuleBasedProvider().audit(_req(location_conflict=True, duplicate_history=2))
    assert low.risk_score < high.risk_score
    codes = {r.code for r in low.reasons}
    assert "LOCATION_CONFLICT" in codes and "DUPLICATE_SUSPECT" in codes


def test_factory_returns_rule_provider_by_default():
    provider = get_ai_provider()
    assert provider.name == "rule-based"
    result = provider.audit(_req())
    assert result.conclusion in (AiConclusion.VIOLATION, AiConclusion.NORMAL, AiConclusion.UNCERTAIN)
