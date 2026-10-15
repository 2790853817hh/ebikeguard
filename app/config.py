"""全局配置（Pydantic Settings，可通过 .env 或环境变量覆盖）。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 基础 ----------
    app_name: str = "电动车不安全使用行为识别系统"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    debug: bool = True

    # ---------- 安全 ----------
    secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720
    refresh_token_expire_days: int = 30

    # ---------- 数据库 ----------
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'ev_audit.db').as_posix()}"
    auto_create_tables: bool = True

    # ---------- 目录 ----------
    data_dir: Path = BASE_DIR / "data"
    images_dir: Path = BASE_DIR / "data" / "images"
    static_dir: Path = BASE_DIR / "static"

    # ---------- 外部适配 ----------
    storage_backend: str = "local"  # local | oss
    oss_endpoint: str = ""
    oss_bucket: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""

    sms_provider: str = "mock"  # mock | aliyun_dypns（个人实名可用，仅验证码）| aliyun（需企业资质）
    sms_mock_code: str = "888888"
    sms_code_ttl_seconds: int = 300
    sms_send_interval_seconds: int = 60
    # 阿里云短信（SMS_PROVIDER=aliyun / aliyun_dypns 时必填）
    sms_sign_name: str = ""  # 签名名称：aliyun=自建签名；aliyun_dypns=控制台「赠送签名」名称
    sms_template_code: str = ""  # 模板编号：aliyun=SMS_xxx；aliyun_dypns=赠送模板编号（如 100001）
    sms_notice_template_code: str = ""  # 业务通知模板 Code（仅 aliyun 企业通道支持；dypns 通道不支持）
    sms_template_param_name: str = "code"  # 模板内验证码变量名（系统赠送模板通常为 code）
    sms_template_extra_json: str = "{}"  # 模板内其他变量的固定值，如 {"min":"5"}
    sms_dypns_valid_time_seconds: int = 300  # dypns：验证码有效期（秒），透传给接口 ValidTime
    sms_dypns_interval_seconds: int = 60  # dypns：同号发送间隔（秒），透传给接口 Interval
    sms_region: str = "cn-hangzhou"
    # 短信专用 AccessKey；留空则回落到 OSS 的 AccessKey
    sms_access_key_id: str = ""
    sms_access_key_secret: str = ""

    geocoding_provider: str = "stub"  # stub | amap | baidu
    amap_key: str = ""

    ai_provider: str = "rule"  # rule | qwenvl
    dashscope_api_key: str = ""
    # 视觉大模型（OpenAI 兼容协议；默认阿里云百炼 qwen3-vl，个人实名即可开通）
    ai_vl_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ai_vl_model: str = "qwen3-vl-plus"  # 想更省换 qwen3-vl-flash；想更强换 qwen-vl-max
    ai_vl_timeout_seconds: int = 60
    ai_vl_max_images: int = 9  # 单次审核最多送模型几张图
    ai_vl_max_edge: int = 1024  # 送模型前图片最长边（控制请求体积与 token 成本）
    ai_vl_jpeg_quality: int = 82

    # 通知通道（邮件 SMTP + 群机器人 Webhook；微信/短信为预留通道）
    email_provider: str = "mock"  # mock（未启用）| smtp
    smtp_host: str = ""  # 如 smtp.189.cn（189 邮箱）/ smtp.qq.com（QQ 邮箱）
    smtp_port: int = 465  # 465=SSL（推荐）；587=STARTTLS；阿里云服务器出站 25 被封装，勿用
    smtp_security: str = "ssl"  # ssl | starttls
    smtp_username: str = ""
    smtp_password: str = ""  # 邮箱「授权码」，非登录密码
    smtp_from: str = ""  # 发件人（留空回落 SMTP_USERNAME）
    smtp_from_name: str = "电动车安全上报"  # 发件人显示名
    smtp_timeout_seconds: int = 10
    webhook_kind: str = "wecom"  # wecom（企业微信）| dingtalk（钉钉）
    webhook_url: str = ""  # 群机器人完整地址（含密钥，勿外贴）
    webhook_timeout_seconds: int = 8
    notify_ops_email: str = ""  # 内部提醒收件邮箱（可选，逗号分隔）

    # ---------- 上传限制 ----------
    max_images_per_report: int = 9
    max_image_mb: int = 15
    origin_max_edge: int = 2048
    thumb_max_edge: int = 320
    image_jpeg_quality: int = 85

    # ---------- 初始管理端账号密码（仅 init_db.py 首次创建账号时使用） ----------
    # 说明（2026-10-15）：原先明文写在 scripts/init_db.py 里并展示在登录页，
    # 属安全隐患。现改为「环境变量 / .env 注入」：留空则初始化时随机生成并打印一次。
    seed_admin_password: str = ""
    seed_reviewer1_password: str = ""
    seed_reviewer2_password: str = ""

    image_url_ttl_seconds: int = 600

    cors_origins: str = "*"

    # ---------- 派生属性 ----------
    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def max_image_bytes(self) -> int:
        return self.max_image_mb * 1024 * 1024

    # ---- 短信凭证（专用 Key 缺失时回落到 OSS 的 Key） ----
    @property
    def sms_provider_key(self) -> str:
        """归一化后的短信通道标识：mock / aliyun_dypns / aliyun / unknown。

        写错通道名时返回 unknown（而不是悄悄退回 mock），避免「配了却在发模拟短信」。
        """
        raw = (self.sms_provider or "").strip().lower()
        if raw in ("", "mock"):
            return "mock"
        if raw in ("aliyun_dypns", "dypns", "dypnsapi", "pnvs", "aliyun_pnvs", "sms_cert"):
            return "aliyun_dypns"
        if raw in ("aliyun", "aliyun_sms", "dysmsapi"):
            return "aliyun"
        return "unknown"

    @property
    def sms_template_param(self) -> dict[str, str]:
        """构造模板参数：验证码变量 + 由 SMS_TEMPLATE_EXTRA_JSON 提供的固定变量。"""
        param: dict[str, str] = {}
        extra = (self.sms_template_extra_json or "").strip()
        if extra and extra != "{}":
            try:
                data = json.loads(extra)
            except json.JSONDecodeError:
                return param
            if isinstance(data, dict):
                param.update({str(k): str(v) for k, v in data.items()})
        return param

    @property
    def effective_sms_ak_id(self) -> str:
        return self.sms_access_key_id or self.oss_access_key_id

    @property
    def effective_sms_ak_secret(self) -> str:
        return self.sms_access_key_secret or self.oss_access_key_secret

    def sms_ready(self) -> bool:
        """阿里云短信是否已配置完整。"""
        return bool(
            self.effective_sms_ak_id
            and self.effective_sms_ak_secret
            and self.sms_sign_name
            and self.sms_template_code
        )

    def oss_ready(self) -> bool:
        return bool(self.oss_endpoint and self.oss_bucket and self.oss_access_key_id and self.oss_access_key_secret)

    def amap_ready(self) -> bool:
        return bool(self.amap_key)

    def ai_vl_ready(self) -> bool:
        """视觉大模型（OpenAI 兼容通道）是否已配置完整。"""
        return bool(self.dashscope_api_key and self.ai_vl_base_url and self.ai_vl_model)

    # ---- 通知通道（邮件 / 群机器人） ----
    @property
    def email_provider_key(self) -> str:
        """归一化后的邮件通道标识：mock / smtp / unknown。

        写错通道名时返回 unknown（而不是悄悄退回 mock），避免「配了却在发模拟」。
        """
        raw = (self.email_provider or "").strip().lower()
        if raw in ("", "mock", "off", "none"):
            return "mock"
        if raw in ("smtp", "email", "mail"):
            return "smtp"
        return "unknown"

    @property
    def smtp_security_key(self) -> str:
        """归一化 SMTP 加密方式：ssl / starttls / unknown。"""
        raw = (self.smtp_security or "").strip().lower()
        if raw in ("", "ssl", "smtps"):
            return "ssl"
        if raw in ("starttls", "tls"):
            return "starttls"
        return "unknown"

    @property
    def smtp_from_addr(self) -> str:
        return self.smtp_from or self.smtp_username

    @property
    def webhook_kind_key(self) -> str:
        """归一化群机器人类型：wecom / dingtalk / unknown。"""
        raw = (self.webhook_kind or "").strip().lower()
        if raw in ("", "wecom", "wechat_work", "qywx"):
            return "wecom"
        if raw in ("dingtalk", "ding", "dingding"):
            return "dingtalk"
        return "unknown"

    @property
    def notify_ops_emails(self) -> list[str]:
        return [e.strip() for e in (self.notify_ops_email or "").split(",") if e.strip()]

    def email_ready(self) -> bool:
        """邮件通道是否已配置完整（不含运行时开关判断）。"""
        return bool(
            self.email_provider_key == "smtp"
            and self.smtp_host
            and self.smtp_username
            and self.smtp_password
            and self.smtp_security_key != "unknown"
        )

    def webhook_ready(self) -> bool:
        """群机器人通道是否已配置（URL 存在且类型可识别）。"""
        return bool(self.webhook_url and self.webhook_kind_key != "unknown")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
