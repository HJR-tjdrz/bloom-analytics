"""数据工厂：一键生成全量业务数据（OLTP 干净层 + 原始导出脏层）。

调用示例
--------
>>> from bloom.datagen import generate_all
>>> tables = generate_all()
"""
from __future__ import annotations

from datetime import date
from typing import Dict

import numpy as np
import pandas as pd

from ..config import (
    BASE_DAILY_ORDERS,
    CUSTOMER_POOL_SIZE,
    DATA_END,
    DATA_START,
    SEED,
)
from ..logger import get_logger
from . import dirty, writer
from .calendar_cn import build_calendar
from .catalog import (
    build_bundles,
    build_channels,
    build_coupons,
    build_employees,
    build_promotions,
    build_skus,
    build_stores,
    build_suppliers,
)
from .customers import build_customer_pool, make_employee_names, pool_to_frame
from .operations import build_inventory, build_refunds, build_status_log
from .orders import CatalogCtx, generate_transactions

log = get_logger("datagen")

N_ENTERPRISE = 800


def build_dim_date(calendar_df: pd.DataFrame) -> pd.DataFrame:
    """把业务日历转成日期维度表（供数仓 dim_date 使用）。"""
    df = calendar_df.copy()
    df["date_key"] = df["date"].apply(lambda d: int(d.strftime("%Y%m%d")))
    df["full_date"] = df["date"]
    return df[
        ["date_key", "full_date", "year", "quarter", "month", "month_name",
         "week_of_year", "day_of_week", "day_name", "is_weekend", "is_holiday",
         "holiday_name", "is_festival", "festival_name", "season", "month_key",
         "demand_index"]
    ]


def generate_all(persist: bool = True, write_sample: bool = True) -> Dict[str, pd.DataFrame]:
    """生成全部数据表。

    Parameters
    ----------
    persist : 是否把结果写成 CSV
    write_sample : 是否额外写一份 200 行的小样本（给 GitHub 用）
    """
    rng = np.random.default_rng(SEED)
    log.info("=" * 78)
    log.info("数据工厂启动｜种子=%s｜区间=%s ~ %s", SEED, DATA_START, DATA_END)
    log.info("=" * 78)

    # ---- 1. 维度主数据 ----
    log.info("[1/7] 生成商品/渠道/门店等主数据 …")
    suppliers = build_suppliers()
    skus = build_skus()
    bundles, bom = build_bundles(skus)
    channels = build_channels()
    stores = build_stores()
    employees = make_employee_names(build_employees(), rng)
    promotions = build_promotions([DATA_START.year, DATA_START.year + 1, DATA_END.year])
    coupons = build_coupons()

    # ---- 2. 业务日历 ----
    log.info("[2/7] 构建节假日业务日历 …")
    calendar_df = build_calendar(DATA_START, DATA_END)
    festival_days = int(calendar_df["is_festival"].sum())
    log.info("  ✓ 日历 %d 天，其中节日 %d 天，峰值需求指数 %.2f",
             len(calendar_df), festival_days, calendar_df["demand_index"].max())

    # ---- 3. 客户池 ----
    log.info("[3/7] 生成客户池（个人 %d / 企业 %d）…", CUSTOMER_POOL_SIZE - N_ENTERPRISE, N_ENTERPRISE)
    pool = build_customer_pool(CUSTOMER_POOL_SIZE - N_ENTERPRISE, N_ENTERPRISE, rng)

    # ---- 4. 订单交易 ----
    log.info("[4/7] 生成订单与订单明细（这一步最耗时）…")
    ctx = CatalogCtx.build(bundles, bom, skus)
    result = generate_transactions(calendar_df, ctx, pool, promotions, coupons, rng, DATA_END)
    orders = result["orders"]
    order_items = result["order_items"]
    order_promotions = result["order_promotions"]
    customers = pool_to_frame(pool)
    log.info("  ✓ 订单 %d 单｜明细 %d 行｜客户 %d 人",
             len(orders), len(order_items), len(customers))

    # ---- 5. 履约与库存 ----
    log.info("[5/7] 生成退款、状态流转与每日库存 …")
    refunds = build_refunds(orders, rng)
    order_status_log = build_status_log(orders, employees, rng)
    inventory_daily = build_inventory(orders, order_items, ctx, DATA_START, DATA_END, rng)
    log.info("  ✓ 退款 %d 单｜状态日志 %d 行｜库存 %d 行",
             len(refunds), len(order_status_log), len(inventory_daily))

    # ---- 6. 组装 ----
    clean: Dict[str, pd.DataFrame] = {
        "suppliers": suppliers,
        "channels": channels,
        "stores": stores,
        "employees": employees,
        "flower_skus": skus,
        "bundles": bundles,
        "bundle_items": bom,
        "customers": customers,
        "promotions": promotions,
        "coupons": coupons,
        "orders": orders,
        "order_items": order_items,
        "order_promotions": order_promotions,
        "refunds": refunds,
        "order_status_log": order_status_log,
        "inventory_daily": inventory_daily,
        "dim_date": build_dim_date(calendar_df),
    }

    # ---- 7. 脏数据注入 + 落盘 ----
    log.info("[6/7] 注入数据质量问题（模拟业务系统导出）…")
    raw_tables, dq_manifest = dirty.apply_all(orders, customers, skus, rng, order_items)
    for item in dq_manifest:
        log.info("  · %s %-24s 影响 %6d 行：%s",
                 item["rule"], item["field"], item["affected"], item["desc"])

    if persist:
        log.info("[7/7] 落盘 …")
        writer.write_clean(clean)
        writer.write_raw(raw_tables)
        if write_sample:
            writer.write_samples(clean)
        manifest = writer.build_manifest(
            clean, raw_tables, dq_manifest,
            {
                "seed": SEED,
                "data_start": str(DATA_START),
                "data_end": str(DATA_END),
                "base_daily_orders": BASE_DAILY_ORDERS,
                "repeat_order_ratio": 0.72,
                "n_personal_customers": CUSTOMER_POOL_SIZE - N_ENTERPRISE,
                "n_enterprise_customers": N_ENTERPRISE,
            },
        )
        writer.write_manifest(manifest)

    log.info("数据工厂完成 ✓")
    return clean
