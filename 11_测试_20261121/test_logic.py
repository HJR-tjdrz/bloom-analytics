"""RFM、质量规则、指标定义、商品主数据的纯逻辑测试（不依赖数据库）。"""
from __future__ import annotations

import pandas as pd
import pytest

from bloom.analysis.rfm import _segment
from bloom.datagen.catalog import (
    SKU_CODE,
    build_bundles,
    build_channels,
    build_coupons,
    build_skus,
    build_stores,
    channel_weight,
)
from bloom.governance.metrics import METRICS, format_value
from bloom.governance.quality import DIMENSIONS, RULES


# ---------------- RFM 分群 ----------------
@pytest.mark.parametrize("r,f,m,expected", [
    (5, 5, 5, "重要价值客户"),
    (5, 1, 5, "重要发展客户"),
    (1, 5, 5, "重要保持客户"),
    (1, 1, 5, "重要挽留客户"),
    (5, 5, 1, "一般价值客户"),
    (5, 1, 1, "一般发展客户"),
    (1, 5, 1, "一般保持客户"),
    (1, 1, 1, "一般挽留客户"),
])
def test_rfm_segments(r, f, m, expected):
    row = pd.Series({"r_score": r, "f_score": f, "m_score": m})
    assert _segment(row) == expected


def test_rfm_all_eight_segments_covered():
    seen = set()
    for r in (1, 5):
        for f in (1, 5):
            for m in (1, 5):
                seen.add(_segment(pd.Series({"r_score": r, "f_score": f, "m_score": m})))
    assert len(seen) == 8


# ---------------- 质量规则 ----------------
def test_quality_rules_count_and_unique():
    assert len(RULES) == 18
    codes = [r.code for r in RULES]
    assert len(codes) == len(set(codes))
    assert codes[0] == "DQ01" and codes[-1] == "DQ18"


def test_quality_dimensions_weight_sum():
    assert abs(sum(DIMENSIONS.values()) - 1.0) < 1e-9
    assert set(DIMENSIONS) == {"完整性", "唯一性", "有效性", "一致性", "及时性"}


def test_every_rule_has_condition_or_key():
    for r in RULES:
        assert r.cond or r.unique_key, f"{r.code} 既没有条件也没有唯一键"
        assert r.dimension in DIMENSIONS
        assert r.fix_policy


# ---------------- 指标定义 ----------------
def test_metrics_count_and_unique():
    assert len(METRICS) == 48
    codes = [m.code for m in METRICS]
    assert len(codes) == len(set(codes))


def test_metrics_layers():
    layers = {m.layer for m in METRICS}
    assert layers == {"原子", "派生", "复合"}
    for m in METRICS:
        assert m.sql.strip().upper().startswith("SELECT"), f"{m.code} 不是查询语句"
        assert m.formula and m.unit


def test_metric_formatting():
    assert format_value({"value": 0.6126, "percent": True, "unit": "%"}) == "61.26%"
    assert format_value({"value": 1234.5, "percent": False, "unit": "元"}) == "1,234.50"
    assert format_value({"value": None, "percent": False, "unit": "元"}) == "—"


# ---------------- 商品主数据 ----------------
def test_sku_master():
    skus = build_skus()
    assert len(skus) == 41
    assert skus["sku_id"].is_unique
    # 成本必须低于售价，否则毛利为负
    assert (skus["cost_price"] < skus["list_price"]).all()
    assert set(skus["supplier_id"]).issubset({f"SUP0{i}" for i in range(1, 7)})


def test_bundles_and_bom():
    skus = build_skus()
    bundles, bom = build_bundles(skus)
    assert len(bundles) == 38
    assert len(bom) == 106
    # BOM 里的 SKU 必须都存在
    assert set(bom["sku_id"]).issubset(set(skus["sku_id"]))
    # 花束售价必须高于成本
    assert (bundles["list_price"] > bundles["cost_price"]).all()


def test_bundle_bom_completeness():
    skus = build_skus()
    bundles, bom = build_bundles(skus)
    counts = bom.groupby("bundle_id").size()
    assert set(counts.index) == set(bundles["bundle_id"])


def test_channels_and_stores():
    ch = build_channels()
    stores = build_stores()
    assert len(ch) == 6 and len(stores) == 8
    assert set(ch["channel_type"]) == {"线上", "线下"}
    # 佣金率在 [0, 0.25] 之间
    assert ch["commission_rate"].between(0, 0.25).all()


def test_channel_weight_sums_to_one():
    for year in (2024, 2025, 2026):
        for month in (1, 6, 12):
            w = channel_weight(year, month)
            assert abs(sum(w.values()) - 1.0) < 1e-9


def test_channel_mix_evolves():
    """运营策略：抖音占比逐年上升、门店逐年下降。"""
    early = channel_weight(2024, 1)
    late = channel_weight(2026, 11)
    assert late["CH04"] > early["CH04"]
    assert late["CH01"] < early["CH01"]


def test_coupons():
    coupons = build_coupons()
    assert len(coupons) == 12
    assert coupons["coupon_id"].is_unique
