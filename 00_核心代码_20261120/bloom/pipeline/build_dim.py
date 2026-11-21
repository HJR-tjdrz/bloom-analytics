"""维度层构建：日期维、客户维（SCD2）、商品维。

为什么这三张维度用 Python 而不用 SQL？
--------------------------------------
- **dim_date**：需要农历节日、预售节奏、需求指数，这些是"业务日历"，
  在 Python 里维护一份配置比在 SQL 里写一堆 CASE WHEN 可读性高得多；
- **dim_customer（SCD2）**：拉链表要判断"哪个版本在哪个时间段有效"，
  用 DataFrame 做逐行状态推进，逻辑更直白、也更好写单元测试；
- **dim_product**：SKU 与花束两种粒度需要先 union 再打价格带标签。

其余维度（渠道/门店/促销）结构简单，交给 06_构建数仓.sql 用 INSERT…SELECT 完成，
既能少写重复代码，也能体现 SQL 能力——两种方式各取所长，不是非此即彼。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd

from ..config import DATA_CLEAN_DIR, MYSQL_DW_DB
from ..logger import get_logger
from .. import db

log = get_logger("build_dim")


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA_CLEAN_DIR / f"{name}.csv", encoding="utf-8-sig", low_memory=False)


# --------------------------------------------------------------------------
# 日期维度
# --------------------------------------------------------------------------
def build_dim_date() -> pd.DataFrame:
    df = _read("dim_date")
    df["full_date"] = pd.to_datetime(df["full_date"]).dt.date
    return df


# --------------------------------------------------------------------------
# 客户维度（SCD2 拉链表）
# --------------------------------------------------------------------------
LEVEL_UP = {
    "普通会员": "银卡会员",
    "银卡会员": "金卡会员",
    "金卡会员": "钻石会员",
    "钻石会员": "钻石会员",
    "企业客户": "企业客户",
}


def build_dim_customer(rng: np.random.Generator, upgrade_ratio: float = 0.12) -> pd.DataFrame:
    """生成带历史版本的客户维表。

    业务含义：客户在 2025 年升级了会员等级，那么 2025 年之前的历史订单
    仍然应该按"旧等级"计算权益。SCD2 用 valid_from/valid_to 记录这个变化，
    事实表通过 customer_sk 关联"下单当时"的版本。
    """
    cust = _read("customers")
    cust["register_date"] = pd.to_datetime(cust["register_date"])

    base_cols = ["customer_id", "customer_name", "customer_type", "gender", "city",
                 "province", "member_level", "source_channel", "register_date"]

    v1 = cust[base_cols].copy()
    v1["valid_from"] = v1["register_date"]
    v1["valid_to"] = pd.NaT
    v1["is_current"] = 1
    v1["version"] = 1

    # 选取一部分客户做会员升级，形成第二版本
    n_up = int(len(cust) * upgrade_ratio)
    up_idx = rng.choice(len(cust), size=n_up, replace=False)
    upgrades = cust.iloc[up_idx][base_cols].copy()

    # 升级时间：注册满 180 天之后，且不早于 2025-03-01
    offset_days = rng.integers(180, 720, size=n_up)
    upgrade_at = [
        max(reg + timedelta(days=int(off)), datetime(2025, 3, 1))
        for reg, off in zip(upgrades["register_date"], offset_days)
    ]
    upgrades["valid_from"] = upgrade_at
    upgrades["valid_to"] = pd.NaT
    upgrades["is_current"] = 1
    upgrades["version"] = 2
    upgrades["member_level"] = upgrades["member_level"].map(
        lambda lv: LEVEL_UP.get(str(lv), str(lv))
    )

    # 老版本失效：valid_to = 升级时间，is_current = 0
    v1 = v1.set_index("customer_id")
    for cid, ts in zip(upgrades["customer_id"], upgrades["valid_from"]):
        if cid in v1.index:
            v1.at[cid, "valid_to"] = ts
            v1.at[cid, "is_current"] = 0
    v1 = v1.reset_index()

    dim = pd.concat([v1, upgrades], ignore_index=True)
    dim["valid_from"] = pd.to_datetime(dim["valid_from"])
    dim["valid_to"] = pd.to_datetime(dim["valid_to"])
    dim["register_date"] = pd.to_datetime(dim["register_date"]).dt.date
    return dim[
        ["customer_id", "customer_name", "customer_type", "gender", "city", "province",
         "member_level", "source_channel", "register_date", "valid_from", "valid_to",
         "is_current", "version"]
    ].sort_values(["customer_id", "version"])


# --------------------------------------------------------------------------
# 商品维度（SKU + 花束统一）
# --------------------------------------------------------------------------
def build_dim_product() -> pd.DataFrame:
    skus = _read("flower_skus")
    bundles = _read("bundles")

    sku_part = pd.DataFrame({
        "product_id": skus["sku_id"],
        "product_type": "SKU",
        "product_name": skus["sku_name"],
        "category": skus["category"],
        "scene": None,
        "unit_price": skus["list_price"],
        "cost_price": skus["cost_price"],
        "launch_date": None,
        "status": skus["status"],
    })
    bundle_part = pd.DataFrame({
        "product_id": bundles["bundle_id"],
        "product_type": "BUNDLE",
        "product_name": bundles["bundle_name"],
        "category": bundles["category"],
        "scene": bundles["scene"],
        "unit_price": bundles["list_price"],
        "cost_price": bundles["cost_price"],
        "launch_date": bundles["launch_date"],
        "status": bundles["status"],
    })
    dim = pd.concat([sku_part, bundle_part], ignore_index=True)
    dim["launch_date"] = pd.to_datetime(dim["launch_date"], errors="coerce").dt.date
    dim["price_band"] = None  # 由 06_构建数仓.sql 调用 fn_price_band() 统一填充
    return dim


# --------------------------------------------------------------------------
# 装载
# --------------------------------------------------------------------------
def _insert(conn, table: str, df: pd.DataFrame) -> int:
    from .load import _to_rows

    db.assert_schema_matches(conn, MYSQL_DW_DB, table, list(df.columns))
    return db.bulk_insert(conn, table, list(df.columns), _to_rows(df))


def load_dimensions(seed: int = 20241118) -> Dict[str, int]:
    """清空并装载三张 Python 侧维度表。"""
    log.info("=" * 70)
    log.info("装载维度层（dim_date / dim_customer / dim_product）")
    log.info("=" * 70)
    rng = np.random.default_rng(seed)
    counts: Dict[str, int] = {}

    dim_date = build_dim_date()
    dim_customer = build_dim_customer(rng)
    dim_product = build_dim_product()

    with db.connect(MYSQL_DW_DB) as conn:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for t in ("dim_date", "dim_customer", "dim_product"):
                cur.execute(f"TRUNCATE TABLE `{t}`")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")

        counts["dim_date"] = _insert(conn, "dim_date", dim_date)
        counts["dim_customer"] = _insert(conn, "dim_customer", dim_customer)
        counts["dim_product"] = _insert(conn, "dim_product", dim_product)
        conn.commit()

    for k, v in counts.items():
        log.info("  ✓ %-16s %8d 行", k, v)
    log.info("维度层装载完成")
    return counts
