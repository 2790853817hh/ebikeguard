"""部署依赖回归测试：systemd 启动服务所需的可执行文件必须在 requirements.txt 中声明。

缺陷背景（2026-10-11 服务器实测）：
    `deploy/gunicorn/ev_audit.service` 的
    `ExecStart=/opt/ev_audit/.venv/bin/gunicorn ...`
    但 `requirements.txt` 从未声明 gunicorn → 部署出的虚拟环境里没有这个可执行文件 →
    systemd 直接以 `status=203/EXEC` 启动失败，表现为「服务刚起就退出」。
    这类缺陷本地跑应用（run.py 用 uvicorn）完全测不出来，只在真实部署时暴露。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "deploy" / "gunicorn" / "ev_audit.service"
REQUIREMENTS = ROOT / "requirements.txt"

# 解释器与 shell 不算依赖包
_IGNORED = {"python", "python3", "pip", "pip3", "sh", "bash", "env"}


def _exec_binaries() -> set[str]:
    """从 systemd 单元里取出所有 `.venv/bin/<可执行文件>`，排除解释器。"""
    text = UNIT.read_text(encoding="utf-8")
    found = re.findall(r"\.venv/bin/([\w.-]+)", text)
    return {name for name in found if name not in _IGNORED}


def test_extraction_actually_finds_gunicorn():
    """防假阴性：正则必须真的抓到东西，否则下面的断言会空转通过。"""
    assert "gunicorn" in _exec_binaries()


def test_service_executables_are_declared_in_requirements():
    declared = REQUIREMENTS.read_text(encoding="utf-8").lower()
    missing = sorted(name for name in _exec_binaries() if name.lower() not in declared)
    assert not missing, f"systemd 需要 {missing}，但 requirements.txt 未声明（部署后会 203/EXEC）"


def test_deploy_script_restarts_service_on_redeploy():
    """重复部署必须显式 restart 服务。

    缺陷背景（2026-10-12 凌晨确认）：`systemctl enable --now` 的 start 对「已在运行」的
    服务是 no-op，而 Python 进程不会因代码文件被 rsync 覆盖而热加载（gunicorn 未开 reload）——
    表现为「部署完成但跑的还是旧代码」。restart 对未运行的服务等价于 start，两种情况都覆盖。
    """
    script = (ROOT / "deploy" / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "systemctl restart ev_audit" in script, "重复部署时新代码不会生效：脚本缺少 systemctl restart"
