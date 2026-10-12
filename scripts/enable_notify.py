"""一条命令启用通知通道（邮件 SMTP / 群机器人 Webhook）：写入 .env → 真实发送探测 → 失败自动回滚。

用法（服务器上，Linux / bash）：

    cd /opt/ev_audit
    # 邮件（用邮箱「授权码」而非登录密码；阿里云服务器须用 465/587 端口）
    sudo -u evaudit ./.venv/bin/python scripts/enable_notify.py \
        --mode email --smtp-host smtp.189.cn --smtp-user you@189.cn --smtp-pass 授权码 --to 收件人@example.com
    # 群机器人（企业微信 / 钉钉）
    sudo -u evaudit ./.venv/bin/python scripts/enable_notify.py \
        --mode webhook --webhook-kind wecom --webhook-url "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

用法（本机 Windows / PowerShell）：先 cd 到项目根目录，把上面的 python 换成 .\\.venv\\Scripts\\python.exe

行为约定：
    1) 先备份 .env 为 .env.bak，再只替换指定键，其余行与注释原样保留；
    2) 写入后立刻真实发送一封邮件 / 一条群消息做探测（邮件需 --to；可用 --no-probe 跳过）；
    3) 失败 → 自动回滚对应配置（EMAIL_PROVIDER 回 mock / WEBHOOK_URL 恢复原值）；
    4) 参数值不能含空格（argparse 会按空格切分），占位符会被写入前的防呆校验拒绝。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enable_sms import (  # noqa: E402
    DEFAULT_ENV,
    FAIL,
    OK,
    WARN,
    mask,
    restore_env,
    restart_hint,
    write_env,
)

# 只允许脚本改这些键，避免误伤其它配置
WRITABLE_KEYS = {
    "EMAIL_PROVIDER",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "NOTIFY_OPS_EMAIL",
    "WEBHOOK_KIND",
    "WEBHOOK_URL",
}

PLACEHOLDER_TOKENS = ("你的", "xxxx", "XXXX", "粘贴", "请填")


def _looks_like_email(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and "@" in v and "." in v.rsplit("@", 1)[-1] and " " not in v and len(v) >= 6


def validate_email_inputs(host: str, port, security: str, user: str, password: str, to_addr: str) -> list[str]:
    """邮件模式写入 .env 之前的防呆校验，返回问题清单（空列表 = 通过）。纯函数，便于单测。"""
    problems: list[str] = []

    h = (host or "").strip()
    if not h:
        problems.append("--smtp-host 不能为空（如 smtp.189.cn / smtp.qq.com）")
    elif any(tok in h for tok in PLACEHOLDER_TOKENS):
        problems.append("--smtp-host 是占位符，请填真实 SMTP 服务器地址")

    try:
        p = int(port)
        if not (1 <= p <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        problems.append("--smtp-port 应为 1-65535 的整数（465=SSL / 587=STARTTLS）")

    if (security or "").strip().lower() not in ("ssl", "starttls"):
        problems.append("--smtp-security 只能是 ssl 或 starttls（465 配 ssl、587 配 starttls）")

    u = (user or "").strip()
    if not _looks_like_email(u):
        problems.append("--smtp-user 应为完整邮箱地址（如 you@189.cn）")
    elif any(tok in u for tok in PLACEHOLDER_TOKENS):
        problems.append("--smtp-user 是占位符，请填真实邮箱账号")

    pw = (password or "").strip()
    if not pw or "*" in pw or any(tok in pw for tok in PLACEHOLDER_TOKENS) or pw in ("授权码", "密码", "password"):
        problems.append("--smtp-pass 不能为空/占位符；请填邮箱「授权码」（非登录密码）")

    if to_addr and not _looks_like_email(to_addr):
        problems.append("--to 不是合法的邮箱地址")
    return problems


def validate_webhook_inputs(kind: str, url: str) -> list[str]:
    """群机器人模式写入 .env 之前的防呆校验。纯函数，便于单测。"""
    problems: list[str] = []

    if (kind or "").strip().lower() not in ("wecom", "dingtalk"):
        problems.append("--webhook-kind 只能是 wecom（企业微信）或 dingtalk（钉钉）")

    u = (url or "").strip()
    if not u:
        problems.append("--webhook-url 不能为空（从群机器人设置里复制完整地址）")
    elif not u.startswith("https://"):
        problems.append("--webhook-url 必须以 https:// 开头（群机器人的 Webhook 地址均为 https）")
    elif any(tok in u for tok in PLACEHOLDER_TOKENS):
        problems.append("--webhook-url 是占位符，请从群机器人设置里复制真实地址")
    elif "key=" not in u and "access_token=" not in u:
        problems.append("--webhook-url 缺少 key= 或 access_token= 参数，可能复制不完整")
    return problems


def selfcheck_hint() -> str:
    """自检命令按平台给出（本地 Windows / 服务器 Linux 路径不同）。"""
    import os

    if os.name == "nt":
        return "    .\\.venv\\Scripts\\python scripts\\check_external.py --probe"
    return "    ./.venv/bin/python scripts/check_external.py --probe"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启用通知通道（邮件 SMTP / 群机器人，写入 .env 并真实探测）")
    parser.add_argument("--mode", required=True, choices=["email", "webhook"], help="email=邮件 SMTP；webhook=群机器人")
    # 邮件模式
    parser.add_argument("--smtp-host", default="", help="SMTP 服务器，如 smtp.189.cn / smtp.qq.com")
    parser.add_argument("--smtp-port", type=int, default=465, help="465=SSL（推荐） / 587=STARTTLS")
    parser.add_argument("--smtp-security", default="ssl", choices=["ssl", "starttls"], help="加密方式（与端口匹配）")
    parser.add_argument("--smtp-user", default="", help="登录账号（完整邮箱地址）")
    parser.add_argument("--smtp-pass", default="", help="邮箱授权码（非登录密码）")
    parser.add_argument("--smtp-from", default="", help="发件人（留空用 --smtp-user）")
    parser.add_argument("--ops-email", default="", help="（可选）内部运营提醒收件邮箱，写入 NOTIFY_OPS_EMAIL")
    parser.add_argument("--to", default="", help="邮件模式探测收件人；留空则跳过真实发送")
    # 群机器人模式
    parser.add_argument("--webhook-kind", default="wecom", help="wecom（企业微信）| dingtalk（钉钉）")
    parser.add_argument("--webhook-url", default="", help="群机器人 Webhook 完整地址（含密钥）")
    # 公共
    parser.add_argument("--no-probe", action="store_true", help="只写入并做静态检查，不真实发送")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV), help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file)

    if args.mode == "email":
        problems = validate_email_inputs(
            args.smtp_host, args.smtp_port, args.smtp_security, args.smtp_user, args.smtp_pass, args.to
        )
        updates = {
            "EMAIL_PROVIDER": "smtp",
            "SMTP_HOST": args.smtp_host.strip(),
            "SMTP_PORT": str(args.smtp_port),
            "SMTP_SECURITY": args.smtp_security,
            "SMTP_USERNAME": args.smtp_user.strip(),
            "SMTP_PASSWORD": args.smtp_pass,
            "SMTP_FROM": (args.smtp_from or "").strip(),
        }
        if (args.ops_email or "").strip():
            updates["NOTIFY_OPS_EMAIL"] = args.ops_email.strip()
    else:
        problems = validate_webhook_inputs(args.webhook_kind, args.webhook_url)
        updates = {
            "WEBHOOK_KIND": args.webhook_kind.strip().lower(),
            "WEBHOOK_URL": args.webhook_url.strip(),
        }

    unknown = set(updates) - WRITABLE_KEYS
    if unknown:  # pragma: no cover - 防御性
        print(f"{FAIL} 内部错误：试图写入不允许的键 {unknown}")
        return 1

    print("=" * 78)
    print(" 启用通知通道")
    print(f" 配置文件：{env_path}")
    print(f" 模式    ：{'邮件 SMTP' if args.mode == 'email' else '群机器人 Webhook'}")
    if args.mode == "email":
        print(f" SMTP    ：{args.smtp_host}:{args.smtp_port}（{args.smtp_security}）")
        print(f" 账号    ：{args.smtp_user}    授权码：{mask(args.smtp_pass)}    探测收件：{args.to or '(未指定，跳过真实发送)'}")
    else:
        from app.adapters.notify.webhook import mask_url

        print(f" 机器人  ：{args.webhook_kind}    {mask_url(args.webhook_url)}")
    print("=" * 78)

    if problems:
        print(f"{FAIL} 参数检查未通过，**未改动任何文件**（.env 保持原样）：")
        for item in problems:
            print(f"   · {item}")
        return 1

    original, backup = write_env(env_path, updates)
    print(f"{OK} 已写入 {env_path}（备份：{backup.name}）")

    # 让当前进程立刻按新配置工作（模块内的 settings 是同一个对象）
    from app.config import settings

    for key, value in updates.items():
        setattr(settings, key.lower(), int(value) if key == "SMTP_PORT" else value)

    from app.adapters.notify.factory import describe_notify_channels

    info = describe_notify_channels()
    target = info["email"] if args.mode == "email" else info["webhook"]
    if not target.get("ready"):
        print(f"{FAIL} 配置仍不完整：{target.get('detail')}")
        restore_env(env_path, original)
        print(f"{WARN} 已回滚 .env，未改变原有通知配置")
        return 1
    print(f"{OK} 配置完整：{target.get('detail')}")

    # 探测策略：webhook 默认真实发送（--no-probe 跳过）；邮件需要 --to 才发送
    probe_enabled = not args.no_probe and (args.mode == "webhook" or bool(args.to))
    if not probe_enabled:
        print()
        print("未进行真实发送（邮件模式需 --to 指定收件人；或使用了 --no-probe）。验证命令：")
        print(selfcheck_hint())
        return 0

    from app.adapters.notify.base import NotifyMessage, NotifyRecipient
    from app.adapters.notify.factory import get_email_provider, get_webhook_provider

    msg = NotifyMessage(
        event="SELFCHECK",
        title="通知通道自检",
        content="这是一条测试消息：收到即表示通道配置成功。",
        audience="internal",
    )
    try:
        if args.mode == "email":
            ok, detail = get_email_provider().send(msg, NotifyRecipient(email=args.to))
        else:
            ok, detail = get_webhook_provider().send(msg, NotifyRecipient())
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"

    if ok:
        print(f"{OK} 真实发送成功：{detail}")
        print()
        print("下一步：重启服务即生效")
        print(restart_hint())
        return 0

    print(f"{FAIL} 真实发送失败：{detail}")
    if args.mode == "email":
        restore_env(env_path, original)
        print(f"{WARN} 已回滚 .env（EMAIL_PROVIDER 恢复为原值）")
        print("   排查顺序：① 授权码是否正确（非登录密码）；② 端口/加密是否匹配（465=ssl、587=starttls）；"
              "③ 服务器出网是否放行（阿里云 25 端口被封装，须用 465/587）")
    else:
        restore_env(env_path, original)
        print(f"{WARN} 已回滚 .env（WEBHOOK_URL 恢复为原值）")
        print("   排查顺序：① URL 是否完整（含 key=/access_token=）；"
              "② 机器人安全设置（自定义关键词需包含消息中出现的词）；③ 服务器能否访问该域名")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
