"""外部服务接入自检脚本。

用途：填完 .env 后，一条命令确认「OSS / 高德 / 短信 / 域名证书」是否真的可用，
而不是等到线上才发现 Key 填错或模板没审核通过。

用法：
    # 只做静态检查（不产生任何外部调用与费用）
    .venv/Scripts/python scripts/check_external.py

    # 真实连通性探测（OSS 会写入并删除一个探针对象；高德会消耗 1 次配额）
    .venv/Scripts/python scripts/check_external.py --probe

    # 真实发送一条验证码短信到指定手机号（会产生短信费用）
    .venv/Scripts/python scripts/check_external.py --probe --sms 13800138000

    # 检查线上域名与 HTTPS 证书剩余有效期
    .venv/Scripts/python scripts/check_external.py --domain ebikeguard.xyz
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

OK = "✅"
WARN = "⚠️ "
FAIL = "❌"
SKIP = "➖"

rows: list[tuple[str, str, str]] = []


def add(item: str, status: str, detail: str) -> None:
    rows.append((item, status, detail))


def _display_width(text: str) -> int:
    """中文/全角字符按 2 列计算，保证终端输出对齐。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


# ---------------------------------------------------------------- 基础


def check_base() -> None:
    # SECRET_KEY
    if settings.secret_key in ("dev-secret-change-me", "", "change-me-to-a-long-random-string-in-production"):
        add("密钥 SECRET_KEY", WARN, "仍为默认值，生产环境必须先更换（python -c \"import secrets;print(secrets.token_urlsafe(36))\"）")
    elif len(settings.secret_key) < 32:
        add("密钥 SECRET_KEY", WARN, f"长度仅 {len(settings.secret_key)} 字符，建议 ≥32")
    else:
        add("密钥 SECRET_KEY", OK, f"已自定义（{len(settings.secret_key)} 字符）")

    # DEBUG
    if settings.debug:
        add("DEBUG 开关", WARN, "当前为 true：验证码会通过接口回显、错误信息更详细，生产环境请置 false")
    else:
        add("DEBUG 开关", OK, "false（生产模式）")

    # 数据库
    try:
        from app.database import SessionLocal
        from app.models import BehaviorType

        with SessionLocal() as db:
            n = db.query(BehaviorType).count()
        add("数据库", OK if n else WARN, f"可连接，行为类型字典 {n} 条" + ("" if n else "（请先执行 scripts/init_db.py）"))
    except Exception as exc:  # noqa: BLE001
        add("数据库", FAIL, f"连接失败：{type(exc).__name__} {exc}")

    # 图片目录
    try:
        probe = Path(settings.images_dir) / ".probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("图片存储目录", OK, f"{settings.images_dir} 可写")
    except Exception as exc:  # noqa: BLE001
        add("图片存储目录", FAIL, f"不可写：{exc}")


# ---------------------------------------------------------------- OSS


def check_oss(probe: bool) -> None:
    if settings.storage_backend != "oss":
        add("对象存储 OSS", SKIP, f"当前 STORAGE_BACKEND={settings.storage_backend}，使用本地磁盘（无需配置）")
        return
    if not settings.oss_ready():
        add("对象存储 OSS", FAIL, "OSS_ENDPOINT / OSS_BUCKET / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET 不完整")
        return
    try:
        import oss2  # noqa: F401
    except ImportError:
        add("对象存储 OSS", FAIL, "未安装 oss2：pip install -r requirements-optional.txt")
        return
    if not probe:
        add("对象存储 OSS", OK, f"配置完整（Bucket={settings.oss_bucket}），加 --probe 做读写探测")
        return
    try:
        from app.adapters.storage.aliyun_oss import AliyunOSSStorage

        storage = AliyunOSSStorage()
        key = "_probe/check_external.txt"
        storage.save(key, b"probe")
        data = storage.read(key)
        storage.delete(key)
        if data == b"probe":
            add("对象存储 OSS", OK, "读写删除探针均成功，Bucket 权限与 Key 正常")
        else:
            add("对象存储 OSS", WARN, "写入成功但读回内容不一致，请检查 Bucket 是否有 CDN/镜像回源")
    except Exception as exc:  # noqa: BLE001
        add("对象存储 OSS", FAIL, f"探测失败：{type(exc).__name__} {exc}")


