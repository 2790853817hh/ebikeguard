"""AI Provider 工厂：按配置选择实现。"""
from __future__ import annotations

from functools import lru_cache

from app.adapters.ai.base import AuditRequest, AuditResult, VisionAuditProvider
from app.adapters.ai.qwen_vl import QwenVLProvider
from app.adapters.ai.rule_based import RuleBasedProvider
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_FACTORY_PROVIDER_OVERRIDE: str | None = None


def get_ai_provider() -> VisionAuditProvider:
    """返回当前生效的 AI 审核提供者。"""
    name = (_FACTORY_PROVIDER_OVERRIDE or settings.ai_provider or "rule").lower()
    if name == "qwenvl":
        logger.info("AI 审核提供者：QwenVLProvider（model=%s）", settings.ai_vl_model)
        return QwenVLProvider(api_key=settings.dashscope_api_key)

    # 默认：规则占位引擎（阈值来自配置服务）
    from app.services.config_service import get_config_float

    provider = RuleBasedProvider(
        violation_threshold=get_config_float("ai.violation_threshold", 0.62),
        normal_threshold=get_config_float("ai.normal_threshold", 0.34),
    )
    logger.debug("AI 审核提供者：RuleBasedProvider")
    return provider


def set_provider_override(name: str | None) -> None:
    """测试或运行时切换提供者。"""
    global _FACTORY_PROVIDER_OVERRIDE
    _FACTORY_PROVIDER_OVERRIDE = name


__all__ = ["get_ai_provider", "set_provider_override", "VisionAuditProvider", "AuditRequest", "AuditResult"]
