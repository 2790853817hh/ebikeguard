"""规则占位引擎（RuleBasedProvider）。

本期不进行真正的图像内容识别，而是依据「结构化元数据」给出可解释的初审建议：
行为类型固有风险权重、图片数量与分辨率、EXIF 拍摄时间、定位一致性、
备注关键词、历史相似上报等。给出 risk_score、结论与逐条理由。

⚠️ 设计要点：本引擎的结论**只作为审核员的参考建议**（保守档策略下，
"疑似违规"一律进入人工队列），因此即使全部判为 VIOLATION 也不会造成误裁决。
接入真实多模态大模型后，保持同一 AuditResult 契约即可无缝替换。
"""
from __future__ import annotations

import re
import time

from app.adapters.ai.base import AuditReason, AuditRequest, AuditResult
from app.core.constants import AiConclusion

# 倾向“确实存在违规”的关键词
_POSITIVE_KEYWORDS = (
    "充电", "拉线", "飞线", "入户", "上楼", "进楼", "进电梯", "楼道", "楼梯间",
    "堵塞", "占用", "停放", "电池", "推车", "门厅", "地下车库", "私接",
)
# 强烈否定（用户自述无违规 / 已整改）
_STRONG_NEGATIVE = ("未发现", "没有发现", "不是违规", "不属实", "已整改", "已清理", "正常停放", "合规")
# 一般否定
_MILD_NEGATIVE = ("无违规", "未见", "无异常", "正常")

NIGHT_HOURS = {22, 23, 0, 1, 2, 3, 4, 5}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _parse_hour(shoot_time: str | None) -> int | None:
    if not shoot_time:
        return None
    m = re.search(r"[T ](\d{2}):", shoot_time)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


class RuleBasedProvider:
    """规则占位实现：无外部依赖、毫秒级返回、结论可解释。"""

    name = "rule-based"
    version = "v1"

    def __init__(self, violation_threshold: float = 0.62, normal_threshold: float = 0.34) -> None:
        self.violation_threshold = violation_threshold
        self.normal_threshold = normal_threshold

    def audit(self, req: AuditRequest) -> AuditResult:
        started = time.perf_counter()
        reasons: list[AuditReason] = []

        # ---- 1. 行为类型固有风险 ----
        risk = _clamp(req.behavior_risk_weight)
        reasons.append(
            AuditReason(
                code="BEHAVIOR_RISK",
                text=f"行为类型「{req.behavior_type_name}」的固有风险权重为 {req.behavior_risk_weight:.2f}",
                weight=round(req.behavior_risk_weight, 3),
            )
        )

        # ---- 2. 图片数量 ----
        n = len(req.images)
        if n >= 2:
            risk += 0.05
            reasons.append(AuditReason(code="MULTI_IMAGE", text=f"提供了 {n} 张现场照片，信息更充分", weight=0.05))
        elif n == 1:
            reasons.append(AuditReason(code="SINGLE_IMAGE", text="仅提供 1 张现场照片", weight=0.0))
        else:
            risk -= 0.20
            reasons.append(AuditReason(code="NO_IMAGE", text="未提供现场照片，无法核实", weight=-0.20))

        # ---- 3. 分辨率 ----
        widths = [i.width for i in req.images if i.width]
        if widths:
            max_w = max(widths)
            if max_w >= 1280:
                risk += 0.05
                reasons.append(AuditReason(code="RESOLUTION_OK", text=f"图片分辨率较高（{max_w}px），可用于核实", weight=0.05))
            elif max_w < 640:
                risk -= 0.10
                reasons.append(AuditReason(code="RESOLUTION_LOW", text=f"图片分辨率偏低（{max_w}px），细节不易辨认", weight=-0.10))

        # ---- 4. 拍摄时间（夜间高发行为） ----
        hour = _parse_hour(req.shoot_time)
        if hour is not None and req.behavior_night_sensitive and hour in NIGHT_HOURS:
            risk += 0.08
            reasons.append(
                AuditReason(code="NIGHT_TIME", text=f"拍摄时间为 {hour:02d} 时，属该类行为的高发时段", weight=0.08)
            )

        # ---- 5. 备注关键词 ----
        remark = (req.remark or "").strip()
        if remark:
            if any(k in remark for k in _STRONG_NEGATIVE):
                risk -= 0.90
                reasons.append(
                    AuditReason(
                        code="REMARK_DENY_STRONG",
                        text="上报人备注中明确说明该情况不构成违规或已整改，风险分大幅下调",
                        weight=-0.90,
                    )
                )
            elif any(k in remark for k in _MILD_NEGATIVE):
                risk -= 0.25
                reasons.append(AuditReason(code="REMARK_DENY_MILD", text="上报人备注中描述了较为中性/正常的情况", weight=-0.25))
            hits = [k for k in _POSITIVE_KEYWORDS if k in remark]
            if hits:
                bonus = min(0.12, 0.06 * len(hits))
                risk += bonus
                reasons.append(
                    AuditReason(
                        code="REMARK_DESCRIBE",
                        text=f"备注描述了具体情形（{'、'.join(hits[:4])}），与所选行为类型相符",
                        weight=round(bonus, 3),
                    )
                )

        # ---- 6. 定位一致性 ----
        if req.location_conflict:
            risk -= 0.10
            reasons.append(
                AuditReason(code="LOCATION_CONFLICT", text="浏览器定位与照片 EXIF 位置不一致，需人工核实", weight=-0.10)
            )
        if req.location is None and not any(i.gps_lat for i in req.images):
            risk -= 0.05
            reasons.append(AuditReason(code="NO_LOCATION", text="未获取到定位信息，地点依赖文字描述", weight=-0.05))

        # ---- 7. 历史相似上报 ----
        if req.duplicate_history > 0:
            risk -= 0.15
            reasons.append(
                AuditReason(
                    code="DUPLICATE_SUSPECT",
                    text=f"检测到 {req.duplicate_history} 条历史相似照片上报，存在重复上报可能",
                    weight=-0.15,
                )
            )

        # ---- 8. 引擎能力声明（对审核员诚实告知） ----
        reasons.append(
            AuditReason(
                code="PLACEHOLDER_ENGINE",
                text="本期为规则占位引擎，尚未进行图像内容识别；接入视觉大模型后结论精度将显著提升",
                weight=0.0,
            )
        )

        risk = round(_clamp(risk), 4)

        # ---- 结论与置信度 ----
        if risk >= self.violation_threshold:
            conclusion = AiConclusion.VIOLATION
            confidence = round(min(0.95, 0.45 + risk * 0.5), 3)
        elif risk <= self.normal_threshold:
            conclusion = AiConclusion.NORMAL
            confidence = round(min(0.95, 0.45 + (1 - risk) * 0.5), 3)
        else:
            conclusion = AiConclusion.UNCERTAIN
            confidence = 0.5
            reasons.append(
                AuditReason(
                    code="NEEDS_HUMAN_REVIEW",
                    text="风险分处于灰区，建议人工重点核实",
                    weight=0.0,
                )
            )

        latency_ms = int((time.perf_counter() - started) * 1000)

        return AuditResult(
            conclusion=conclusion,
            confidence=confidence,
            reasons=reasons,
            model_name="rule-based",
            model_version=self.version,
            raw={
                "risk_score": risk,
                "violation_threshold": self.violation_threshold,
                "normal_threshold": self.normal_threshold,
                "image_count": n,
                "signals": {r.code: r.weight for r in reasons},
            },
            latency_ms=latency_ms,
            risk_score=risk,
        )
