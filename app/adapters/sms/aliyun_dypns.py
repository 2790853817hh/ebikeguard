"""阿里云「短信认证」服务（号码认证服务 PNVS / Dypnsapi 2017-05-25）。

**这条通道个人实名认证即可开通，不需要企业资质**——它使用控制台赠送的系统签名与
标准验证码模板，因此跳过了「申请签名 → 提交营业执照 → 运营商实名报备」整条链路。
代价是：只支持验证码，不支持通知/营销短信。

启用步骤：
    1) .venv/Scripts/python -m pip install -r requirements-optional.txt
    2) 阿里云控制台 → 号码认证服务 → 开通「短信认证」，在
       「赠送签名配置」「赠送模板配置」页面选定签名与模板，把名称/编号抄下来
    3) .env：
         SMS_PROVIDER=aliyun_dypns
         SMS_ACCESS_KEY_ID=...            （RAM 子账号，仅授权 dypns:SendSmsVerifyCode）
         SMS_ACCESS_KEY_SECRET=...
         SMS_SIGN_NAME=...                （赠送签名名称）
         SMS_TEMPLATE_CODE=...            （赠送模板编号，形如 100001）
         SMS_TEMPLATE_PARAM_NAME=code     （模板里验证码变量名；赠送模板通常就是 code）
    4) 验证：python scripts/check_external.py --probe --sms 13800138000

计费：核验服务免费，只收短信发送费；按运营商回执状态计费——提交成功但回执失败不计费。

失败处理原则与 aliyun.py 一致：异常不外抛中断业务，返回 (False, 可读原因)。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.adapters.sms.aliyun_errors import describe_send_failure
from app.adapters.sms.base import SmsProvider
from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

ENDPOINT = "dypnsapi.aliyuncs.com"

# 接口返回码 → 中文处理建议
# 来源：阿里云《号码认证服务的服务端 API 返回码》+《SendSmsVerifyCode》错误码。
# 注意：号码认证服务的返回码与「短信服务」(dysmsapi) 完全不同，
# 不存在 isv.SMS_SIGNATURE_ILLEGAL / isv.SMS_TEMPLATE_PARAM_ERROR 这类短信服务专属码。
_ERROR_HINTS: dict[str, str] = {
    "MOBILE_NUMBER_ILLEGAL": "手机号格式非法，请检查号码位数与格式",
    "isv.MOBILE_NUMBER_ILLEGAL": "手机号格式非法，请检查号码位数与格式",
    "InvalidPhoneNumber.Check": "手机号格式校验不通过，请检查号码",
    "INVALID_PARAMETERS": (
        "非法参数：确认 SMS_SIGN_NAME / SMS_TEMPLATE_CODE 与控制台「赠送签名」「赠送模板」一字不差，"
        "且 SMS_TEMPLATE_PARAM_NAME 与模板里的验证码变量名一致"
    ),
    "isv.INVALID_PARAMETERS": (
        "非法参数：确认 SMS_SIGN_NAME / SMS_TEMPLATE_CODE 与控制台「赠送签名」「赠送模板」一字不差，"
        "且 SMS_TEMPLATE_PARAM_NAME 与模板里的验证码变量名一致"
    ),
    "EntityNotExist.SmsSignName": "签名不存在：请在控制台「赠送签名配置」中选定该签名，并核对与 SMS_SIGN_NAME 一致",
    "EntityNotExist.SmsTemplateCode": "模板不存在：请在控制台「赠送模板配置」中选定该模板，并核对与 SMS_TEMPLATE_CODE 一致",
    "BUSINESS_LIMIT_CONTROL": "触发号码流控：该手机号已触发运营商限制，请稍后重试",
    "isv.BUSINESS_LIMIT_CONTROL": "触发号码流控：该手机号已触发运营商限制，请稍后重试",
    "biz.FREQUENCY": "发送频次超限：同一号码对同一签名与模板的发送过于频繁，请拉长 SMS_DYPNS_INTERVAL_SECONDS",
    "LimitExceeded.SmsCode": "验证码次数超限：同一号码对同一签名与模板生成验证码超出限制",
    "isp.QPS_LIMIT": "调用 QPS 受限：请求过于密集，请稍后重试",
    "Throttling": "调用被限流，请稍后再试",
    "isv.OUT_OF_SERVICE": "业务停机：请检查阿里云账户余额并及时充值",
    "isv.PRODUCT_UNSUBSCRIBE": "号码认证服务未开通：请到控制台开通后再试",
    "isv.PRODUCT_UN_SUBSCRIPT": "号码认证服务未开通：请到控制台开通后再试",
    "FUNCTION_NOT_OPENED": "未开通短信认证功能：请到号码认证服务控制台开通后再试",
    "isv.RAM_PERMISSION_DENY": "RAM 用户未授权：请授予 dypns:SendSmsVerifyCode 权限",
    "isp.RAM_PERMISSION_DENY": "RAM 用户未授权：请授予 dypns:SendSmsVerifyCode 权限",
    "InvalidAccessKeyId.NotFound": "AccessKey 不存在，请检查 SMS_ACCESS_KEY_ID",
    "SignatureDoesNotMatch": "AccessKey Secret 不正确，请重新生成 AccessKey",
}


def hint_for(code: str | None) -> str:
    """返回错误码对应的中文处理建议（纯函数，便于单测）。"""
    return _ERROR_HINTS.get(str(code), "")


# 报错文本形如「模版变量min内容非法」，从中提取变量名给精确修复建议
_VAR_IN_MESSAGE = re.compile(r"变量\s*([A-Za-z_][A-Za-z0-9_]*)")


def hint_for_error(code: str | None, message: str = "") -> str:
    """优先从报错文本里提取模板变量名，给出可直接照做的修复；取不到再回退静态码表。

    赠送模板（如 100001）正文常是「您的验证码是${code}，有效期${min}分钟」，缺任何一个变量都会
    报 isv.INVALID_PARAMETERS。只给「检查参数」这类泛化提示，用户无法定位，所以这里点名具体变量。
    """
    if str(code).upper() in ("ISV.INVALID_PARAMETERS", "INVALID_PARAMETERS"):
        match = _VAR_IN_MESSAGE.search(message or "")
        if match:
            name = match.group(1)
            return (
                f"模板里有变量「{name}」未传值或取值不合法：在 .env 的 SMS_TEMPLATE_EXTRA_JSON 补上它，"
                f'例如 {{"{name}":"5"}}（有效期分钟数应与 SMS_CODE_TTL_SECONDS 一致）'
            )
    return hint_for(code)


class AliyunDypnsSmsProvider(SmsProvider):
    """个人可开通的验证码通道（阿里云短信认证服务）。"""

    name = "aliyun_dypns"

    def __init__(self) -> None:
        self._client = self._build_client()

    # ---------------------------------------------------------------- 初始化
    def _build_client(self):
        if not settings.effective_sms_ak_id or not settings.effective_sms_ak_secret:
            raise AppError(
                "阿里云短信认证未配置 AccessKey：请在 .env 中设置 SMS_ACCESS_KEY_ID / SMS_ACCESS_KEY_SECRET"
                "（或复用 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET）"
            )
        try:
            from alibabacloud_dypnsapi20170525.client import Client
            from alibabacloud_tea_openapi import models as open_api_models
        except ImportError as exc:
            raise AppError(
                "未安装号码认证服务 SDK，请执行："
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
    def send_code(self, phone: str, code: str, scene: str = "login") -> tuple[bool, str, str | None]:
        from alibabacloud_dypnsapi20170525 import models as dypnsapi_models

        if not settings.sms_sign_name:
            raise AppError("短信认证未配置签名：请把控制台「赠送签名」的名称填入 SMS_SIGN_NAME")
        if not settings.sms_template_code:
            raise AppError("短信认证未配置模板：请把控制台「赠送模板」的编号填入 SMS_TEMPLATE_CODE")

        param = dict(settings.sms_template_param)
        param[settings.sms_template_param_name or "code"] = code

        request = dypnsapi_models.SendSmsVerifyCodeRequest(
            phone_number=phone,
            sign_name=settings.sms_sign_name,
            template_code=settings.sms_template_code,
            template_param=json.dumps(param, ensure_ascii=False),
            code_type=1,  # 占位符模式下的验证码类型：1=纯数字
            valid_time=settings.sms_dypns_valid_time_seconds,
            interval=settings.sms_dypns_interval_seconds,
            return_verify_code=False,  # 生产环境绝不回显验证码
            auto_retry=1,  # 运营商返回明确失败时允许换签名/换通道重试，提升到达率
        )
        try:
            response = self._client.send_sms_verify_code(request)
        except Exception as exc:  # noqa: BLE001 - 网络/SDK 异常统一转可读信息
            logger.warning("短信认证调用异常（scene=%s phone=%s）：%s", scene, phone, exc)
            return False, describe_send_failure(exc, hint_for), None

        body = getattr(response, "body", None)
        api_code = getattr(body, "code", None)
        if str(api_code).upper() == "OK":
            biz_id = getattr(getattr(body, "model", None), "biz_id", None)
            logger.info("短信认证下发成功（scene=%s phone=%s bizId=%s）", scene, phone, biz_id)
            return True, "验证码已发送", None

        message = getattr(body, "message", "未知错误")
        logger.warning("短信认证返回失败（scene=%s phone=%s）：code=%s message=%s", scene, phone, api_code, message)
        hint = hint_for_error(api_code, message)
        return False, f"短信发送失败：{message}（{api_code}）" + (f"，{hint}" if hint else ""), None

    def send_notice(self, phone: str, content: str) -> tuple[bool, str]:
        """短信认证服务只覆盖验证码场景，通知短信在此通道下自动降级为站内信。"""
        logger.info("短信认证通道不支持通知短信，已降级为站内信：%s", phone)
        return False, "短信认证服务（个人可开通）仅支持验证码，通知已改为站内信；如需短信通知请切换 SMS_PROVIDER=aliyun"


@lru_cache
def _singleton() -> AliyunDypnsSmsProvider:  # pragma: no cover - 便于脚本复用
    return AliyunDypnsSmsProvider()
