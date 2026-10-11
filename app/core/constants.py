"""全局枚举与常量：角色、上报状态、审核动作、驳回原因、通知事件。"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    REPORTER = "reporter"  # 上报人（市民 / 巡查员）
    REVIEWER = "reviewer"  # 审核员
    ADMIN = "admin"  # 管理员


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    LOCKED = "locked"


class ReportStatus(StrEnum):
    """上报记录状态（审核状态机的节点）。"""

    SUBMITTED = "SUBMITTED"  # 已提交，待 AI 审核
    PENDING_AI = "PENDING_AI"  # AI 审核中
    AI_DONE_PENDING_HUMAN = "AI_DONE_PENDING_HUMAN"  # AI 已审，待人工
    HUMAN_REVIEWING = "HUMAN_REVIEWING"  # 人工审核中（已认领）
    NEED_SUPPLEMENT = "NEED_SUPPLEMENT"  # 退回补充材料
    APPROVED = "APPROVED"  # 审核通过（违规成立，转处置）
    REJECTED = "REJECTED"  # 审核不通过（不构成违规）
    ESCALATED = "ESCALATED"  # 疑难升级，待双人复核
    APPEALING = "APPEALING"  # 申诉中
    ARCHIVED = "ARCHIVED"  # 已归档
    WITHDRAWN = "WITHDRAWN"  # 上报人撤回
    CANCELLED = "CANCELLED"  # 作废


#: 终态（不再流转，除申诉 / 归档动作外）
TERMINAL_STATUSES = {ReportStatus.ARCHIVED, ReportStatus.WITHDRAWN, ReportStatus.CANCELLED}

#: 可发起申诉的状态
APPEALABLE_STATUSES = {ReportStatus.APPROVED, ReportStatus.REJECTED}

REPORT_STATUS_LABEL: dict[str, str] = {
    ReportStatus.SUBMITTED: "已提交",
    ReportStatus.PENDING_AI: "AI 审核中",
    ReportStatus.AI_DONE_PENDING_HUMAN: "待人工审核",
    ReportStatus.HUMAN_REVIEWING: "人工审核中",
    ReportStatus.NEED_SUPPLEMENT: "待补充材料",
    ReportStatus.APPROVED: "审核通过",
    ReportStatus.REJECTED: "审核不通过",
    ReportStatus.ESCALATED: "疑难复核中",
    ReportStatus.APPEALING: "申诉处理中",
    ReportStatus.ARCHIVED: "已归档",
    ReportStatus.WITHDRAWN: "已撤回",
    ReportStatus.CANCELLED: "已作废",
}


class AuditAction(StrEnum):
    """状态机动作（唯一合法的流转入口）。"""

    START_AI = "START_AI"  # 系统：启动 AI 审核
    AI_DONE = "AI_DONE"  # 系统：AI 审核完成
    CLAIM = "CLAIM"  # 审核员：认领任务
    APPROVE = "APPROVE"  # 审核员：通过（违规成立）
    REJECT = "REJECT"  # 审核员：不通过
    NEED_SUPPLEMENT = "NEED_SUPPLEMENT"  # 审核员：退回补充
    ESCALATE = "ESCALATE"  # 审核员：疑难升级
    SUPPLEMENT = "SUPPLEMENT"  # 上报人：补充材料
    SECOND_APPROVE = "SECOND_APPROVE"  # 复核人：复核通过
    SECOND_REJECT = "SECOND_REJECT"  # 复核人：复核不通过
    APPEAL = "APPEAL"  # 上报人：发起申诉
    APPEAL_RESOLVE = "APPEAL_RESOLVE"  # 管理员：申诉复议
    ARCHIVE = "ARCHIVE"  # 系统/管理员：归档
    WITHDRAW = "WITHDRAW"  # 上报人：撤回
    CANCEL = "CANCEL"  # 管理员：作废
    SLA_REJECT = "SLA_REJECT"  # 系统：超期未补充，自动不通过


class AiConclusion(StrEnum):
    VIOLATION = "VIOLATION"  # 疑似违规
    NORMAL = "NORMAL"  # 疑似正常
    UNCERTAIN = "UNCERTAIN"  # 无法判定


class ActorType(StrEnum):
    USER = "user"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    SYSTEM = "system"


class NotificationEvent(StrEnum):
    SUBMITTED = "SUBMITTED"
    AI_DONE = "AI_DONE"
    HUMAN_PENDING = "HUMAN_PENDING"
    FINAL_RESULT = "FINAL_RESULT"
    NEED_SUPPLEMENT = "NEED_SUPPLEMENT"
    ESCALATED = "ESCALATED"
    APPEAL_RECEIVED = "APPEAL_RECEIVED"
    APPEAL_RESOLVED = "APPEAL_RESOLVED"
    REVIEW_TASK = "REVIEW_TASK"


class NotificationChannel(StrEnum):
    INAPP = "inapp"
    SMS = "sms"
    WECHAT = "wechat"


class RejectReasonCode(StrEnum):
    NOT_EV_BEHAVIOR = "NOT_EV_BEHAVIOR"
    UNCLEAR_IMAGE = "UNCLEAR_IMAGE"
    WRONG_TYPE = "WRONG_TYPE"
    NO_VIOLATION = "NO_VIOLATION"
    DUPLICATE = "DUPLICATE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    INSUFFICIENT_INFO = "INSUFFICIENT_INFO"
    OTHER = "OTHER"


REJECT_REASON_LABEL: dict[str, str] = {
    RejectReasonCode.NOT_EV_BEHAVIOR: "与电动车无关的图片",
    RejectReasonCode.UNCLEAR_IMAGE: "图片模糊，无法辨认",
    RejectReasonCode.WRONG_TYPE: "行为类型选择与实际不符",
    RejectReasonCode.NO_VIOLATION: "经核实不构成违规",
    RejectReasonCode.DUPLICATE: "重复上报",
    RejectReasonCode.OUT_OF_SCOPE: "不在受理范围",
    RejectReasonCode.LOCATION_MISMATCH: "地点与实际不符",
    RejectReasonCode.INSUFFICIENT_INFO: "信息不足且未补充",
    RejectReasonCode.OTHER: "其他",
}


class LocationSource(StrEnum):
    GEOLOCATION = "geolocation"
    EXIF = "exif"
    MANUAL = "manual"
    MAP = "map"
    UNKNOWN = "unknown"


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEED_SUPPLEMENT = "NEED_SUPPLEMENT"
    ESCALATED = "ESCALATED"


#: system_config 默认值（首次初始化时写入数据库，可在后台修改）
DEFAULT_SYSTEM_CONFIG: dict[str, tuple[str, str, str]] = {
    # key: (value, group, description)
    "ai.auto_close_normal_enabled": ("false", "ai", "是否允许 AI 高置信“正常”结论自动结案（保守档默认关闭）"),
    "ai.high_confidence_threshold": ("0.90", "ai", "高置信阈值"),
    "ai.trust_low_threshold": ("0.50", "ai", "低于该置信度标记为疑难，建议双人复核"),
    "ai.violation_threshold": ("0.62", "ai", "规则引擎判定“疑似违规”的风险分阈值"),
    "ai.normal_threshold": ("0.34", "ai", "规则引擎判定“疑似正常”的风险分阈值"),
    "review.supplement_sla_hours": ("72", "review", "退回补充材料的时限（小时），超期自动不通过"),
    "review.task_sla_hours": ("24", "review", "人工审核时限（小时），超期提醒"),
    "appeal.window_days": ("7", "appeal", "终态后可发起申诉的天数"),
    "report.retention_days": ("1825", "report", "上报记录保留天数（默认 5 年）"),
    "report.auto_archive_days": ("30", "report", "终态后自动归档天数"),
    "upload.max_images": ("9", "upload", "单次上报最多图片数"),
    "privacy.notice": (
        "请勿拍摄可识别人脸、车牌的清晰画面；如已拍摄，系统或人工将做脱敏处理。",
        "privacy",
        "上传页隐私提示文案",
    ),
}
