"""清洗逻辑测试：时间解析、状态/等级映射、金额勾稽、修复标记。

这些函数是 ETL 的核心，一旦出错会静默污染整张 DWD 表，
所以必须用真实出现过的脏格式来测。
"""
from __future__ import annotations

import pandas as pd
import pytest

from bloom.etl.clean import (
    CLEAN_RULES,
    LEVEL_MAP,
    PHONE_RE,
    STATUS_MAP,
    _flags,
    _map_with_cache,
    parse_datetime,
)


# ---------------- 时间解析 ----------------
@pytest.mark.parametrize("raw,expected", [
    ("2024-02-14 10:30:00", "2024-02-14 10:30:00"),
    ("2024-02-14 10:30", "2024-02-14 10:30:00"),
    ("2024/2/14 9:30", "2024-02-14 09:30:00"),      # Excel 转换后的斜杠格式
    ("2024年2月14日 9:30", "2024-02-14 09:30:00"),   # 手工录入的中文格式
    ("14/02/2024 09:30", "2024-02-14 09:30:00"),    # 日/月/年
    ("2024-02-14", "2024-02-14 00:00:00"),
])
def test_parse_datetime_formats(raw, expected):
    ts = parse_datetime(raw)
    assert ts is not None
    assert ts.strftime("%Y-%m-%d %H:%M:%S") == expected


@pytest.mark.parametrize("raw", ["", None, "not a date", "2024-13-45 99:99"])
def test_parse_datetime_invalid(raw):
    assert parse_datetime(raw) is None


# ---------------- 状态 / 等级映射 ----------------
@pytest.mark.parametrize("raw,expected", [
    ("已完成", "已完成"),
    ("已完成 ", "已完成"),
    ("已 完成", "已完成"),
    ("COMPLETED", "已完成"),
    ("完成", "已完成"),
    ("REFUNDED", "已退款"),
    ("待配送", "待配送"),
])
def test_status_mapping(raw, expected):
    series = pd.Series([raw])
    out = _map_with_cache(series, STATUS_MAP, {})
    assert out.iloc[0] == expected


@pytest.mark.parametrize("raw,expected", [
    ("普通会员", "普通会员"),
    ("普通", "普通会员"),
    ("SILVER", "银卡会员"),
    (" 金卡会员 ", "金卡会员"),
    ("Diamond", "钻石会员"),
])
def test_level_mapping(raw, expected):
    series = pd.Series([raw])
    out = _map_with_cache(series, LEVEL_MAP, {})
    assert out.iloc[0] == expected


# ---------------- 手机号 ----------------
@pytest.mark.parametrize("phone,ok", [
    ("13812345678", True), ("19900001111", True),
    ("1381234567", False), ("138123456789", False), ("1381234567a", False),
])
def test_phone_regex(phone, ok):
    assert bool(PHONE_RE.match(phone)) is ok


# ---------------- 修复标记 ----------------
def test_flags_combines_codes_in_order():
    mask_a = pd.Series([True, False, False])
    mask_b = pd.Series([True, True, False])
    flags = _flags([("E05", mask_a), ("E06", mask_b)])
    assert flags.tolist() == ["E05,E06", "E06", None]


def test_flags_keeps_index():
    idx = [10, 20, 30]
    mask = pd.Series([True, False, True], index=idx)
    flags = _flags([("E01", mask)])
    assert flags.index.tolist() == idx


# ---------------- 金额勾稽（E04 的核心规则）----------------
def test_amount_reconcile_rule():
    """实付 = 原价 - 优惠 + 运费；被放大 50 倍的行一定不满足。"""
    df = pd.DataFrame({
        "total_amount": [300.0, 300.0, 300.0],
        "discount_amount": [30.0, 30.0, 30.0],
        "freight_amount": [9.9, 9.9, 9.9],
        "payable_amount": [279.9, 13995.0, -1.0],
    })
    diff = (df["payable_amount"]
            - (df["total_amount"] - df["discount_amount"] + df["freight_amount"])).abs()
    bad = (df["payable_amount"] <= 0) | (diff > 0.01)
    assert bad.tolist() == [False, True, True]


# ---------------- 规则清单完整性 ----------------
def test_clean_rules_complete():
    assert len(CLEAN_RULES) == 19
    for code in [f"E{i:02d}" for i in range(1, 20)]:
        assert code in CLEAN_RULES
        assert CLEAN_RULES[code]
