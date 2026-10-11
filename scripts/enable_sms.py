"""一条命令启用真实短信通道：写入 .env → 立即发一条验证码实测 → 失败自动回滚。

用法（服务器上，Linux / bash）：

    cd /opt/ev_audit
    sudo -u evaudit ./.venv/bin/python scripts/enable_sms.py \
        --ak-id LTAI5t************ --ak-secret ************ \
        --sign 恒创联众 --template 100001 --extra min=5 \
        --phone 13349638573

用法（本机 Windows / PowerShell）：

    cd "E:\\cg\\Documents\\WorkBuddy\\电动车识别"
    .\\.venv\\Scripts\\python.exe scripts\\enable_sms.py --ak-id LTAI5t************ --ak-secret ************ --sign 恒创联众 --template 100001 --extra min=5 --phone 13349638573

通道 B（阿里云短信服务，需企业/事业单位资质，支持通知短信）加 `--provider aliyun`
并把 `--template` 换成形如 `SMS_123456789` 的自建模板，另加 `--notice-template SMS_987654321`。

行为约定：
    1) 先备份 .env 为 .env.bak，再只替换指定键，其余行与注释原样保留；
    2) 写入后立刻用真实接口发一条验证码到 --phone；
    3) 发送失败 → 自动把 SMS_PROVIDER 回滚为 mock，避免登录接口因凭证错误而报错；
    4) 不带 --phone 时只写入并做静态就绪检查，不产生任何短信费用；
    5) 参数值**不能含空格**（argparse 会按空格切分），占位符会被写入前的防呆校验拒绝。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "✅"
WARN = "⚠️ "
FAIL = "❌"

DEFAULT_ENV = ROOT / ".env"

# 只允许脚本改这些键，避免误伤其它配置
WRITABLE_KEYS = {
    "SMS_PROVIDER",
    "SMS_ACCESS_KEY_ID",
    "SMS_ACCESS_KEY_SECRET",
    "SMS_SIGN_NAME",
    "SMS_TEMPLATE_CODE",
    "SMS_TEMPLATE_EXTRA_JSON",
    "SMS_NOTICE_TEMPLATE_CODE",
}


def upsert_env(text: str, updates: dict[str, str]) -> str:
    """把 updates 写入 env 文本：已存在的键原地替换，缺失的键追加到末尾。

    保留无关行、注释与原有顺序；不产生重复键。纯函数，便于单测。
    """
    remaining = dict(updates)
    lines = text.split("\n") if text.strip() else []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        if lines and lines[-1].strip() != "":
            lines.append("")
        for key, value in remaining.items():
            lines.append(f"{key}={value}")

    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    return out


def mask(value: str, keep: int = 4) -> str:
    """脱敏显示，避免把 AccessKey/Secret 打到终端与日志里。"""
    if not value:
        return "(空)"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


def mask_phone(phone: str) -> str:
    return phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "***"


# 文档里出现过的占位符，用户容易整段复制过来
PLACEHOLDER_TOKENS = (
    "你的",
    "赠送签名名称",
    "赠送模板编号",
    "签名名称",
    "模板编号",
    "xxxx",
    "XXXX",
)

_AK_RE = re.compile(r"[A-Za-z0-9+/=_\-]{8,64}")
_AK_ID_RE = re.compile(r"[A-Za-z0-9]{8,64}")


def validate_inputs(
    ak_id: str,
    ak_secret: str,
    sign: str,
    template: str,
    provider: str,
    extra_json: str = "{}",
) -> list[str]:
    """写入 .env 之前的防呆校验，返回问题清单（空列表 = 通过）。纯函数，便于单测。

    为什么必须查：AccessKey ID 会被阿里云 SDK 放进 HTTP 的 Authorization 请求头，
    而 HTTP 头只能按 latin-1 编码——填了中文会抛出 'latin-1 codec can't encode characters'，
    用户只看到 UnretryableException，完全不知道是自己填错了。
    """
    problems: list[str] = []

    for label, value, pattern in (
        ("--ak-id", ak_id, _AK_ID_RE),
        ("--ak-secret", ak_secret, _AK_RE),
    ):
        text = value or ""
        if "*" in text or any(token in text for token in PLACEHOLDER_TOKENS):
            problems.append(f"{label} 是占位符或打码值，请到控制台点「复制」取真实值")
        elif not pattern.fullmatch(text):
            problems.append(
                f"{label} 只能是字母/数字（当前含非法字符）。AccessKey 会写进 HTTP 请求头，"
                "含中文或空格会直接报 latin-1 编码错误"
            )

    if not (sign or "").strip():
        problems.append("--sign 不能为空：填控制台「赠送签名配置」里选定的签名名称")
    elif any(token in sign for token in ("你的", "赠送签名名称", "签名名称")):
        problems.append("--sign 是占位符，请填控制台里真实显示的签名名称（例如「恒创联众」）")

    template_text = (template or "").strip()
    if provider == "aliyun_dypns":
        if not re.fullmatch(r"\d{4,8}", template_text):
            hint = "；注意 dypns 通道的模板编号是纯数字，形如 100001，SMS_xxxx 是企业通道的格式"
            problems.append("--template 应为纯数字的赠送模板编号" + (hint if template_text.upper().startswith("SMS_") else ""))
    elif not re.fullmatch(r"SMS_\d{6,}", template_text):
        problems.append("--template 企业通道的模板编号形如 SMS_123456789")

    problems.extend(validate_extra_json(extra_json))
    return problems


def validate_extra_json(extra_json: str) -> list[str]:
    """SMS_TEMPLATE_EXTRA_JSON 必须是 JSON 对象，且取值都为字符串/数字。"""
    text = (extra_json or "").strip()
    if not text or text == "{}":
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f'--extra-json 不是合法 JSON（{exc.msg}）：应写成 {{"min":"5"}} 这种形式']
    if not isinstance(data, dict):
        return ['--extra-json 必须是 JSON 对象，例如 {"min":"5"}']
    return []


def parse_extra_pairs(pairs: list[str] | None) -> tuple[dict[str, str], list[str]]:
    """把可重复的 `--extra KEY=VALUE` 解析成字典，返回 (数据, 问题清单)。

    设计成 KEY=VALUE 而不是传一段 JSON，是为了绕开两个 shell 的引号转义差异：
    bash 单引号原样保留反斜杠，PowerShell 又可能吃掉内层双引号，同一个 JSON 字面量
    在两边会得到不同结果。KEY=VALUE 不含引号，两边行为完全一致。
    """
    data: dict[str, str] = {}
    problems: list[str] = []
    for item in pairs or []:
        if "=" not in item:
            problems.append(f"--extra 要写成 KEY=VALUE 形式（收到「{item}」）")
            continue
        key, value = item.split("=", 1)
        if not key.strip():
            problems.append(f"--extra 的键不能为空（收到「{item}」）")
            continue
        data[key.strip()] = value.strip()
    return data, problems


def write_env(env_path: Path, updates: dict[str, str]) -> tuple[str, Path]:
    """备份后写回 .env，返回 (原始文本, 备份路径)。"""
    original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    backup = env_path.with_name(env_path.name + ".bak")
    if env_path.exists():
        shutil.copy2(env_path, backup)

    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_text(upsert_env(original, updates), encoding="utf-8", newline="\n")
    os.replace(tmp, env_path)
    return original, backup


def restore_env(env_path: Path, original: str) -> None:
    env_path.write_text(original, encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启用真实短信通道（写入 .env 并实测）")
    parser.add_argument("--provider", default="aliyun_dypns", choices=["aliyun_dypns", "aliyun"],
                        help="aliyun_dypns=个人实名可用（仅验证码，默认）；aliyun=需企业资质（支持通知）")
    parser.add_argument("--ak-id", required=True, help="RAM 子账号的 AccessKey ID")
    parser.add_argument("--ak-secret", required=True, help="RAM 子账号的 AccessKey Secret")
    parser.add_argument("--sign", required=True, help="签名名称（通道 A 填控制台「赠送签名」，通道 B 填自建签名）")
    parser.add_argument("--template", required=True, help="验证码模板编号（通道 A 形如 100001）")
    parser.add_argument(
        "--extra-json",
        default="{}",
        help='（备选）模板内其他变量，直接给 JSON，例如 {"min":"5"}；优先用 --extra',
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="模板内其他变量，可重复：赠送模板 100001 需要 --extra min=5",
    )
    parser.add_argument("--notice-template", default="", help="通知短信模板编号，仅通道 B 支持；留空则通知走站内信")
    parser.add_argument("--phone", default="", help="真实发送一条验证码到该号码；留空则只做静态检查")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV), help=argparse.SUPPRESS)
    return parser.parse_args()


def restart_hint() -> str:
    """按运行平台给出重启提示（纯函数，便于单测）。

    Windows 本机跑 `run.py` 前台进程；Linux 服务器上服务由 systemd 托管，
    只提示 `run.py` 会让服务器用户照抄一条在服务器上不成立的 Windows 路径。
    """
    import os

    if os.name == "nt":
        return "    .\\.venv\\Scripts\\python run.py"
    return "    sudo systemctl restart ev_audit    # 或 ./.venv/bin/python run.py（前台调试用）"


def selfcheck_hint() -> str:
    """自检命令同样按平台给出（本地 Windows / 服务器 Linux 路径不同）。"""
    import os

    if os.name == "nt":
        return "    .\\.venv\\Scripts\\python scripts\\check_external.py --probe --sms <你的手机号>"
    return "    ./.venv/bin/python scripts/check_external.py --probe --sms <你的手机号>"


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file)

    if args.provider == "aliyun_dypns" and args.notice_template:
        print(f"{WARN} 通道 aliyun_dypns 不支持通知短信，--notice-template 将被忽略")
        args.notice_template = ""

    extra_pairs, extra_problems = parse_extra_pairs(args.extra)
    if extra_pairs:
        extra_json = json.dumps(extra_pairs, ensure_ascii=False)
    else:
        extra_json = (args.extra_json or "{}").strip()

    updates = {
        "SMS_PROVIDER": args.provider,
        "SMS_ACCESS_KEY_ID": args.ak_id,
        "SMS_ACCESS_KEY_SECRET": args.ak_secret,
        "SMS_SIGN_NAME": args.sign,
        "SMS_TEMPLATE_CODE": args.template,
        "SMS_TEMPLATE_EXTRA_JSON": extra_json,
        "SMS_NOTICE_TEMPLATE_CODE": args.notice_template,
    }
    unknown = set(updates) - WRITABLE_KEYS
    if unknown:  # pragma: no cover - 防御性
        print(f"{FAIL} 内部错误：试图写入不允许的键 {unknown}")
        return 1

    problems = extra_problems + validate_inputs(
        args.ak_id, args.ak_secret, args.sign, args.template, args.provider, extra_json
    )
    if args.phone and not re.fullmatch(r"1[3-9]\d{9}", args.phone):
        problems.append("--phone 不是合法的中国大陆手机号（应为 1 开头的 11 位数字）")

    print("=" * 78)
    print(" 启用真实短信通道")
    print(f" 配置文件：{env_path}")
    print(f" 通道    ：{args.provider}")
    print(f" 签名    ：{args.sign}    模板：{args.template}    额外变量：{updates['SMS_TEMPLATE_EXTRA_JSON']}")
    print(f" AccessKey：{mask(args.ak_id)}    发送测试：{mask_phone(args.phone) if args.phone else '否'}")
    print("=" * 78)

    if problems:
        print(f"{FAIL} 参数检查未通过，**未改动任何文件**（.env 保持原样）：")
        for item in problems:
            print(f"   · {item}")
        print()
        print("说明：文档里的 AK / 签名 / 模板都是占位符，必须换成控制台里的真实值再跑；")
        print("      占位符里的中文会让阿里云 SDK 在拼 HTTP 请求头时报 latin-1 编码错误。")
        return 1

    original, backup = write_env(env_path, updates)
    print(f"{OK} 已写入 {env_path}（备份：{backup.name}）")

    # 让当前进程立刻按新配置工作（模块内的 settings 是同一个对象）
    from app.config import settings

    for key, value in updates.items():
        setattr(settings, key.lower(), value)

    from app.adapters.sms.factory import describe_sms_provider

    info = describe_sms_provider()
    if not info["ready"]:
        print(f"{FAIL} 配置仍不完整：{info['detail']}")
        restore_env(env_path, original)
        print(f"{WARN} 已回滚 .env，未改变原有短信通道")
        return 1
    print(f"{OK} 配置完整：{info['detail']}")

    if not args.phone:
        print()
        print("未指定 --phone，跳过真实发送。验证命令：")
        print(selfcheck_hint())
        return 0

    from app.adapters.sms.factory import get_sms_provider

    code = f"{secrets.randbelow(1_000_000):06d}"
    print(f"→ 正在向 {mask_phone(args.phone)} 发送验证码 {code} …")
    try:
        ok, message, _ = get_sms_provider().send_code(args.phone, code, "selfcheck")
    except Exception as exc:  # noqa: BLE001
        ok, message = False, f"{type(exc).__name__}: {exc}"

    if ok:
        print(f"{OK} 短信已下发：{message}")
        print(f"   请查看手机是否收到验证码 {code}（内容与签名以控制台「赠送模板」为准）")
        print()
        print("下一步：重启服务即生效")
        print(restart_hint())
        return 0

    print(f"{FAIL} 发送失败：{message}")
    restore_env(env_path, original)
    print(f"{WARN} 已回滚 .env（SMS_PROVIDER 恢复为原值），登录接口不受影响")
    print("   排查顺序：① AK/Secret 是否正确且有 dypns:SendSmsVerifyCode 权限；"
          "② 签名与模板是否取自控制台「赠送签名 / 赠送模板」；③ 是否已开通「短信认证」并有余量")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
