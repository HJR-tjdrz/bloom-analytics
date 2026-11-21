"""特征工程：客户画像、商品画像、日粒度时序特征。

为什么特征要单独一层？
--------------------
模型（RFM 分群、波士顿矩阵、销量预测）需要的不是"原始明细"，
而是"每个客户一行 / 每个商品一行 / 每天一行"的特征表。
把特征沉淀到 DWS 层，有三个好处：

1. 第 3 轮建模直接读宽表，不用每次重写 SQL；
2. 特征口径唯一（如"最近一次购买距今天数"只有一种算法）；
3. 便于做特征巡检（分布是否漂移、空值率是否异常）。

三个原则：
- **可解释**：每个特征都能说出业务含义；
- **可复算**：全部从事实表推导，改一行代码就能全量重算；
- **不 leakage**：预测类特征只用"当前及过去"的数据（滞后/移动平均/YoY 都遵循时间顺序）。
"""
from __future__ import annotations

from datetime import date
from typing import Dict

import numpy as np
import pandas as pd

from .. import db
from ..config import DATA_END, MYSQL_DW_DB, MYSQL_OLTP_DB
from ..logger import get_logger
from .clean import write_frame

log = get_logger("features")

CALC_DATE = date(2026, 11, 19)  # 数据截止日 + 1 天，作为"今天"


def _read(sql: str, database: str = MYSQL_DW_DB) -> pd.DataFrame:
    return db.read_df(sql, database=database)


