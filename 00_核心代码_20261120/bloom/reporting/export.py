"""BI 工具数据源导出：把数仓结果导出成 Tableau / Power BI / R 可直接读取的 CSV。

为什么还要单独导出？
------------------
BI 工具当然可以直连 MySQL，但实际交付时经常遇到三个问题：
1. 客户/同事没有数据库账号，或网络不通；
2. 直连会实时打库，几个人同时刷新就影响线上；
3. 不同 BI 工具对 MySQL 驱动的版本要求不一样，越简单越不容易出错。

所以标准做法是：**宽表导出 CSV（utf-8-sig，Excel 双击不乱码）+ 保留直连说明**，
两条路都留，让用户自己选。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from ..config import PROJECT_ROOT
from ..logger import get_logger
from ..analysis import base

log = get_logger("reporting.export")

BI_DIR = PROJECT_ROOT / "10_BI工具配套_20261121"
DATA_DIR = BI_DIR / "data"

# (导出文件名, SQL, 说明)
EXPORTS: list[tuple[str, str]] = [
    ("dim_date", "SELECT * FROM bloom_dw.dim_date ORDER BY date_key"),
    ("ads_kpi_monthly", "SELECT * FROM bloom_dw.ads_kpi_monthly ORDER BY month_key"),
    ("ads_product_performance",
     "SELECT * FROM bloom_dw.ads_product_performance ORDER BY sales_amt DESC"),
    ("ads_channel_efficiency",
     "SELECT * FROM bloom_dw.ads_channel_efficiency ORDER BY gmv DESC"),
    ("ads_promo_roi", "SELECT * FROM bloom_dw.ads_promo_roi ORDER BY gmv DESC"),
    ("ads_inventory_analysis",
     "SELECT * FROM bloom_dw.ads_inventory_analysis ORDER BY waste_amt DESC"),
    ("ads_forecast", "SELECT * FROM bloom_dw.ads_forecast ORDER BY forecast_date"),
    ("ads_assoc_rules", "SELECT * FROM bloom_dw.ads_assoc_rules ORDER BY lift DESC"),
    ("dws_daily_features",
     "SELECT date_key, full_date, order_cnt, customer_cnt, qty, gmv, aov, gross_profit, "
     "profit_rate, is_weekend, is_holiday, festival_name, demand_index "
     "FROM bloom_dw.dws_daily_features ORDER BY full_date"),
    ("ads_customer_rfm_segments",
     "SELECT rfm_segment, COUNT(*) AS customers, SUM(monetary) AS gmv, "
     "AVG(recency_days) AS avg_r, AVG(frequency) AS avg_f, AVG(monetary) AS avg_m "
     "FROM bloom_dw.ads_customer_rfm GROUP BY rfm_segment"),
    ("fact_sales_sample",
     "SELECT f.order_id, d.full_date, dc.customer_id, dc.customer_type, dc.city, "
     "p.product_name, p.product_type, p.category, s.store_name, ch.channel_name, "
     "f.quantity, f.net_amount, f.discount_amount, f.freight_amount, f.cost_amount, "
     "f.gross_profit, f.order_status "
     "FROM bloom_dw.fact_sales f "
     "JOIN bloom_dw.dim_date d ON d.date_key = f.date_key "
     "JOIN bloom_dw.dim_customer dc ON dc.customer_sk = f.customer_sk "
     "JOIN bloom_dw.dim_product p ON p.product_sk = f.product_sk "
     "JOIN bloom_dw.dim_store s ON s.store_sk = f.store_sk "
     "JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk "
     "LIMIT 5000"),
]


def _write_dict(frames: Dict[str, pd.DataFrame]) -> None:
    """自动生成 BI 数据字典：每个 CSV 的字段、类型、缺失率、样例值。"""
    lines = [
        "# BI 数据源字段字典\n",
        "> 本文件由 `bloom/reporting/export.py` 自动生成，与导出的 CSV 完全一致。\n",
    ]
    for name, df in frames.items():
        lines.append(f"## `{name}.csv`（{len(df):,} 行 × {len(df.columns)} 列）\n")
        lines.append("| 字段 | 类型 | 缺失率 | 样例值 |")
        lines.append("| --- | --- | ---: | --- |")
        for col in df.columns:
            series = df[col]
            null_rate = float(series.isna().mean()) if len(series) else 0.0
            sample = series.dropna().iloc[0] if series.notna().any() else ""
            sample = str(sample)[:28].replace("|", "/")
            lines.append(f"| `{col}` | {series.dtype} | {null_rate * 100:.1f}% | {sample} |")
        lines.append("")
    (BI_DIR / "05_数据字典_BI用.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("  📄 05_数据字典_BI用.md")


def export_all() -> Dict[str, int]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info("▸ 导出 BI 数据源 → %s", DATA_DIR)
    counts: Dict[str, int] = {}
    frames: Dict[str, pd.DataFrame] = {}
    for name, sql in EXPORTS:
        df = base.read_df(sql)
        frames[name] = df
        path = DATA_DIR / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        counts[name] = len(df)
        log.info("  📤 %-30s %7d 行  %5.1f KB", path.name, len(df),
                 path.stat().st_size / 1024)
    _write_dict(frames)
    return counts
