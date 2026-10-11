"""前端上传控件回归测试：手机端必须同时支持「拍照」与「从相册选择」。

缺陷背景（2026-10-12 用户反馈）：
    `static/upload.html` 的文件输入框带有 `capture="environment"` 属性，
    移动端浏览器（iOS Safari / Android Chrome）会据此**直接调起相机**、
    不显示系统选择面板 → 表现为「只能拍照、无法从相册选择照片」。
    页面文案本身已写「点击拍照 / 从相册选择」，属行为与文案不一致。

修复：移除 `capture` 属性后，系统面板恢复（iOS：拍照 / 照片图库 / 浏览；
Android：相机 / 相册），`multiple` 同时保证相册可多选。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_HTML = ROOT / "static" / "upload.html"


def _file_input_line(html: str) -> str:
    """取出文件输入框所在行；找不到即失败。"""
    lines = [ln for ln in html.splitlines() if 'id="fileInput"' in ln]
    assert lines, "upload.html 中必须存在 id=fileInput 的文件输入框"
    return lines[0]


def _assert_no_capture(html: str) -> None:
    line = _file_input_line(html)
    assert "capture" not in line, (
        "fileInput 不得带 capture 属性——它会让手机端只能拍照、无法从相册选择"
    )


def test_mobile_can_pick_from_album():
    """真实文件：文件输入框不得带 capture 属性。"""
    _assert_no_capture(UPLOAD_HTML.read_text(encoding="utf-8"))


def test_guard_detects_reintroduced_capture():
    """防假阴性对照：把 capture 加回去，守卫断言必须真的失败。"""
    bad = '<input type="file" id="fileInput" accept="image/*" capture="environment" multiple>'
    with pytest.raises(AssertionError):
        _assert_no_capture(bad)


def test_file_input_accepts_images_and_multiple():
    """相册批量选择依赖 multiple；文件类型限定为图片。"""
    line = _file_input_line(UPLOAD_HTML.read_text(encoding="utf-8"))
    assert 'accept="image/*"' in line
    assert "multiple" in line
