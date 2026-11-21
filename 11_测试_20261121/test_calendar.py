"""业务日历测试：节日日期是否准确、需求指数是否合理。

这些是"数据可信度"的地基——如果情人节算错了一天，
后面的节日效应分析、预测模型全部会偏。
"""
from __future__ import annotations

from datetime import date

import pytest

from bloom.datagen.calendar_cn import (
    FESTIVALS,
    LUNAR_FESTIVALS,
    build_calendar,
    festival_affinity,
)


@pytest.fixture(scope="module")
def cal():
    return build_calendar(date(2026, 1, 1), date(2026, 12, 31)).set_index("date")


def test_calendar_length():
    df = build_calendar(date(2026, 1, 1), date(2026, 12, 31))
    assert len(df) == 365
    assert df["date"].min() == date(2026, 1, 1)


def test_valentine_is_festival(cal):
    row = cal.loc[date(2026, 2, 14)]
    assert row["is_festival"] == 1
    assert row["festival_name"] == "情人节"
    assert row["demand_index"] > 3.0


def test_qixi_lunar_date(cal):
    """七夕 2026 是农历七月初七 = 公历 8 月 19 日。"""
    assert LUNAR_FESTIVALS["七夕节"][2026] == date(2026, 8, 19)
    row = cal.loc[date(2026, 8, 19)]
    assert row["festival_name"] == "七夕节"


def test_spring_festival(cal):
    """春节：节前 7 天是年宵花旺季，节中门店冷清（相对下降）。"""
    row = cal.loc[date(2026, 2, 17)]
    assert row["festival_name"] == "春节"
    eve = cal.loc[date(2026, 2, 16)]          # 节前最后一天（年宵花高峰）
    # 需求指数是"乘法模型"，不能用绝对值 1.0 判断，要跟节前对比
    assert row["demand_index"] < eve["demand_index"]
    assert row["demand_index"] < cal.loc[date(2026, 2, 14)]["demand_index"]


def test_pre_sale_ramp(cal):
    """情人节前 3 天应当逐日升温（预售效应）。"""
    d12 = cal.loc[date(2026, 2, 12)]["demand_index"]
    d13 = cal.loc[date(2026, 2, 13)]["demand_index"]
    d14 = cal.loc[date(2026, 2, 14)]["demand_index"]
    assert d12 < d13 < d14


def test_weekend_lift(cal):
    """同一月份内，周末需求指数平均应高于工作日。"""
    feb = cal[cal["month"] == 2]
    weekend = feb[feb["is_weekend"] == 1]["demand_index"].mean()
    weekday = feb[feb["is_weekend"] == 0]["demand_index"].mean()
    assert weekend > weekday


def test_all_festivals_resolvable():
    """每个节日在 2024-2026 至少能解析出日期（避免写错月份/星期）。"""
    for f in FESTIVALS:
        resolved = 0
        for year in (2024, 2025, 2026):
            from bloom.datagen.calendar_cn import _resolve

            if _resolve(f, year):
                resolved += 1
        assert resolved >= 3, f"{f.name} 有年份无法解析"


def test_affinity_weights_sum_to_one():
    for name in ("情人节", "母亲节", "清明节", "春节", "", "毕业季"):
        weights = festival_affinity(name)
        assert abs(sum(weights.values()) - 1.0) < 1e-6
