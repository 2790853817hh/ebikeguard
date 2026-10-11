"""一条命令启用真实视觉大模型（AI 审核）：写入 .env → 真实探测一次 → 失败自动回滚。

用法（先 cd 到项目根目录；本机 PowerShell / 服务器 bash 均可）：

    .\\.venv\\Scripts\\python.exe scripts\\enable_ai.py --key sk-xxxx

    # 想更省：--model qwen3-vl-flash；想更强：--model qwen-vl-max
    # 换其它 OpenAI 兼容平台（如智谱）：--base-url https://open.bigmodel.cn/api/paas/v4 --model glm-4.6v
    # 只写配置、不真实调用：--no-probe

行为约定：
    1) 先备份 .env 为 .env.bak；
    2) 写入 AI_PROVIDER / DASHSCOPE_API_KEY（及可选的 AI_VL_MODEL / AI_VL_BASE_URL）；
    3) 立刻用一张程序生成的小图真实调用一次模型，验证 Key、模型名与网络；
    4) 失败 → 自动把 AI_PROVIDER 回滚为原值，AI 审核退化为规则占位引擎，上报流程不受影响。
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

# 允许写入的键（防呆：避免脚本越权改配置）
WRITABLE_KEYS = {
    "AI_PROVIDER",
    "DASHSCOPE_API_KEY",
    "AI_VL_MODEL",
    "AI_VL_BASE_URL",
}


def validate_key(key: str) -> list[str]:
    """写入 .env 前的防呆校验，返回问题清单（空列表 = 通过）。纯函数，便于单测。"""
    problems: list[str] = []
    text = (key or "").strip()
    if not text:
        return ["--key 不能为空：填百炼控制台「API-KEY 管理」里创建的 Key"]
    if "*" in text or any(token in text for token in ("你的", "xxxx", "sk-xxx")):
        problems.append("--key 是占位符或打码值，请到百炼控制台点「复制」取真实 Key")
    elif len(text) < 16:
        problems.append("--key 长度异常（不足 16 位），请确认复制完整")
    return problems


def validate_model(model: str) -> list[str]:
    """模型名不能为空、不能含空格（空格会把参数拆散）。纯函数。"""
    text = (model or "").strip()
    if not text:
        return ["--model 不能为空"]
    if any(ch.isspace() for ch in text):
        return ["--model 不能含空格"]
    return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启用真实视觉大模型（写入 .env 并真实探测）")
    parser.add_argument("--key", required=True, help="百炼 API-KEY（sk- 开头）")
    parser.add_argument(
        "--model",
        default="",
        help="模型名；不填则保持 .env 现值（初始为 qwen3-vl-plus），可填 qwen3-vl-flash / qwen-vl-max",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="OpenAI 兼容接口地址；默认阿里云百炼，换平台（如智谱）时才需要填",
    )
    parser.add_argument("--no-probe", action="store_true", help="只写配置、不真实调用模型")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV), help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file)

    updates = {
        "AI_PROVIDER": "qwenvl",
        "DASHSCOPE_API_KEY": args.key.strip(),
    }
    if args.model.strip():
        updates["AI_VL_MODEL"] = args.model.strip()
    if args.base_url.strip():
        updates["AI_VL_BASE_URL"] = args.base_url.strip()

    unknown = set(updates) - WRITABLE_KEYS
    if unknown:  # pragma: no cover - 防御性
        print(f"{FAIL} 内部错误：试图写入不允许的键 {unknown}")
        return 1

    problems = validate_key(args.key)
    if args.model.strip():
        problems += validate_model(args.model)
    if args.base_url.strip() and not args.base_url.strip().startswith(("http://", "https://")):
        problems.append("--base-url 应以 http:// 或 https:// 开头")

    print("=" * 78)
    print(" 启用真实视觉大模型（AI 审核）")
    print(f" 配置文件：{env_path}")
    print(f" 模型    ：{updates.get('AI_VL_MODEL', '(保持 .env 现值)')}")
    print(f" 接口    ：{updates.get('AI_VL_BASE_URL', '(默认阿里云百炼)')}")
    print(f" API-KEY：{mask(args.key)}    真实探测：{'否' if args.no_probe else '是'}")
    print("=" * 78)

    if problems:
        print(f"{FAIL} 参数检查未通过，**未改动任何文件**（.env 保持原样）：")
        for item in problems:
            print(f"   · {item}")
        print()
        print("说明：文档与命令里的 Key 都是占位符，必须换成控制台里复制出来的真实值再跑。")
        return 1

    original, backup = write_env(env_path, updates)
    print(f"{OK} 已写入 {env_path}（备份：{backup.name}）")

    # 让当前进程立刻按新配置工作（模块内的 settings 是同一个对象）
    from app.config import settings

    for key, value in updates.items():
        setattr(settings, key.lower(), value)

    if not settings.ai_vl_ready():
        print(f"{FAIL} 配置仍不完整（缺 Key / Base URL / 模型名）")
        restore_env(env_path, original)
        print(f"{WARN} 已回滚 .env，未改变原有 AI 审核通道")
        return 1
    print(f"{OK} 配置完整：视觉大模型 {settings.ai_vl_model}（OpenAI 兼容协议）")

    if args.no_probe:
        print()
        print("已跳过真实调用。验证命令：")
        print("    .\\.venv\\Scripts\\python.exe scripts\\check_external.py --probe")
        return 0

    from app.adapters.ai.qwen_vl import QwenVLProvider

    print(f"→ 正在真实调用一次模型（{settings.ai_vl_model}，程序生成小图探测）…")
    try:
        ok, message = QwenVLProvider().probe()
    except Exception as exc:  # noqa: BLE001
        ok, message = False, f"{type(exc).__name__}: {exc}"

    if ok:
        print(f"{OK} {message}")
        print()
        print("下一步：重启服务即生效（新上报将由视觉大模型初审，疑似违规仍转人工复核）")
        print(restart_hint())
        return 0

    print(f"{FAIL} 探测失败：{message}")
    restore_env(env_path, original)
    print(f"{WARN} 已回滚 .env（AI_PROVIDER 恢复为原值），AI 审核保持原有通道，上报流程不受影响")
    print(
        "   排查顺序：① Key 是否复制完整且已在百炼控制台开通（模型广场里能看到 qwen3-vl）；"
        "② 模型名是否正确（qwen3-vl-plus / qwen3-vl-flash / qwen-vl-max）；"
        "③ 账户是否有免费额度或余额；④ 服务器出网是否可达 dashscope.aliyuncs.com"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