# ---------------------------------------------------------------- 高德


def check_amap(probe: bool) -> None:
    if settings.geocoding_provider != "amap":
        add("高德逆地理编码", SKIP, f"当前 GEOCODING_PROVIDER={settings.geocoding_provider}，仅存经纬度不解析地址")
        return
    if not settings.amap_ready():
        add("高德逆地理编码", FAIL, "AMAP_KEY 为空")
        return
    if not probe:
        add("高德逆地理编码", OK, f"Key 已配置（{settings.amap_key[:6]}…），加 --probe 做真实调用")
        return
    try:
        import httpx

        resp = httpx.get(
            "https://restapi.amap.com/v3/geocode/regeo",
            params={"key": settings.amap_key, "location": "116.397428,39.90923", "output": "JSON"},
            timeout=8.0,
        )
        data = resp.json()
        if data.get("status") == "1":
            addr = (data.get("regeocode") or {}).get("formatted_address")
            add("高德逆地理编码", OK, f"调用成功，示例解析：{addr}")
        else:
            code = data.get("infocode")
            hint = {
                "10001": "Key 不正确或已删除",
                "10003": "Key 已过期",
                "10009": "Key 与绑定的服务平台不符，创建 Key 时须选「Web服务」",
                "10044": "该 Key 日调用量已达上限",
                "10012": "Key 已欠费",
            }.get(str(code), "")
            add("高德逆地理编码", FAIL, f"{data.get('info')}（infocode={code}）" + (f"，{hint}" if hint else ""))
    except Exception as exc:  # noqa: BLE001
        add("高德逆地理编码", FAIL, f"调用异常：{type(exc).__name__} {exc}")


# ---------------------------------------------------------------- 短信


def check_sms(probe: bool, phone: str | None) -> None:
    from app.adapters.sms.factory import describe_sms_provider, get_sms_provider

    info = describe_sms_provider()
    provider = info["provider"]
    if provider == "mock":
        add("短信通道", SKIP, "当前为 Mock 模拟通道：验证码写日志并由接口回显，生产环境上报人将收不到真实短信")
        return
    if not info["ready"]:
        add("短信通道", FAIL, f"{provider} 配置不完整，" + info["detail"])
        return

    sdk = "alibabacloud_dypnsapi20170525" if provider == "aliyun_dypns" else "alibabacloud_dysmsapi20170525"
    try:
        __import__(sdk)
    except ImportError:
        add("短信通道", FAIL, f"未安装 {provider} 对应 SDK（{sdk}）：pip install -r requirements-optional.txt")
        return
    if not (probe and phone):
        add("短信通道", OK, f"{provider} 配置完整，{info['detail']}；加 --probe --sms <手机号> 真实发送")
        return
    try:
        ok, message, _ = get_sms_provider().send_code(phone, "123456", "selfcheck")
        add("短信通道", OK if ok else FAIL, f"{provider} 真实发送到 {phone}：{message}")
    except Exception as exc:  # noqa: BLE001
        add("短信通道", FAIL, f"发送异常：{type(exc).__name__} {exc}")


# ---------------------------------------------------------------- 域名 / HTTPS


