"""本地测试图 AI 效果验收：对目录里的照片逐张跑视觉大模型，输出识别结论对照表。

用途：配好视觉大模型（`AI_PROVIDER=qwenvl` + `DASHSCOPE_API_KEY`）后，一条命令验证识别效果——
对准备好的测试场景照片（默认 `test_images/`）逐张给出 AI 结论、置信度与摘要，
并统计「与文件名预期一致 / 无法判断 / 不一致」。

用法（先 cd 到项目根目录；本机 PowerShell / 服务器 bash 均可）：

    .\\.venv\\Scripts\\python.exe scripts\\check_ai_images.py
    .\\.venv\\Scripts\\python.exe scripts\\check_ai_images.py --dir test_images --model qwen3-vl-flash

预期规则（粗判，仅用于快速对照）：文件名含 `NORMAL` / `NEGATIVE` / `规范` / `负样本` → 期望「正常」；
其余文件 → 期望「违规」。

成本提示：每张图约消耗 1000+ token（送模型前会缩到 1024 长边），按当前模型单价计费。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_NEGATIVE_MARKERS_EN = ("NORMAL", "NEGATIVE")
_NEGATIVE_MARKERS_CN = ("规范", "负样本")


def parse_expectation(filename: str) -> str:
    """从文件名粗判预期结论：返回 'VIOLATION' 或 'NORMAL'。纯函数，便于单测。"""
    upper = filename.upper()
    if any(marker in upper for marker in _NEGATIVE_MARKERS_EN):
        return "NORMAL"
    if any(marker in filename for marker in _NEGATIVE_MARKERS_CN):
        return "NORMAL"
    return "VIOLATION"


def classify(expectation: str, conclusion: str) -> str:
    """把「预期 vs AI 结论」归类为 hit / uncertain / miss。纯函数，便于单测。

    - 期望违规：AI=VIOLATION → hit；UNCERTAIN → uncertain（未识别出）；NORMAL → miss。
    - 期望正常：AI=NORMAL → hit；UNCERTAIN → uncertain；VIOLATION → miss。
    """
    text = (conclusion or "").upper()
    if text == "UNCERTAIN":
        return "uncertain"
    expected_ok = "NORMAL" if expectation == "NORMAL" else "VIOLATION"
    return "hit" if text == expected_ok else "miss"


def main() -> int:
    parser = argparse.ArgumentParser(description="本地测试图 AI 效果验收（逐张跑视觉大模型）")
    parser.add_argument("--dir", default=str(ROOT / "test_images"), help="测试图目录，默认 test_images/")
    parser.add_argument("--model", default="", help="模型名；不填则用 .env 的 AI_VL_MODEL")
    args = parser.parse_args()

    import logging

    # 静默调试日志：验收对照表是本脚本的唯一输出（避免第三方库 DEBUG 洪水淹没结果）
    logging.getLogger().setLevel(logging.WARNING)

    from app.config import settings

    if settings.ai_provider != "qwenvl" or not settings.dashscope_api_key:
        print("❌ 视觉大模型未启用：先在 .env 配置 AI_PROVIDER=qwenvl 与 DASHSCOPE_API_KEY")
        print("   一条命令启用：.\\.venv\\Scripts\\python.exe scripts\\enable_ai.py --key sk-xxxx")
        return 1

    image_dir = Path(args.dir)
    if not image_dir.is_dir():
        print(f"❌ 目录不存在：{image_dir}")
        return 1
    files = sorted(
        p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        print(f"❌ {image_dir} 下没有可识别的图片（支持：{'、'.join(sorted(SUPPORTED_SUFFIXES))}）")
        return 1

    from app.adapters.ai.qwen_vl import QwenVLProvider

    provider = QwenVLProvider(model=args.model or None)

    print("=" * 78)
    print(" 本地测试图 · AI 效果验收")
    print(f" 目录：{image_dir}")
    print(f" 模型：{provider.model}    图片数：{len(files)}")
    print("=" * 78)
    print()

    total_tokens = 0
    counts = {"hit": 0, "uncertain": 0, "miss": 0}
    for path in files:
        expectation = parse_expectation(path.name)
        result = provider.audit_images_bytes([path.read_bytes()], behavior_name="（批量验收）")
        usage = (result.raw or {}).get("usage") or {}
        if isinstance(usage.get("total_tokens"), int):
            total_tokens += usage["total_tokens"]

        verdict = classify(expectation, str(result.conclusion))
        counts[verdict] += 1
        mark = {"hit": "✅", "uncertain": "⚠️", "miss": "❌"}[verdict]
        summary = next((r.text for r in result.reasons if r.code == "MODEL_SUMMARY"), "")

        print(f"{mark} {path.name}")
        print(
            f"    预期={expectation}   AI={result.conclusion}  "
            f"置信度={result.confidence:.2f}  用时={result.latency_ms}ms"
        )
        if summary:
            print(f"    摘要：{summary}")
        print()

    print("-" * 78)
    print(f"汇总：✅ 与预期一致 {counts['hit']}  ｜  ⚠️ 无法判断 {counts['uncertain']}  ｜  ❌ 不一致 {counts['miss']}")
    if total_tokens:
        print(f"本次消耗约 {total_tokens} tokens（可直接在百炼控制台核对）")
    print("说明：文件名预期为粗判（含 NORMAL/规范/负样本 视为正常场景），仅用于快速对照；")
    print("      实际效果以人工复核体验为准——所有结论在系统里仍会转人工复核（保守档）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
