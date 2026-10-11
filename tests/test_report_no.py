"""上报编号生成回归测试（纯逻辑，不写数据库）。

缺陷背景（2026-10-11 00:10 实测复现）：
    `generate_report_no()` 用 **UTC** 日期拼查询前缀，而 `new_report_no()` 用
    **北京时间** 拼编号。北京时间 00:00~08:00 这段（UTC 仍停在前一天）两者日期不同：
    前缀 `EV<昨天>` 查不到任何记录 → 流水号恒为 1 → 连续两条上报都拿到
    `EV<今天>0001` → `UNIQUE constraint failed: report.report_no`，
    表现为该时段内所有新上报提交返回 500。
"""
from __future__ import annotations

import datetime as dt

from app.core import utils
from app.services import report_service

# UTC 2026-10-10 16:30 == 北京时间 2026-10-11 00:30（跨日窗口内）
CROSS_DAY_MOMENT = dt.datetime(2026, 10, 10, 16, 30, tzinfo=dt.timezone.utc)


class _FakeQuery:
    """记录被查询的 LIKE 表达式，并返回调用方指定的「上一条记录」。"""

    def __init__(self, owner: "_FakeSession") -> None:
        self._owner = owner

    def filter(self, expression):  # noqa: ANN001 - SQLAlchemy 表达式
        self._owner.like_clause = str(expression.compile(compile_kwargs={"literal_binds": True}))
        return self

    def order_by(self, *_args):  # noqa: ANN002
        return self

    def first(self):
        return self._owner.row


class _FakeSession:
    def __init__(self, row) -> None:  # noqa: ANN001
        self.row = row
        self.like_clause = ""

    def query(self, *_args):  # noqa: ANN002
        return _FakeQuery(self)


def test_new_report_no_uses_beijing_date(monkeypatch):
    """编号日期段必须是北京时间：UTC 16:30 时北京已是次日。"""
    monkeypatch.setattr(utils, "utc_now", lambda: CROSS_DAY_MOMENT)
    assert utils.report_no_day() == "20261011"
    assert utils.new_report_no(1) == "EV202610110001"


def test_query_prefix_matches_generated_number(monkeypatch):
    """核心回归：查询前缀与生成编号的日期段必须一致。

    跨日窗口内若前缀仍是 UTC 日期（EV20261010），上一条记录（EV20261011xxxx）
    就查不到 —— 这正是重号的触发条件。
    """
    monkeypatch.setattr(utils, "utc_now", lambda: CROSS_DAY_MOMENT)
    monkeypatch.setattr(report_service, "utc_now", lambda: CROSS_DAY_MOMENT)

    # 数据库里已存在当天第 7 条，编号按北京时间生成
    fake = _FakeSession((utils.new_report_no(7),))
    generated = report_service.generate_report_no(fake)  # type: ignore[arg-type]

    assert "EV20261011%" in fake.like_clause, fake.like_clause
    assert generated == "EV202610110008"


def test_stale_utc_prefix_would_have_collided(monkeypatch):
    """对照实验：人为把前缀改回 UTC 日期，就会重号（证明该断言真的能抓到这个缺陷）。"""
    monkeypatch.setattr(utils, "utc_now", lambda: CROSS_DAY_MOMENT)
    monkeypatch.setattr(report_service, "utc_now", lambda: CROSS_DAY_MOMENT)

    # 模拟旧实现：前缀查不到记录（返回 None），流水号退回 1
    stale = _FakeSession(None)
    stale_query = report_service.generate_report_no(stale)  # type: ignore[arg-type]
    fresh = utils.new_report_no(1)
    assert stale_query == fresh  # 旧实现下，这两条上报会拿到同一个编号
