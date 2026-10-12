"""前端路径规范回归测试：所有页面跳转与静态资源引用必须是站点根绝对路径。

缺陷背景（2026-10-12 用户反馈「使用中有时会切换成没有样式的裸排页面，刷新可恢复」）：
    `static/js/api.js` 由根级页面与 `static/admin/dashboard.html`（位于 /admin/ 子目录）共用，
    其中 `renderHeader()` 生成的导航链接（如 href="review-workbench.html"）与
    `Auth.home()/requireLogin()/logout()` 的 `location.replace('index.html')` 都是**裸相对路径**。

    在 /admin/dashboard.html 页面上点击「审核工作台」时，浏览器把 href 解析为
    `/admin/review-workbench.html`（并不存在）；Nginx `try_files $uri $uri/ /index.html`
    把它**兜底成 index.html 的 HTML（状态码仍是 200）**；该 HTML 里的
    `<link href="css/base.css">` 又按 /admin/ 解析成 `/admin/css/base.css`，
    再次被兜底成 HTML —— 浏览器把 text/html 当 CSS 解析 → 全站样式失效，
    表现为整页纯白的裸排页面（刷新后回到正常路径即恢复）。

修复：
    1) 全站前端路径统一改为**以 / 开头的绝对路径**（本应用固定部署在站点根）；
    2) Nginx 对 css/js 等扩展名资源显式 `try_files $uri =404`，不再兜底成 HTML，
       让路径错误立刻显性暴露而不是静默降级。

本测试守住第 1 条（代码层规范），部署层的 nginx 断言见 test_deploy_requirements.py。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

HTML_FILES = sorted(list(STATIC.glob("*.html")) + list(STATIC.glob("admin/*.html")))
JS_FILES = sorted(STATIC.glob("js/*.js"))

# 允许的绝对路径前缀 / 协议；其余一律视为裸相对路径
_SAFE = re.compile(r"^(?:https?:)?//|^/|^#|^mailto:|^tel:|^\{\{")


def _rel(html_files, js_files):
    """返回 (文件, 行号, 路径片段) 的裸相对路径清单。"""
    bad = []
    for f in html_files:
        for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r'(?:href|src)="([^"]+)"', ln):
                val = m.group(1)
                if _SAFE.match(val):
                    continue
                # 纯 JS 占位（如 href="javascript:;"）不算
                if val.startswith("javascript:"):
                    continue
                bad.append((f, i, val))
    for f in js_files:
        txt = f.read_text(encoding="utf-8")
        for i, ln in enumerate(txt.splitlines(), 1):
            # 跳过注释行（缺陷说明里会引用反例写法）
            stripped = ln.strip()
            if stripped.startswith("*") or stripped.startswith("//"):
                continue
            for m in re.finditer(r"""location\.(?:replace|assign)\(\s*['"]([^'"]+)['"]""", ln):
                val = m.group(1)
                if not val.startswith("/") and not val.startswith("http"):
                    bad.append((f, i, "location-> " + val))
            for m in re.finditer(r"""location\.href\s*=\s*['"]([^'"]+)['"]""", ln):
                val = m.group(1)
                if not val.startswith("/") and not val.startswith("http"):
                    bad.append((f, i, "href-> " + val))
            for m in re.finditer(r"""href=\\?['"]([a-z0-9_\-]+\.html)""", ln):
                bad.append((f, i, "html-link-> " + m.group(1)))
    return bad


# ---------------------------------------------------------------- 真实文件断言


def test_all_html_assets_use_absolute_paths():
    """所有 HTML 的资源引用与页面链接必须是以 / 开头的绝对路径。"""
    bad = _rel(HTML_FILES, [])
    assert not bad, "以下引用不是绝对路径（会导致子目录页面资源 404）：\n" + "\n".join(
        f"  {f.relative_to(ROOT)}:{i}  {v}" for f, i, v in bad
    )


def test_js_navigations_use_absolute_paths():
    """所有 JS 中的页面跳转必须是以 / 开头的绝对路径。"""
    bad = _rel([], JS_FILES)
    assert not bad, "以下跳转不是绝对路径（在 /admin/ 等子目录下会解析到错误路径）：\n" + "\n".join(
        f"  {f.relative_to(ROOT)}:{i}  {v}" for f, i, v in bad
    )


def test_admin_page_does_not_reference_parent_assets():
    """admin/ 子目录页面统一用 /css、/js 绝对引用，不再用 ../ 上溯。"""
    txt = (STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    assert "../" not in txt, "admin 页面不应再出现 ../ 上溯引用，统一改用绝对路径"


def test_api_js_exposes_page_helper_and_absolute_home():
    """api.js 的核心跳转点必须是绝对路径（防止回归到裸相对路径）。"""
    src = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
    assert "function page(" in src, "api.js 应提供 page() 规范化辅助函数"
    assert "return this.isReviewer() ? '/review-workbench.html' : '/my-reports.html';" in src, (
        "Auth.home() 必须返回绝对路径——它被 index.html 的登录跳转直接使用"
    )


# ---------------------------------------------------------------- 防假阴性对照


def test_guard_detects_relative_link():
    """对照：裸相对路径必须能被守卫捕获（否则守卫形同虚设）。"""
    fake_html = '<a href="review-workbench.html">审核工作台</a>'
    tmp = ROOT / "static" / "_guard_probe.html"
    tmp.write_text(fake_html, encoding="utf-8")
    try:
        bad = _rel([tmp], [])
        assert bad, "守卫未能识别裸相对路径——测试本身失效"
    finally:
        tmp.unlink(missing_ok=True)


def test_guard_detects_relative_location_replace():
    """对照：JS 里的裸相对跳转也必须能被捕获。"""
    fake_js = "if (!this.token) { location.replace('index.html'); }"
    tmp = STATIC / "js" / "_guard_probe.js"
    tmp.write_text(fake_js, encoding="utf-8")
    try:
        bad = _rel([], [tmp])
        assert bad, "守卫未能识别裸相对 location.replace——测试本身失效"
    finally:
        tmp.unlink(missing_ok=True)


def test_guard_accepts_absolute_and_external():
    """对照：绝对路径与外链不得误报。"""
    ok_html = '<a href="/upload.html">上报</a><link href="/css/base.css"><a href="https://x.cn/a">外</a>'
    tmp = ROOT / "static" / "_guard_ok.html"
    tmp.write_text(ok_html, encoding="utf-8")
    try:
        assert _rel([tmp], []) == []
    finally:
        tmp.unlink(missing_ok=True)
