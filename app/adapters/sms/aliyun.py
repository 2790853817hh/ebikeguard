"""阿里云短信通道实现（阿里云短信服务 Dysmsapi）。

启用步骤：
    1) pip install -r requirements-optional.txt          # 安装阿里云短信 SDK
    2) 在 .env 中设置：
         SMS_PROVIDER=aliyun
         SMS_ACCESS_KEY_ID=...            （RAM 子账号，仅授权 Dysmsapi 权限）
         SMS_ACCESS_KEY_SECRET=...
         SMS_SIGN_NAME=湘西州数据局        （已审核通过的短信签名）
         SMS_TEMPLATE_CODE=SMS_xxxxxxx    （验证码模板，内容含 ${code}）
         SMS_NOTICE_TEMPLATE_CODE=SMS_xxxxxxx   （可选，业务通知模板，含 ${content}）
    3) 验证：python scripts/check_external.py --sms 13800138000

失败处理原则：任何异常都不向上抛到业务层中断流程，而是返回 (False, 可读原因)，
由调用方决定是否回退（登录接口会把原因原样反馈给用户）。
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.adapters.sms.aliyun_errors import describe_send_failure
from app.adapters.sms.base import SmsProvider
from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

ENDPOINT = "dysmsapi.aliyuncs.com"

# 接口返回码 → 中文处理建议（短信服务 Dysmsapi 专属码表）
_ERROR_HINTS: dict[str, str] = {
    "isv.SMS_SIGNATURE_ILLEGAL": "短信签名未审核通过或与模板不匹配（控制台「签名管理」确认）",
    "isv.SMS_TEMPLATE_ILLEGAL": "短信模板未审核通过或已删除（控制台「模板管理」确认）",
    "isv.SMS_TEMPLATE_PARAM_ERROR": "模板变量名不匹配：模板里必须是 ${code} / ${content}",
    "isv.MOBILE_NUMBER_ILLEGAL": "手机号格式不合法",
    "isv.BUSINESS_LIMIT_CONTROL": "触发流控，请稍后再试（登录接口已内置 60 秒重发限制）",
    "isv.AMOUNT_NOT_ENOUGH": "短信账户余额不足，请充值",
    "isv.OUT_OF_SERVICE": "短信服务已停机，请检查账户状态",
    "isv.RAM_PERMISSION_DENY": "RAM 用户未授权：请授予 dysms:SendSms 权限",
    "InvalidAccessKeyId.NotFound": "AccessKey 不存在，请检查 SMS_ACCESS_KEY_ID",
    "SignatureDoesNotMatch": "AccessKey Secret 不正确",
    "Throttling": "调用被限流，请稍后再试",
}


def hint_for(code: str | None) -> str:
    """返回错误码对应的中文处理建议（纯函数，便于单测）。"""
    return _ERROR_HINTS.get(str(code), "")


class AliyunSmsProvider(SmsProvider):
    name = "aliyun"

    def __init__(self) -> None:
        self._client = self._build_client()

    # ---------------------------------------------------------------- 初始化
    def _build_client(self):
        if not settings.effective_sms_ak_id or not settings.effective_sms_ak_secret:
            raise AppError(
                "阿里云短信未配置 AccessKey：请在 .env 中设置 SMS_ACCESS_KEY_ID / SMS_ACCESS_KEY_SECRET"
                "（或复用 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET）"
            )
        try:
            from alibabacloud_dysmsapi20170525.client import Client
            from alibabacloud_tea_openapi import models as open_api_models
        except ImportError as exc:
            raise AppError(
                "未安装阿里云短信 SDK，请执行："
                ".venv/Scripts/python -m pip install -r requirements-optional.txt"
            ) from exc

        config = open_api_models.Config(
            access_key_id=settings.effective_sms_ak_id,
            access_key_secret=settings.effective_sms_ak_secret,
        )
        config.endpoint = ENDPOINT
        config.region_id = settings.sms_region
        return Client(config)

    # ---------------------------------------------------------------- 发送
    def _send(self, phone: str, template_code: str, template_param: dict, scene: str) -> tuple[bool, str]:
        from alibabacloud_dysmsapi20170525 import models as dysmsapi_models

        if not settings.sms_sign_name:
            raise AppError("阿里云短信未配置短信签名：请在 .env 中设置 SMS_SIGN_NAME")
        if not template_code:
            raise AppError("阿里云短信未配置模板 Code：请在 .env 中设置 SMS_TEMPLATE_CODE")

        request = dysmsapi_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=settings.sms_sign_name,
            template_code=template_code,
            template_param=json.dumps(template_param, ensure_ascii=False),
        )
        try:
            response = self._client.send_sms(request)
        except Exception as exc:  # noqa: BLE001 - 网络/SDK 异常统一转可读信息
            logger.warning("阿里云短信调用异常（scene=%s phone=%s）：%s", scene, phone, exc)
            return False, describe_send_failure(exc, hint_for)

        body = getattr(response, "body", None)
        code = getattr(body, "code", None)
        if code == "OK":
            return True, "验证码已发送"

        message = getattr(body, "message", "未知错误")
        logger.warning("阿里云短信返回失败（scene=%s phone=%s）：code=%s message=%s", scene, phone, code, message)
        hint = hint_for(code)
        text = f"{message}（{code}）"
        return False, f"短信发送失败：{text}" + (f"，{hint}" if hint else "")

    def send_code(self, phone: str, code: str, scene: str = "login") -> tuple[bool, str, str | None]:
        ok, message = self._send(phone, settings.sms_template_code, {"code": code}, scene)
        return ok, message, None  # 生产环境绝不回显验证码

    def send_notice(self, phone: str, content: str) -> tuple[bool, str]:
        if not settings.sms_notice_template_code:
            # 未配置通知模板时静默跳过（站内信仍然送达），避免影响主流程
            logger.info("未配置 SMS_NOTICE_TEMPLATE_CODE，跳过短信通知：%s", phone)
            return False, "未配置通知短信模板，已跳过"
        return self._send(phone, settings.sms_notice_template_code, {"content": content[:100]}, "notice")


@lru_cache
def _singleton() -> AliyunSmsProvider:  # pragma: no cover - 便于脚本复用
    return AliyunSmsProvider()
