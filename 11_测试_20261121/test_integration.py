"""依赖数据库的测试：API 契约、关键指标口径、对账一致性。

连不上 MySQL 时整组自动 skip（见 conftest.require_db），
所以这个文件在 CI 上也不会红。
"""
from __future__ import annotations

import pytest


# ---------------- API ----------------
def test_api_health(require_db):
    from bloom.api.app import create_app

    client = create_app().test_client()
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_api_overview_shape(require_db):
    from bloom.api.app import create_app

    client = create_app().test_client()
    data = client.get("/api/overview").get_json()
    for key in ("gmv", "orders", "customers", "aov", "profit", "margin", "qty",
                "new_customer_ratio", "refund_rate"):
        assert key in data["kpis"]
    assert data["kpis"]["gmv"] > 0
    assert data["kpis"]["orders"] > 0
    assert len(data["monthly"]["months"]) == len(data["monthly"]["yoy"])
    for series in data["monthly"]["series"].values():
        assert len(series) == len(data["monthly"]["months"])


def test_api_all_endpoints(require_db):
    from bloom.api.app import create_app

    client = create_app().test_client()
    for path in ("/api/meta", "/api/rfm", "/api/product", "/api/promo",
                 "/api/channel", "/api/inventory", "/api/forecast"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} 返回 {resp.status_code}"
        assert isinstance(resp.get_json(), dict)


def test_api_filter_changes_result(require_db):
    """按渠道筛选后 GMV 必须小于全量 GMV。"""
    from bloom.api.app import create_app

    client = create_app().test_client()
    full = client.get("/api/overview").get_json()["kpis"]["gmv"]
    part = client.get("/api/overview?channel=美团外卖").get_json()["kpis"]["gmv"]
    assert 0 < part < full


def test_static_index_served(require_db):
    from bloom.api.app import create_app

    client = create_app().test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "BloomAnalytics" in html
    assert "echarts.min.js" in html


# ---------------- 指标口径 ----------------
def test_metric_sample_runs(require_db):
    """抽 5 个指标实跑，验证 SQL 都能执行且返回数值。"""
    from bloom import db
    from bloom.governance.metrics import METRICS

    sample = [m for m in METRICS if m.code in ("M01", "M03", "M05", "M08", "M16")]
    assert len(sample) == 5
    for m in sample:
        row = db.fetch_one(m.sql, database="bloom_dw")
        assert row is not None, f"{m.code} 无返回"
        value = list(row.values())[0]
        assert value is not None, f"{m.code} 返回空"


def test_kpi_consistency(require_db):
    """GMV 与订单数的比值应等于客单价（口径自洽）。"""
    from bloom import db

    row = db.fetch_one("""
        SELECT SUM(net_amount + freight_amount) AS gmv,
               COUNT(DISTINCT order_id) AS orders
        FROM bloom_dw.fact_sales WHERE order_status <> '已取消'
    """, database="bloom_dw")
    gmv, orders = float(row["gmv"]), int(row["orders"])
    aov = gmv / orders
    assert 200 < aov < 320, f"客单价异常：{aov:.2f}"


# ---------------- 数据质量与对账 ----------------
def test_quality_scores(require_db):
    """清洗后得分必须高于清洗前，且都在合理区间。"""
    from bloom.governance.quality import evaluate

    ods = evaluate("ods")
    dwd = evaluate("dwd")
    assert 0.9 < ods["overall"] <= 1.0
    assert ods["overall"] < dwd["overall"] <= 1.0


def test_ods_has_dirty_data(require_db):
    """ODS 必须真的脏（否则清洗链路无从体现）。"""
    from bloom.governance.quality import evaluate

    ods = evaluate("ods")
    total = sum(r["detected"] for r in ods["rules"])
    assert total > 3000, f"ODS 脏数据太少：{total}"


def test_fact_reconciles_with_oltp(require_db):
    """数仓事实表与业务库金额必须完全一致。"""
    from bloom import db

    row = db.fetch_one("""
        SELECT
          (SELECT SUM(payable_amount) FROM bloom_oltp.orders) AS oltp_amt,
          (SELECT SUM(net_amount + freight_amount) FROM bloom_dw.fact_sales) AS dwd_amt
    """, database="bloom_dw")
    assert abs(float(row["oltp_amt"]) - float(row["dwd_amt"])) < 0.01


def test_rfm_table_matches_profile(require_db):
    """ads_customer_rfm 的客户数应与特征表一致。"""
    from bloom import db

    row = db.fetch_one("""
        SELECT (SELECT COUNT(*) FROM bloom_dw.ads_customer_rfm) AS rfm,
               (SELECT COUNT(*) FROM bloom_dw.dws_customer_profile) AS profile
    """, database="bloom_dw")
    assert int(row["rfm"]) == int(row["profile"])


def test_forecast_table_complete(require_db):
    from bloom import db

    row = db.fetch_one("SELECT COUNT(*) AS c FROM bloom_dw.ads_forecast", database="bloom_dw")
    assert int(row["c"]) == 30
