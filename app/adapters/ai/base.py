"""AI 审核适配器契约（VisionAuditProvider）。

这是整个系统中最关键的“可替换边界”：
本期实现 `RuleBasedProvider`（规则占位引擎），后续接入多模态大模型（如 qwen-vl）
只需新增一个实现类并通过 AI_PROVIDER 配置切换，**上层 AuditOrchestrator 零改动**。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.constants import AiConclusion


class GeoPoint(BaseModel):
    longitude: float
    latitude: float


class AuditImage(BaseModel):
    image_id: str
    storage_key: str
    sha256: str
    width: int | None = None
    height: int | None = None
    taken_at: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    phash: str | None = None


class AuditRequest(BaseModel):
    report_id: int
    behavior_type_code: str
    behavior_type_name: str
    behavior_risk_weight: float = 0.5
    behavior_night_sensitive: bool = False
    remark: str | None = None
    address_text: str = ""
    location: GeoPoint | None = None
    location_conflict: bool = False
    shoot_time: str | None = None
    images: list[AuditImage] = Field(default_factory=list)
    # 历史重复信息（由编排器查询后注入，避免 Provider 反向依赖数据库）
    duplicate_history: int = 0


class AuditReason(BaseModel):
    code: str
    text: str
    weight: float = 0.0


class AuditResult(BaseModel):
    conclusion: AiConclusion
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons: list[AuditReason] = Field(default_factory=list)
    model_name: str
    model_version: str = "v1"
    raw: dict[str, Any] | None = None
    latency_ms: int = 0
    #: 风险分（0~1），供审核员参考与阈值配置
    risk_score: float | None = None


@runtime_checkable
class VisionAuditProvider(Protocol):
    """AI 审核提供者协议。"""

    name: str

    def audit(self, req: AuditRequest) -> AuditResult:  # pragma: no cover - 协议定义
        ...
