"""看板 API 的数据查询层。

设计原则
--------
1. **参数化查询**：所有筛选条件走 `%s` 占位符，不做字符串拼接（防注入）；
2. **一次查询多个指标**：能用一条 GROUP BY 拿到的，绝不拆成多条（减少往返）；
3. **口径统一**：所有经营类查询都排除「已取消」订单，与第 3 轮报告一致；
4. **只读不写**：API 层不碰任何 DDL/DML，纯查询。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from .. import db
from ..config import MYSQL_DW_DB
from ..logger import get_logger

log = get_logger("api.queries")


# --------------------------------------------------------------------------
# 筛选条件拼装
# --------------------------------------------------------------------------
def _filters(start: Optional[str], end: Optional[str],
             channels: Sequence[str], stores: Sequence[str]) -> tuple[str, List[Any]]:
    sql, params = "", []
    if start:
        sql += " AND d.full_date >= %s"
        params.append(start)
    if end:
        sql += " AND d.full_date <= %s"
        params.append(end)
    if channels:
        sql += f" AND ch.channel_name IN ({','.join(['%s'] * len(channels))})"
        params += list(channels)
    if stores:
        sql += f" AND s.store_name IN ({','.join(['%s'] * len(stores))})"
        params += list(stores)
    return sql, params


_BASE_FROM = """
FROM bloom_dw.fact_sales f
JOIN bloom_dw.dim_date    d  ON d.date_key   = f.date_key
JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk
JOIN bloom_dw.dim_store   s  ON s.store_sk    = f.store_sk
WHERE f.order_status <> '已取消'
"""


def _scalar(row: Optional[Dict[str, Any]], key: str, default: float = 0.0) -> float:
    if not row or row.get(key) is None:
        return default
    return float(row[key])


# --------------------------------------------------------------------------
# 元信息（筛选器选项）
# --------------------------------------------------------------------------
def meta() -> Dict[str, Any]:
    channels = db.fetch_all(
        "SELECT channel_name FROM bloom_dw.dim_channel ORDER BY channel_sk",
        database=MYSQL_DW_DB)
    stores = db.fetch_all(
        "SELECT store_name FROM bloom_dw.dim_store ORDER BY store_sk",
        database=MYSQL_DW_DB)
    span = db.fetch_one(
        "SELECT MIN(full_date) AS d1, MAX(full_date) AS d2 FROM bloom_dw.dim_date",
        database=MYSQL_DW_DB)
    return {
        "channels": [r["channel_name"] for r in channels],
        "stores": [r["store_name"] for r in stores],
        "date_start": str(span["d1"]),
        "date_end": str(span["d2"]),
        "calc_date": "2026-11-19",
    }


# --------------------------------------------------------------------------
# 1. 经营总览
# --------------------------------------------------------------------------
def overview(start, end, channels, stores) -> Dict[str, Any]:
    where, params = _filters(start, end, channels, stores)

    kpi = db.fetch_one(f"""
        SELECT COUNT(DISTINCT f.order_id)                          AS orders,
               COUNT(DISTINCT f.customer_sk)                       AS customers,
               SUM(f.net_amount + f.freight_amount)                AS gmv,
               SUM(f.gross_profit)                                 AS profit,
               SUM(f.quantity)                                     AS qty,
               COUNT(DISTINCT CASE WHEN f.is_first_order = 1
                                   THEN f.order_id END)            AS first_orders,
               COUNT(DISTINCT CASE WHEN f.is_refunded = 1
                                   THEN f.order_id END)            AS refund_orders
        {_BASE_FROM} {where}
    """, params, database=MYSQL_DW_DB)

    gmv = _scalar(kpi, "gmv")
    orders = _scalar(kpi, "orders")
    profit = _scalar(kpi, "profit")
    kpis = {
        "gmv": gmv,
        "orders": int(orders),
        "customers": int(_scalar(kpi, "customers")),
        "aov": gmv / orders if orders else 0,
        "profit": profit,
        "margin": profit / gmv if gmv else 0,
        "qty": int(_scalar(kpi, "qty")),
        "new_customer_ratio": _scalar(kpi, "first_orders") / orders if orders else 0,
        "refund_rate": _scalar(kpi, "refund_orders") / orders if orders else 0,
    }

    monthly = db.read_df(f"""
        SELECT d.month_key                                            AS month_key,
               SUM(f.net_amount + f.freight_amount)                   AS gmv,
               COUNT(DISTINCT f.order_id)                             AS orders
        {_BASE_FROM} {where}
        GROUP BY d.month_key ORDER BY d.month_key
    """, params, database=MYSQL_DW_DB)
    if not monthly.empty:
        monthly["gmv"] = pd.to_numeric(monthly["gmv"], errors="coerce").fillna(0)
        monthly["year"] = monthly["month_key"].str[:4]
        monthly["mon"] = monthly["month_key"].str[5:]
        pivot = monthly.pivot_table(index="mon", columns="year", values="gmv", aggfunc="sum")
        yoy = []
        for m in pivot.index:
            cur = pivot.loc[m].get("2026")
            prev = pivot.loc[m].get("2025")
            yoy.append(float(cur / prev - 1) if prev and cur and prev > 0 else None)
        monthly_payload = {
            "months": [f"{int(m)}月" for m in pivot.index],
            "series": {str(y): [float(pivot.loc[m].get(y, 0) or 0) for m in pivot.index]
                       for y in pivot.columns},
            "yoy": yoy,
        }
    else:
        monthly_payload = {"months": [], "series": {}, "yoy": []}

    fest = db.read_df(f"""
        SELECT d.festival_name,
               COUNT(DISTINCT d.full_date)              AS days,
               SUM(f.net_amount + f.freight_amount)     AS gmv
        {_BASE_FROM} {where} AND d.is_festival = 1 AND d.festival_name <> ''
        GROUP BY d.festival_name
        ORDER BY gmv / days DESC
        LIMIT 10
    """, params, database=MYSQL_DW_DB)
    fest["gmv"] = pd.to_numeric(fest["gmv"], errors="coerce").fillna(0)
    fest["days"] = pd.to_numeric(fest["days"], errors="coerce").fillna(1)
    fest["daily"] = (fest["gmv"] / fest["days"]).round(0)

    by_channel = db.read_df(f"""
        SELECT ch.channel_name, ch.channel_type,
               SUM(f.net_amount + f.freight_amount) AS gmv
        {_BASE_FROM} {where}
        GROUP BY ch.channel_name, ch.channel_type
        ORDER BY gmv DESC
    """, params, database=MYSQL_DW_DB)
    by_store = db.read_df(f"""
        SELECT s.store_name, s.city,
               SUM(f.net_amount + f.freight_amount) AS gmv,
               COUNT(DISTINCT f.order_id)           AS orders
        {_BASE_FROM} {where}
        GROUP BY s.store_name, s.city
        ORDER BY gmv DESC
    """, params, database=MYSQL_DW_DB)
    for df in (by_channel, by_store):
        df["gmv"] = pd.to_numeric(df["gmv"], errors="coerce").fillna(0)

    return {
        "kpis": kpis,
        "monthly": monthly_payload,
        "festival": {
            "names": fest["festival_name"].tolist(),
            "daily": [float(v) for v in fest["daily"]],
        },
        "channel": {
            "names": by_channel["channel_name"].tolist(),
            "values": [{"name": r["channel_name"], "value": float(r["gmv"]),
                        "type": r["channel_type"]} for _, r in by_channel.iterrows()],
        },
        "store": {
            "names": by_store["store_name"].tolist(),
            "gmv": [float(v) for v in by_store["gmv"]],
            "orders": [int(v) for v in pd.to_numeric(by_store["orders"], errors="coerce").fillna(0)],
        },
    }


# --------------------------------------------------------------------------
# 2. RFM
# --------------------------------------------------------------------------
def rfm() -> Dict[str, Any]:
    agg = db.read_df("""
        SELECT rfm_segment, COUNT(*) AS customers, SUM(monetary) AS gmv,
               AVG(recency_days) AS r, AVG(frequency) AS f, AVG(monetary) AS m
        FROM bloom_dw.ads_customer_rfm
        GROUP BY rfm_segment
    """)
    total_cust = float(pd.to_numeric(agg["customers"], errors="coerce").sum()) or 1
    total_gmv = float(pd.to_numeric(agg["gmv"], errors="coerce").sum()) or 1
    agg["cust_share"] = pd.to_numeric(agg["customers"], errors="coerce") / total_cust
    agg["gmv_share"] = pd.to_numeric(agg["gmv"], errors="coerce") / total_gmv
    order = ["重要价值客户", "重要发展客户", "重要保持客户", "重要挽留客户",
             "一般价值客户", "一般发展客户", "一般保持客户", "一般挽留客户"]
    agg["ord"] = agg["rfm_segment"].apply(lambda x: order.index(x) if x in order else 99)
    agg = agg.sort_values("ord")

    scatter = db.read_df("""
        SELECT recency_days, frequency, monetary, rfm_segment
        FROM bloom_dw.ads_customer_rfm
        ORDER BY RAND(42) LIMIT 2500
    """)
    points = [[int(r["recency_days"]), int(r["frequency"]), float(r["monetary"]),
               r["rfm_segment"]] for _, r in scatter.iterrows()]

    return {
        "segments": agg["rfm_segment"].tolist(),
        "customers": [int(v) for v in pd.to_numeric(agg["customers"], errors="coerce")],
        "gmv": [float(v) for v in pd.to_numeric(agg["gmv"], errors="coerce")],
        "cust_share": [float(v) for v in agg["cust_share"]],
        "gmv_share": [float(v) for v in agg["gmv_share"]],
        "avg_r": [float(v) for v in pd.to_numeric(agg["r"], errors="coerce")],
        "avg_f": [float(v) for v in pd.to_numeric(agg["f"], errors="coerce")],
        "avg_m": [float(v) for v in pd.to_numeric(agg["m"], errors="coerce")],
        "scatter": points,
    }


# --------------------------------------------------------------------------
# 3. 商品
# --------------------------------------------------------------------------
def product() -> Dict[str, Any]:
    prof = db.read_df("""
        SELECT product_id, product_name, product_type, category, sales_qty, sales_amt,
               gross_profit, profit_rate, growth_rate, abc_class, bcg_class
        FROM bloom_dw.ads_product_performance
        ORDER BY sales_amt DESC
    """)
    for c in ("sales_qty", "sales_amt", "gross_profit", "profit_rate", "growth_rate"):
        prof[c] = pd.to_numeric(prof[c], errors="coerce").fillna(0)
    prof["cum_share"] = prof["sales_amt"].cumsum() / (prof["sales_amt"].sum() or 1)
    abc = (prof.groupby("abc_class", as_index=False)
           .agg(products=("product_id", "count"), revenue=("sales_amt", "sum")))
    bcg = (prof.groupby("bcg_class", as_index=False)
           .agg(products=("product_id", "count"), revenue=("sales_amt", "sum")))

    return {
        "pareto": {
            "names": prof["product_name"].tolist(),
            "values": [float(v) for v in prof["sales_amt"]],
            "cum": [float(v) for v in prof["cum_share"]],
        },
        "abc": {"classes": abc["abc_class"].tolist(),
                "products": [int(v) for v in abc["products"]],
                "revenue": [float(v) for v in abc["revenue"]]},
        "bcg": [{"name": r["product_name"], "category": r["category"],
                 "type": r["product_type"], "class": r["bcg_class"],
                 "x": float(r["sales_amt"]), "y": float(r["growth_rate"]),
                 "size": float(r["sales_amt"])} for _, r in prof.iterrows()],
        "bcg_summary": {"classes": bcg["bcg_class"].tolist(),
                        "products": [int(v) for v in bcg["products"]],
                        "revenue": [float(v) for v in bcg["revenue"]]},
        "table": [{"rank": i + 1, "name": r["product_name"], "type": r["product_type"],
                   "category": r["category"], "qty": int(r["sales_qty"]),
                   "amt": float(r["sales_amt"]), "margin": float(r["profit_rate"]),
                   "growth": float(r["growth_rate"]), "abc": r["abc_class"],
                   "bcg": r["bcg_class"]}
                  for i, (_, r) in enumerate(prof.head(20).iterrows())],
    }


# --------------------------------------------------------------------------
# 4. 促销
# --------------------------------------------------------------------------
def promo() -> Dict[str, Any]:
    rows = db.read_df("""
        SELECT promotion_name, promotion_type, festival, order_cnt, gmv,
               discount_amt, gross_profit, profit_rate, discount_rate, roi
        FROM bloom_dw.ads_promo_roi
        ORDER BY gmv DESC LIMIT 15
    """)
    for c in ("order_cnt", "gmv", "discount_amt", "gross_profit", "profit_rate",
              "discount_rate", "roi"):
        rows[c] = pd.to_numeric(rows[c], errors="coerce").fillna(0)

    buckets = db.read_df("""
        SELECT CASE
                 WHEN f.discount_amount = 0 THEN '无折扣'
                 WHEN f.discount_amount / NULLIF(f.gross_amount,0) < 0.05 THEN '0-5%'
                 WHEN f.discount_amount / NULLIF(f.gross_amount,0) < 0.10 THEN '5-10%'
                 WHEN f.discount_amount / NULLIF(f.gross_amount,0) < 0.15 THEN '10-15%'
                 WHEN f.discount_amount / NULLIF(f.gross_amount,0) < 0.20 THEN '15-20%'
                 ELSE '20%+'
               END AS bucket,
               COUNT(DISTINCT f.order_id) AS orders,
               SUM(f.net_amount + f.freight_amount) AS gmv,
               SUM(f.gross_profit) AS profit
        FROM bloom_dw.fact_sales f
        WHERE f.order_status <> '已取消'
        GROUP BY bucket
    """)
    order = ["无折扣", "0-5%", "5-10%", "10-15%", "15-20%", "20%+"]
    buckets["ord"] = buckets["bucket"].apply(lambda x: order.index(x) if x in order else 99)
    buckets = buckets.sort_values("ord")
    for c in ("orders", "gmv", "profit"):
        buckets[c] = pd.to_numeric(buckets[c], errors="coerce").fillna(0)
    buckets["margin"] = buckets["profit"] / buckets["gmv"].replace(0, pd.NA)
    total_orders = float(buckets["orders"].sum()) or 1

    return {
        "roi": [{"name": r["promotion_name"], "type": r["promotion_type"],
                 "festival": r["festival"], "orders": int(r["order_cnt"]),
                 "gmv": float(r["gmv"]), "discount": float(r["discount_amt"]),
                 "profit": float(r["gross_profit"]), "margin": float(r["profit_rate"]),
                 "roi": float(r["roi"])} for _, r in rows.iterrows()],
        "buckets": {
            "names": buckets["bucket"].tolist(),
            "order_share": [float(v) / total_orders for v in buckets["orders"]],
            "margin": [None if pd.isna(v) else float(v) for v in buckets["margin"]],
        },
    }


# --------------------------------------------------------------------------
# 5. 渠道 / 门店
# --------------------------------------------------------------------------
def channel() -> Dict[str, Any]:
    ch = db.read_df("""
        SELECT channel_id, channel_name, channel_type, order_cnt, customer_cnt, gmv,
               aov, gmv_share, commission_amt, net_profit, net_margin
        FROM bloom_dw.ads_channel_efficiency ORDER BY gmv DESC
    """)
    for c in ("order_cnt", "customer_cnt", "gmv", "aov", "gmv_share", "commission_amt",
              "net_profit", "net_margin"):
        ch[c] = pd.to_numeric(ch[c], errors="coerce").fillna(0)

    store = db.read_df("""
        SELECT s.store_name, s.city, s.area_sqm, s.staff_count,
               SUM(f.net_amount + f.freight_amount) AS gmv,
               COUNT(DISTINCT f.order_id) AS orders
        FROM bloom_dw.fact_sales f
        JOIN bloom_dw.dim_store s ON s.store_sk = f.store_sk
        WHERE f.order_status <> '已取消'
        GROUP BY s.store_name, s.city, s.area_sqm, s.staff_count
        ORDER BY gmv DESC
    """)
    for c in ("gmv", "orders", "area_sqm", "staff_count"):
        store[c] = pd.to_numeric(store[c], errors="coerce").fillna(0)
    store["per_sqm"] = (store["gmv"] / store["area_sqm"].replace(0, pd.NA)).fillna(0)

    return {
        "table": [{"id": r["channel_id"], "name": r["channel_name"], "type": r["channel_type"],
                   "orders": int(r["order_cnt"]), "customers": int(r["customer_cnt"]),
                   "gmv": float(r["gmv"]), "aov": float(r["aov"]),
                   "share": float(r["gmv_share"]), "commission": float(r["commission_amt"]),
                   "net_profit": float(r["net_profit"]), "margin": float(r["net_margin"])}
                  for _, r in ch.iterrows()],
        "scatter": [{"name": r["channel_name"], "aov": float(r["aov"]),
                     "margin": float(r["net_margin"]), "gmv": float(r["gmv"]),
                     "type": r["channel_type"]} for _, r in ch.iterrows()],
        "store": {
            "names": store["store_name"].tolist(),
            "gmv": [float(v) for v in store["gmv"]],
            "per_sqm": [float(v) for v in store["per_sqm"]],
            "orders": [int(v) for v in store["orders"]],
        },
    }


# --------------------------------------------------------------------------
# 6. 库存
# --------------------------------------------------------------------------
def inventory() -> Dict[str, Any]:
    df = db.read_df("""
        SELECT category, sku_cnt, out_qty, on_hand_value, turnover_days, waste_rate,
               waste_amt, low_stock_ratio
        FROM bloom_dw.ads_inventory_analysis ORDER BY out_qty DESC
    """)
    for c in ("sku_cnt", "out_qty", "on_hand_value", "turnover_days", "waste_rate",
              "waste_amt", "low_stock_ratio"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    slow = db.read_df("""
        SELECT product_name, category, sales_qty, sales_amt, profit_rate
        FROM bloom_dw.ads_product_performance
        WHERE abc_class = 'C' ORDER BY sales_qty ASC LIMIT 10
    """)
    return {
        "categories": df["category"].tolist(),
        "turnover": [float(v) for v in df["turnover_days"]],
        "waste_rate": [float(v) for v in df["waste_rate"]],
        "waste_amt": [float(v) for v in df["waste_amt"]],
        "sku_cnt": [int(v) for v in df["sku_cnt"]],
        "slow": [{"name": r["product_name"], "category": r["category"],
                  "qty": int(r["sales_qty"]), "amt": float(r["sales_amt"])}
                 for _, r in slow.iterrows()],
    }


# --------------------------------------------------------------------------
# 7. 预测
# --------------------------------------------------------------------------
def forecast() -> Dict[str, Any]:
    fc = db.read_df("""
        SELECT forecast_date, predicted_gmv, lower_bound, upper_bound, festival_name
        FROM bloom_dw.ads_forecast ORDER BY forecast_date
    """)
    hist = db.read_df("""
        SELECT full_date, gmv FROM bloom_dw.dws_daily_features
        ORDER BY full_date DESC LIMIT 90
    """).sort_values("full_date")
    return {
        "history": {"dates": [str(d) for d in hist["full_date"]],
                    "values": [float(v) for v in pd.to_numeric(hist["gmv"], errors="coerce")]},
        "forecast": {
            "dates": [str(d) for d in fc["forecast_date"]],
            "values": [float(v) for v in pd.to_numeric(fc["predicted_gmv"], errors="coerce")],
            "lower": [float(v) for v in pd.to_numeric(fc["lower_bound"], errors="coerce")],
            "upper": [float(v) for v in pd.to_numeric(fc["upper_bound"], errors="coerce")],
            "festivals": fc["festival_name"].fillna("").tolist(),
        },
        "metrics": {"mape": 10.11, "rmse": 2406,
                    "sum": float(pd.to_numeric(fc["predicted_gmv"], errors="coerce").sum())},
    }