def _numeric(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """DECIMAL 取回来是 Decimal 对象，先转 float 再参与计算（否则 .round() 直接报错）。"""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


# --------------------------------------------------------------------------
# 1. 客户画像
# --------------------------------------------------------------------------
def build_customer_profile() -> int:
    log.info("构建客户画像 dws_customer_profile …")
    sales = _read("""
        SELECT dc.customer_id, f.order_id, d.full_date, d.is_festival,
               f.net_amount, f.freight_amount, f.quantity,
               p.category, p.price_band, ch.channel_name
        FROM fact_sales f
        JOIN dim_customer dc ON dc.customer_sk = f.customer_sk
        JOIN dim_date     d  ON d.date_key     = f.date_key
        JOIN dim_product  p  ON p.product_sk   = f.product_sk
        JOIN dim_channel  ch ON ch.channel_sk  = f.channel_sk
        WHERE f.order_status <> '已取消'
    """)
    sales = _numeric(sales, ["net_amount", "freight_amount", "quantity", "is_festival"])
    sales["amount"] = sales["net_amount"] + sales["freight_amount"]

    # 订单粒度（一单可能多行明细）
    order_level = (
        sales.groupby(["customer_id", "order_id"], as_index=False)
        .agg(order_amt=("amount", "sum"), order_date=("full_date", "max"),
             is_festival=("is_festival", "max"), qty=("quantity", "sum"))
    )

    profile = (
        order_level.groupby("customer_id", as_index=False)
        .agg(first_order_date=("order_date", "min"),
             last_order_date=("order_date", "max"),
             frequency=("order_id", "nunique"),
             monetary=("order_amt", "sum"),
             total_qty=("qty", "sum"),
             active_days=("order_date", "nunique"),
             festival_amt=("order_amt", lambda s: float(s[order_level.loc[s.index, "is_festival"] == 1].sum())))
    )
    profile["monetary"] = profile["monetary"].round(2)
    profile["avg_order_value"] = (profile["monetary"] / profile["frequency"]).round(2)
    profile["recency_days"] = profile["last_order_date"].map(
        lambda d: (CALC_DATE - d).days if pd.notna(d) else None)
    span = (pd.to_datetime(profile["last_order_date"]) - pd.to_datetime(profile["first_order_date"])).dt.days
    profile["avg_interval_days"] = np.where(
        profile["frequency"] > 1,
        (span / (profile["frequency"] - 1)).round(1),
        np.nan,
    )
    profile["festival_ratio"] = (profile["festival_amt"] / profile["monetary"]).round(4)

    # 偏好：按销售额取众数
    def _prefer(col: str, name: str) -> pd.DataFrame:
        tmp = (sales.groupby(["customer_id", col], as_index=False)["amount"].sum()
               .sort_values("amount", ascending=False)
               .drop_duplicates("customer_id"))
        return tmp[["customer_id", col]].rename(columns={col: name})

    profile = profile.merge(_prefer("category", "prefer_category"), on="customer_id", how="left")
    profile = profile.merge(_prefer("channel_name", "prefer_channel"), on="customer_id", how="left")
    profile = profile.merge(_prefer("price_band", "prefer_price_band"), on="customer_id", how="left")

    # 退款次数、用券次数
    refunds = _read("""
        SELECT o.customer_id, COUNT(*) AS refund_cnt
        FROM refunds r JOIN orders o ON o.order_id = r.order_id
        GROUP BY o.customer_id
    """, MYSQL_OLTP_DB)
    coupons = _read("""
        SELECT customer_id, COUNT(*) AS coupon_cnt
        FROM orders WHERE coupon_id IS NOT NULL AND customer_id IS NOT NULL
        GROUP BY customer_id
    """, MYSQL_OLTP_DB)
    profile = profile.merge(refunds, on="customer_id", how="left")
    profile = profile.merge(coupons, on="customer_id", how="left")
    profile[["refund_cnt", "coupon_cnt"]] = profile[["refund_cnt", "coupon_cnt"]].fillna(0).astype(int)

    # 基础属性（当前版本）
    dims = _read("""
        SELECT customer_id, customer_type, city, member_level
        FROM dim_customer WHERE is_current = 1
    """)
    profile = profile.merge(dims, on="customer_id", how="left")

    # 生命周期
    def _stage(row) -> str:
        r = row["recency_days"]
        if pd.isna(r):
            return "未分层"
        if r <= 90:
            return "新客" if row["frequency"] <= 1 else "活跃"
        if r <= 180:
            return "沉睡"
        return "流失"

    profile["lifecycle_stage"] = profile.apply(_stage, axis=1)
    profile["high_value_flag"] = (profile["monetary"] >= 5000).astype(int)
    profile["calc_date"] = CALC_DATE

    cols = ["customer_id", "customer_type", "city", "member_level", "first_order_date",
            "last_order_date", "recency_days", "frequency", "monetary", "avg_order_value",
            "total_qty", "active_days", "avg_interval_days", "prefer_category",
            "prefer_channel", "prefer_price_band", "festival_ratio", "refund_cnt",
            "coupon_cnt", "lifecycle_stage", "high_value_flag", "calc_date"]
    profile = profile[cols].sort_values("customer_id").reset_index(drop=True)
    return write_frame("dws_customer_profile", profile)


# --------------------------------------------------------------------------
# 2. 商品画像
# --------------------------------------------------------------------------
def build_product_profile() -> int:
    log.info("构建商品画像 dws_product_profile …")
    sales = _read("""
        SELECT p.product_id, p.product_name, p.product_type, p.category, p.price_band,
               f.order_id, f.customer_sk, f.quantity, f.net_amount, f.cost_amount,
               f.gross_profit, d.full_date, d.is_festival
        FROM fact_sales f
        JOIN dim_product p ON p.product_sk = f.product_sk
        JOIN dim_date    d ON d.date_key   = f.date_key
        WHERE f.order_status <> '已取消'
    """)
    sales = _numeric(sales, ["quantity", "net_amount", "cost_amount", "gross_profit", "is_festival"])
    sales["revenue"] = sales["net_amount"] + 0  # 商品实收（不含运费）

    profile = (
        sales.groupby("product_id", as_index=False)
        .agg(product_name=("product_name", "first"),
             product_type=("product_type", "first"),
             category=("category", "first"),
             price_band=("price_band", "first"),
             qty=("quantity", "sum"),
             revenue=("revenue", "sum"),
             cost=("cost_amount", "sum"),
             gross_profit=("gross_profit", "sum"),
             order_cnt=("order_id", "nunique"),
             customer_cnt=("customer_sk", "nunique"),
             festival_qty=("quantity", lambda s: float(s[sales.loc[s.index, "is_festival"] == 1].sum())))
    )
    # 复购客户数：买过该商品 >=2 单的客户
    per_cust = sales.groupby(["product_id", "customer_sk"])["order_id"].nunique().reset_index()
    repeat = (per_cust[per_cust["order_id"] >= 2].groupby("product_id")["customer_sk"]
              .nunique().rename("repeat_customer_cnt").reset_index())
    profile = profile.merge(repeat, on="product_id", how="left")
    profile["repeat_customer_cnt"] = profile["repeat_customer_cnt"].fillna(0).astype(int)

    profile["revenue"] = profile["revenue"].round(2)
    profile["cost"] = profile["cost"].round(2)
    profile["gross_profit"] = profile["gross_profit"].round(2)
    profile["profit_rate"] = (profile["gross_profit"] / profile["revenue"]).replace(
        [np.inf, -np.inf], np.nan).fillna(0).round(4)
    profile["festival_qty_ratio"] = (profile["festival_qty"] / profile["qty"]).fillna(0).round(4)
    profile["revenue_share"] = (profile["revenue"] / profile["revenue"].sum()).round(4)

    # 同比增长：最近 12 个月 vs 前 12 个月
    end = pd.Timestamp(DATA_END)
    cur_start = end - pd.Timedelta(days=364)
    prev_start = cur_start - pd.Timedelta(days=364)
    cur = sales[sales["full_date"] > cur_start.date()].groupby("product_id")["revenue"].sum()
    prev = sales[(sales["full_date"] > prev_start.date()) & (sales["full_date"] <= cur_start.date())] \
        .groupby("product_id")["revenue"].sum()
    growth = ((cur - prev) / prev.replace(0, np.nan)).rename("yoy_growth").reset_index()
    profile = profile.merge(growth, on="product_id", how="left")
    profile["yoy_growth"] = profile["yoy_growth"].replace([np.inf, -np.inf], np.nan).round(4)

    # ABC 分类：按销售额降序累加，80% / 95% 分界
    profile = profile.sort_values("revenue", ascending=False).reset_index(drop=True)
    cum = profile["revenue"].cumsum() / profile["revenue"].sum()
    profile["abc_class"] = np.where(cum <= 0.80, "A", np.where(cum <= 0.95, "B", "C"))
    profile["calc_date"] = CALC_DATE

    cols = ["product_id", "product_name", "product_type", "category", "price_band", "qty",
            "revenue", "cost", "gross_profit", "profit_rate", "order_cnt", "customer_cnt",
            "repeat_customer_cnt", "festival_qty_ratio", "yoy_growth", "revenue_share",
            "abc_class", "calc_date"]
    return write_frame("dws_product_profile", profile[cols])


# --------------------------------------------------------------------------
# 3. 日粒度时序特征（窗口函数实现，保证不泄漏未来数据）
# --------------------------------------------------------------------------
DAILY_SQL = """
TRUNCATE TABLE dws_daily_features;

-- 第一步：只扫一遍事实表，把基础指标落进 1053 行的小表
INSERT INTO dws_daily_features
    (date_key, full_date, order_cnt, customer_cnt, qty, gmv, aov, gross_profit, profit_rate,
     is_weekend, is_holiday, festival_name, demand_index)
SELECT
    d.date_key, v.full_date, v.order_cnt, v.customer_cnt, v.qty, v.gmv, v.aov,
    v.gross_profit, v.profit_rate,
    d.is_weekend, d.is_holiday, d.festival_name, d.demand_index
FROM bloom_dw.v_daily_kpi v
JOIN bloom_dw.dim_date d ON d.full_date = v.full_date;

-- 第二步：在 1053 行的小表上算滞后与移动平均（窗口函数），避免重复扫事实表
UPDATE dws_daily_features d
JOIN (
    SELECT date_key,
           LAG(gmv, 7)  OVER w AS lag7,
           LAG(gmv, 28) OVER w AS lag28,
           ROUND(AVG(gmv) OVER (ORDER BY full_date ROWS BETWEEN 6  PRECEDING AND CURRENT ROW), 2) AS ma7,
           ROUND(AVG(gmv) OVER (ORDER BY full_date ROWS BETWEEN 27 PRECEDING AND CURRENT ROW), 2) AS ma28
    FROM dws_daily_features
    WINDOW w AS (ORDER BY full_date)
) t ON t.date_key = d.date_key
SET d.lag_7_gmv = t.lag7,
    d.lag_28_gmv = t.lag28,
    d.ma_7_gmv = t.ma7,
    d.ma_28_gmv = t.ma28;

-- 第三步：去年同期（364 天 = 52 周，规避星期错位）用于同比
UPDATE dws_daily_features d
LEFT JOIN dws_daily_features prev
       ON prev.full_date = DATE_SUB(d.full_date, INTERVAL 364 DAY)
SET d.yoy_gmv = prev.gmv;
"""


def build_daily_features() -> int:
    log.info("构建日粒度特征 dws_daily_features …")
    for stmt in db.split_sql(DAILY_SQL):
        db.execute(stmt, database=MYSQL_DW_DB)
    row = db.fetch_one("SELECT COUNT(*) AS c FROM dws_daily_features", database=MYSQL_DW_DB)
    n = int(row["c"]) if row else 0
    log.info("  ✓ %-26s %8d 行", "dws_daily_features", n)
    return n


def build_all() -> Dict[str, int]:
    return {
        "dws_customer_profile": build_customer_profile(),
        "dws_product_profile": build_product_profile(),
        "dws_daily_features": build_daily_features(),
    }