def nameservers(domain: str) -> list[str]:
    """查询该域名的 NS（权威解析托管在哪家），用于判断「记录该加到哪个控制台」。

    走公共 DNS-over-HTTPS，纯附加信息：查询失败只返回空列表，绝不改变主结论。
    """
    import json
    import urllib.request

    url = f"https://dns.alidns.com/resolve?name={domain}&type=NS"
    req = urllib.request.Request(url, headers={"User-Agent": "ev-audit-check/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [a["data"].rstrip(".") for a in (data.get("Answer") or []) if a.get("data")]


def check_domain(domain: str | None) -> None:
    if not domain:
        add("域名与 HTTPS 证书", SKIP, "未指定 --domain；真机使用手机拍照与浏览器定位必须走 HTTPS")
        return
    try:
        ip = socket.gethostbyname(domain)
    except socket.gaierror as exc:
        try:
            ns = nameservers(domain)
        except Exception:  # noqa: BLE001 - 附加诊断，查不到不影响主结论
            ns = []
        ns_hint = f"当前 NS 为 {'、'.join(ns)}，记录应加在该服务商的控制台；" if ns else ""
        add(
            "域名与 HTTPS 证书",
            FAIL,
            f"{domain} 解析不到 IP（{exc}）：{ns_hint}若尚未添加解析记录，先确定服务器公网 IP，"
            "再到该域名所属的「云解析 DNS」为主机记录 @ 和 www 各加一条 A 记录；"
            "注意解析指向大陆节点服务器前必须先完成 ICP 备案，否则访问会被阻断；"
            "若走 Cloudflare Tunnel 则应改 NS、跳过 A 记录",
        )
        return
    except Exception as exc:  # noqa: BLE001
        add("域名与 HTTPS 证书", FAIL, f"{domain} 解析查询异常：{type(exc).__name__} {exc}")
        return
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as tls:
                cert = tls.getpeercert()
        expire = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days = (expire - dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)).days
        subject = dict(x[0] for x in cert.get("subject", ()))
        status = OK if days > 30 else WARN if days > 7 else FAIL
        add(
            "域名与 HTTPS 证书",
            status,
            f"{domain} → {ip}，证书主体 {subject.get('commonName')}，{expire:%Y-%m-%d} 到期（剩余 {days} 天）",
        )
    except Exception as exc:  # noqa: BLE001
        add("域名与 HTTPS 证书", FAIL, f"TLS 握手失败：{type(exc).__name__} {exc}（443 端口是否开放？证书是否已部署？）")


# ---------------------------------------------------------------- 汇总


def check_ai(probe: bool) -> None:
    if settings.ai_provider != "qwenvl":
        add("AI 审核引擎", SKIP, "当前为规则占位引擎（可解释、零依赖），接入视觉大模型见 docs/外部服务接入指南.md 第五节")
        return
    if not settings.dashscope_api_key:
        add("视觉大模型", FAIL, "AI_PROVIDER=qwenvl 但未设置 DASHSCOPE_API_KEY（百炼控制台创建 API-KEY 后填入 .env 并重启）")
        return
    detail = f"{settings.ai_vl_model} @ {settings.ai_vl_base_url}"
    if not probe:
        add("视觉大模型", OK, f"Key 已配置（{settings.dashscope_api_key[:6]}…，{detail}）；加 --probe 真实调用一次验证连通")
        return
    try:
        from app.adapters.ai.qwen_vl import QwenVLProvider

        ok, message = QwenVLProvider().probe()
    except Exception as exc:  # noqa: BLE001
        ok, message = False, f"探测异常：{type(exc).__name__} {exc}"
    add("视觉大模型", OK if ok else FAIL, message)


def main() -> None:
    parser = argparse.ArgumentParser(description="外部服务接入自检")
    parser.add_argument("--probe", action="store_true", help="执行真实外部调用（OSS 读写、高德、视觉模型小图调用各一次）")
    parser.add_argument("--sms", metavar="PHONE", help="真实发送一条验证码短信到该手机号")
    parser.add_argument("--domain", metavar="HOST", help="检查域名解析与 HTTPS 证书有效期")
    args = parser.parse_args()

    print("=" * 78)
    print(" 电动车不安全使用行为识别系统 · 外部服务自检")
    print(f" 配置文件：{ROOT / '.env'}")
    print(f" 探测模式：{'真实调用' if args.probe else '仅静态检查'}")
    print("=" * 78)

    check_base()
    check_oss(args.probe)
    check_amap(args.probe)
    check_sms(args.probe, args.sms)
    check_ai(args.probe)
    check_domain(args.domain)

    width = max(_display_width(r[0]) for r in rows) + 2
    print()
    for item, status, detail in rows:
        pad = " " * max(1, width - _display_width(item))
        print(f"{status} {item}{pad}{detail}")

    problems = [r for r in rows if r[1] == FAIL]
    print()
    if problems:
        print(f"存在 {len(problems)} 项需要处理，详见上方 ❌ 条目。")
        print("对应操作步骤见 docs/外部服务接入指南.md")
        sys.exit(1)
    print("未发现阻断性问题。⚠️ 条目为生产上线前需要确认的项。")


if __name__ == "__main__":
    main()
