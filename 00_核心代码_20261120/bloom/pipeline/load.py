"""把 CSV 装载进 MySQL：业务库（干净）+ ODS（脏原始）。

三个关键工程细节
----------------
1. **加载顺序**：按外键依赖排序，先维度后事实，避免外键报错；
2. **数据契约**：装载前用 information_schema 校验 CSV 表头与表结构一致，
   字段一错就立刻报错，而不是等到分析阶段发现数字不对；
3. **空值处理**：pandas 会把空串读成 NaN，直接写库会变成字符串 'nan'，
   这里统一转成 None（SQL NULL）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from ..config import DATA_CLEAN_DIR, DATA_RAW_DIR, MYSQL_DW_DB, MYSQL_OLTP_DB
from ..logger import get_logger
from .. import db

log = get_logger("load")

# 业务库装载顺序（满足外键依赖）
OLTP_LOAD_ORDER: List[str] = [
    "suppliers", "channels", "stores", "employees", "flower_skus", "bundles",
    "bundle_items", "customers", "promotions", "coupons", "orders", "order_items",
    "order_promotions", "refunds", "inventory_daily", "order_status_log",
]

# ODS 装载（原始导出 → 贴源表），后三者是脏数据
ODS_TABLES: Dict[str, str] = {
    "orders_raw": "ods_orders_raw",
    "order_items_raw": "ods_order_items_raw",
    "customers_raw": "ods_customers_raw",
    "products_raw": "ods_products_raw",
}


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"数据文件不存在：{path}\n请先执行：python run.py gen（或双击 03 目录下的生成脚本）"
        )
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def _to_rows(df: pd.DataFrame):
    """DataFrame → 可安全写库的元组序列（NaN/NaT → None）。"""
    obj = df.astype(object).where(pd.notna(df), None)
    for row in obj.itertuples(index=False, name=None):
        yield tuple(
            None if (isinstance(v, float) and pd.isna(v)) else v
            for v in row
        )


def _truncate_all(conn, database: str, tables: List[str]) -> None:
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for t in tables:
            cur.execute(f"TRUNCATE TABLE `{database}`.`{t}`")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")


def load_oltp() -> Dict[str, int]:
    """装载业务库（可重复执行，先清空再写入）。"""
    log.info("=" * 70)
    log.info("装载业务库 bloom_oltp（%d 张表）", len(OLTP_LOAD_ORDER))
    log.info("=" * 70)

    counts: Dict[str, int] = {}
    with db.connect(MYSQL_OLTP_DB) as conn:
        _truncate_all(conn, MYSQL_OLTP_DB, OLTP_LOAD_ORDER)
        for table in OLTP_LOAD_ORDER:
            df = _read(DATA_CLEAN_DIR / f"{table}.csv")
            db.assert_schema_matches(conn, MYSQL_OLTP_DB, table, list(df.columns))
            n = db.bulk_insert(conn, table, list(df.columns), _to_rows(df))
            counts[table] = n
            log.info("  ✓ %-20s %8d 行", table, n)
        conn.commit()

    total = sum(counts.values())
    log.info("业务库装载完成：共 %d 行", total)
    return counts


def load_ods() -> Dict[str, int]:
    """装载 ODS 贴源层（脏数据原样入库，不做任何清洗）。"""
    log.info("=" * 70)
    log.info("装载 ODS 贴源层 bloom_dw（模拟业务系统导出）")
    log.info("=" * 70)

    batch_id = f"B{datetime.now():%Y%m%d%H%M%S}"
    counts: Dict[str, int] = {}

    with db.connect(MYSQL_DW_DB) as conn:
        _truncate_all(conn, MYSQL_DW_DB, list(ODS_TABLES.values()))
        for csv_name, table in ODS_TABLES.items():
            df = _read(DATA_RAW_DIR / f"{csv_name}.csv")
            df = df.copy()
            df["batch_id"] = batch_id
            df["load_time"] = datetime.now()
            db.assert_schema_matches(conn, MYSQL_DW_DB, table, list(df.columns))
            n = db.bulk_insert(conn, table, list(df.columns), _to_rows(df))
            counts[table] = n
            log.info("  ✓ %-20s %8d 行", table, n)
        conn.commit()

    log.info("ODS 装载完成，批次号 %s", batch_id)
    return counts
