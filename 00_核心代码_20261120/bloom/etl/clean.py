"""ODS → DWD 清洗链路。

清洗哲学
--------
**能修则修并留痕，不能修则隔离并留痕，绝不静默改数、也绝不静默丢数。**

- 修复：格式归一、别名映射、维表回填——每行在 `repair_flags` 里记下改了什么；
- 隔离：金额为负、时间越界、数量非正这类"真值已损坏"的行，
  进 `dwd_rejects` 并带原始 JSON 快照，既不参与经营口径，也能人工复核；
- 保留：像"已完成但缺配送时间"这种**事实缺失**（不是错误），
  保留行、打标记，分析时按标记排除——不能为了好看的报表去编造时间。

清洗规则编号 E01–E19 与 `01_清洗规则清单.md` 一一对应。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import db
from ..config import MYSQL_DW_DB
from ..logger import get_logger

log = get_logger("etl")

STATUS_MAP = {
    "已完成": "已完成", "COMPLETED": "已完成", "完成": "已完成",
    "已取消": "已取消", "CANCELLED": "已取消",
    "已退款": "已退款", "REFUNDED": "已退款",
    "已付款": "已付款", "制作中": "制作中", "待配送": "待配送",
}
LEVEL_MAP = {
    "普通会员": "普通会员", "普通": "普通会员",
    "银卡会员": "银卡会员", "SILVER": "银卡会员",
    "金卡会员": "金卡会员", "金卡": "金卡会员",
    "钻石会员": "钻石会员", "DIAMOND": "钻石会员",
    "企业客户": "企业客户", "企业": "企业客户",
}
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M", "%Y年%m月%d日",
    "%d/%m/%Y %H:%M", "%d/%m/%Y",
)
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%d/%m/%Y")

# 清洗规则清单（代码 → 说明），文档与报告都从这里取，避免两处维护
CLEAN_RULES: Dict[str, str] = {
    "E01": "订单重复行去重（保留首条）",
    "E02": "下单时间多格式解析归一为 DATETIME",
    "E03": "时间越界（早于 2024-01-01 或晚于数据截止）隔离",
    "E04": "实付金额 ≤0 或与「原价-优惠+运费」不勾稽，整单隔离",
    "E05": "订单状态别名/空格/英文映射为标准枚举",
    "E06": "客户ID缺失填 UNKNOWN 占位并打标（不丢单）",
    "E07": "城市名去空白/全角空格、修正「市市」重复后缀",
    "E08": "手机号正则校验，非法置 NULL（不伪造）",
    "E09": "会员等级别名映射为标准 5 档",
    "E10": "备注乱码/emoji 置 NULL 并打标",
    "E11": "商品价格异常用同品类中位数回填",
    "E12": "客户重复记录去重",
    "E13": "明细重复行去重",
    "E14": "明细数量 ≤0 隔离",
    "E15": "明细缺订单号（孤立行）隔离",
    "E16": "明细单价为 0 用商品主数据门市价回填",
    "E17": "行金额与 数量×单价 不一致时重算",
    "E18": "明细所属订单已被隔离时，明细一并隔离",
    "E19": "已完成但缺配送时间：保留并打标（事实缺失，不编造）",
}


@dataclass
class CleanStats:
    batch: str = ""
    rows_in: Dict[str, int] = field(default_factory=dict)
    dedupe: Dict[str, int] = field(default_factory=dict)
    rejects: Dict[str, int] = field(default_factory=dict)
    repairs: Dict[str, int] = field(default_factory=dict)
    rows_out: Dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def read_table(table: str) -> pd.DataFrame:
    """读取整表。用 db.read_df 而不是 pd.read_sql（DictCursor 会导致列名错位）。"""
    return db.read_df(f"SELECT * FROM `{table}`", database=MYSQL_DW_DB)


def parse_datetime(value: Any, formats=DATETIME_FORMATS) -> Optional[pd.Timestamp]:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in formats:
        try:
            return pd.Timestamp(datetime.strptime(text, fmt))
        except (ValueError, TypeError):
            continue
    ts = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(ts) else pd.Timestamp(ts)


def _map_with_cache(series: pd.Series, mapping: Dict[str, str], cache: Dict[str, str]) -> pd.Series:
    def _one(v):
        raw = str(v).strip()
        key = raw.replace(" ", "").upper()
        if key in cache:
            return cache[key]
        result = mapping.get(key, "未知")
        cache[key] = result
        return result

    return series.map(_one)


def _flags(labels: List[Tuple[str, Any]]) -> pd.Series:
    """把多个布尔掩码合成修复标记列（如 'E02,E06'）。索引与掩码保持一致。"""
    if not labels:
        return pd.Series(dtype=object)
    index = labels[0][1].index
    out: List[List[str]] = [[] for _ in range(len(index))]
    for code, mask in labels:
        for pos in np.flatnonzero(np.asarray(mask)):
            out[pos].append(code)
    return pd.Series([",".join(v) if v else None for v in out], index=index)


def _to_rows(df: pd.DataFrame):
    obj = df.astype(object).where(pd.notna(df), None)
    for row in obj.itertuples(index=False, name=None):
        yield tuple(row)


def write_frame(table: str, df: pd.DataFrame) -> int:
    with db.connect(MYSQL_DW_DB) as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE `{table}`")
        n = db.bulk_insert(conn, table, list(df.columns), _to_rows(df), batch_size=2000)
        conn.commit()
    log.info("  ✓ %-26s %8d 行", table, n)
    return n


def _json_safe(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (pd.Timestamp, datetime, date)):
            out[k] = str(v)
        elif isinstance(v, (np.integer, np.floating)):
            out[k] = v.item()
        elif v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
            out[k] = None
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_clean() -> CleanStats:
    batch = f"ETL{datetime.now():%Y%m%d%H%M%S}"
    stats = CleanStats(batch=batch)
    log.info("=" * 70)
    log.info("ODS → DWD 清洗开始，批次 %s", batch)
    log.info("=" * 70)

    raw_orders = read_table("ods_orders_raw")
    raw_items = read_table("ods_order_items_raw")
    raw_customers = read_table("ods_customers_raw")
    raw_products = read_table("ods_products_raw")
    stats.rows_in = {
        "orders": len(raw_orders), "order_items": len(raw_items),
        "customers": len(raw_customers), "products": len(raw_products),
    }
    log.info("读入 ODS：订单 %d / 明细 %d / 客户 %d / 商品 %d",
             len(raw_orders), len(raw_items), len(raw_customers), len(raw_products))

    rejects: List[dict] = []
    products = _clean_products(raw_products, stats)
    customers = _clean_customers(raw_customers, stats)
    orders = _clean_orders(raw_orders, stats, rejects, batch)
    items = _clean_items(raw_items, orders, stats, rejects, batch)

    log.info("写入 DWD 清洗层 …")
    stats.rows_out["orders"] = write_frame("dwd_orders_clean", orders)
    stats.rows_out["order_items"] = write_frame("dwd_order_items_clean", items)
    stats.rows_out["customers"] = write_frame("dwd_customers_clean", customers)
    stats.rows_out["products"] = write_frame("dwd_products_clean", products)

    if rejects:
        rej = pd.DataFrame(rejects)
        with db.connect(MYSQL_DW_DB) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE dwd_rejects")
            db.bulk_insert(conn, "dwd_rejects",
                           ["batch_id", "source_table", "biz_key", "reason_code",
                            "reason_desc", "payload"], _to_rows(rej), batch_size=500)
            conn.commit()
        log.info("  ✓ %-26s %8d 行（异常隔离）", "dwd_rejects", len(rej))

    log.info("清洗完成：订单 %d → %d，明细 %d → %d，隔离 %d 行",
             stats.rows_in["orders"], len(orders),
             stats.rows_in["order_items"], len(items), len(rejects))
    return stats


# --------------------------------------------------------------------------
# 各表清洗
# --------------------------------------------------------------------------
def _clean_products(raw: pd.DataFrame, stats: CleanStats) -> pd.DataFrame:
    df = raw.drop(columns=[c for c in ("ods_id", "load_time") if c in raw.columns]).copy()
    df = df.drop_duplicates(subset=["sku_id"], keep="first").reset_index(drop=True)

    df["list_price"] = pd.to_numeric(df["list_price"], errors="coerce")
    df["cost_price"] = pd.to_numeric(df["cost_price"], errors="coerce")
    df["shelf_life_days"] = pd.to_numeric(df["shelf_life_days"], errors="coerce")

    bad = (df["list_price"] <= 0) | (df["list_price"] > 10000) | df["list_price"].isna()
    stats.repairs["E11"] = int(bad.sum())
    if bad.any():
        median_by_cat = df.groupby("category")["list_price"].median()
        df.loc[bad, "list_price"] = df.loc[bad, "category"].map(median_by_cat)
        df["list_price"] = df["list_price"].fillna(99.0)

    df["list_price"] = df["list_price"].round(2)
    df["cost_price"] = df["cost_price"].fillna(0).round(2)
    df["repair_flags"] = _flags([("E11", bad)])
    df["etl_time"] = datetime.now()

    cols = ["sku_id", "sku_name", "category", "color", "unit", "spec", "cost_price",
            "list_price", "shelf_life_days", "origin_place", "supplier_id", "status",
            "repair_flags", "source_batch", "etl_time"]
    return df.rename(columns={"batch_id": "source_batch"})[cols]


def _clean_customers(raw: pd.DataFrame, stats: CleanStats) -> pd.DataFrame:
    df = raw.drop(columns=[c for c in ("ods_id", "load_time") if c in raw.columns]).copy()
    before = len(df)
    df = df.drop_duplicates(subset=["customer_id"], keep="first").reset_index(drop=True)
    stats.dedupe["E12"] = before - len(df)

    def _city(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        t = str(v).replace("\u3000", "").replace(" ", "").strip()
        while t.endswith("市市"):
            t = t[:-1]
        return t or None

    raw_city = df["city"].astype(str)
    df["city"] = df["city"].map(_city)
    city_changed = df["city"] != raw_city

    raw_phone = df["phone"].astype(str)
    phone_ok = raw_phone.str.match(PHONE_RE)
    stats.repairs["E08"] = int((~phone_ok).sum())
    df.loc[~phone_ok, "phone"] = None

    cache: Dict[str, str] = {}
    raw_level = df["member_level"].astype(str)
    df["member_level"] = _map_with_cache(df["member_level"], LEVEL_MAP, cache)
    level_changed = df["member_level"] != raw_level
    stats.repairs["E09"] = int(level_changed.sum())
    stats.repairs["E07"] = int(city_changed.sum())

    df["register_date"] = pd.to_datetime(df["register_date"], errors="coerce").dt.date
    df["birthday"] = pd.to_datetime(df["birthday"], errors="coerce").dt.date
    df["is_active"] = pd.to_numeric(df["is_active"], errors="coerce").fillna(1).astype(int)

    df["repair_flags"] = _flags([
        ("E07", city_changed),
        ("E08", ~phone_ok),
        ("E09", level_changed),
    ])
    df["etl_time"] = datetime.now()

    cols = ["customer_id", "customer_name", "customer_type", "gender", "phone", "city",
            "province", "birthday", "register_date", "member_level", "source_channel",
            "is_active", "repair_flags", "source_batch", "etl_time"]
    return df.rename(columns={"batch_id": "source_batch"})[cols]


def _clean_orders(raw: pd.DataFrame, stats: CleanStats,
                  rejects: List[dict], batch: str) -> pd.DataFrame:
    df = raw.drop(columns=[c for c in ("ods_id", "load_time") if c in raw.columns]).copy()
    before = len(df)
    biz_cols = [c for c in df.columns if c != "batch_id"]
    df = df.drop_duplicates(subset=biz_cols, keep="first").reset_index(drop=True)
    stats.dedupe["E01"] = before - len(df)

    # E02 时间解析（标准格式以外的都算"被重解析过"）
    raw_time = df["order_time"].astype(str)
    std_mask = raw_time.str.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    cache: Dict[str, Any] = {}

    def _parse(v):
        key = str(v)
        if key not in cache:
            cache[key] = parse_datetime(v)
        return cache[key]

    df["order_time"] = df["order_time"].map(_parse)
    unparsed = df["order_time"].isna()

    # E03 时间越界
    min_ts, max_ts = pd.Timestamp("2024-01-01"), pd.Timestamp("2026-11-19 23:59:59")
    out_of_range = df["order_time"].notna() & (
        (df["order_time"] < min_ts) | (df["order_time"] > max_ts)
    )

    # E04 金额异常：不拍固定阈值，改用订单内部勾稽关系（实付 = 原价 - 优惠 + 运费）
    # 被 ×50 放大的金额能通过"10 万以内"的阈值，但一定破坏勾稽关系。
    for col in ("total_amount", "discount_amount", "freight_amount", "payable_amount",
                "cost_amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    reconcile_diff = (df["payable_amount"]
                      - (df["total_amount"] - df["discount_amount"] + df["freight_amount"])).abs()
    bad_amount = (df["payable_amount"] <= 0) | (reconcile_diff > 0.01)

    # E06 客户缺失
    missing_cust = df["customer_id"].isna() | (df["customer_id"].astype(str).str.strip() == "")

    # 隔离
    drop_mask = unparsed | out_of_range | bad_amount
    for idx in df.index[drop_mask]:
        row = df.loc[idx]
        code = "E03" if bool(out_of_range.loc[idx]) else ("E02" if bool(unparsed.loc[idx]) else "E04")
        rejects.append({
            "batch_id": batch, "source_table": "ods_orders_raw",
            "biz_key": str(row.get("order_id") or ""), "reason_code": code,
            "reason_desc": CLEAN_RULES[code],
            "payload": json.dumps(_json_safe(row.to_dict()), ensure_ascii=False),
        })
    stats.rejects["E02"] = int(unparsed.sum())
    stats.rejects["E03"] = int(out_of_range.sum())
    stats.rejects["E04"] = int(bad_amount.sum())

    df = df[~drop_mask].reset_index(drop=True)
    missing_cust = missing_cust[~drop_mask].reset_index(drop=True)
    std_mask = std_mask[~drop_mask].reset_index(drop=True)

    # E05 状态标准化
    status_cache: Dict[str, str] = {}
    raw_status = df["status"].astype(str)
    normalized = _map_with_cache(df["status"], STATUS_MAP, status_cache)
    status_changed = normalized != raw_status
    df["status"] = normalized
    stats.repairs["E05"] = int(status_changed.sum())

    # E06 客户占位
    df["customer_id"] = df["customer_id"].astype(object)
    df.loc[missing_cust, "customer_id"] = "UNKNOWN"
    stats.repairs["E06"] = int(missing_cust.sum())

    # E10 备注乱码
    remark = df["remark"].astype(str)
    garbage = remark.str.contains(r"\?\?|café|囧|\*\*\*\*", regex=True, na=False)
    df.loc[garbage, "remark"] = None
    stats.repairs["E10"] = int(garbage.sum())

    # 时间字段统一解析
    df["pay_time"] = df["pay_time"].map(_parse)
    df["expected_delivery_time"] = df["expected_delivery_time"].map(_parse)
    df["delivery_time"] = df["delivery_time"].map(_parse)

    # E19 已完成但缺配送时间（打标保留）
    missing_delivery = (df["status"] == "已完成") & df["delivery_time"].isna()

    df["is_first_order"] = pd.to_numeric(df["is_first_order"], errors="coerce").fillna(0).astype(int)
    df["repair_flags"] = _flags([
        ("E02", ~std_mask),
        ("E05", status_changed),
        ("E06", missing_cust),
        ("E10", garbage),
        ("E19", missing_delivery),
    ])
    df["etl_time"] = datetime.now()
    stats.repairs["E02"] = int((~std_mask).sum())
    stats.repairs["E19"] = int(missing_delivery.sum())

    for col in ("total_amount", "discount_amount", "freight_amount", "payable_amount",
                "cost_amount"):
        df[col] = df[col].round(2)

    cols = ["order_id", "customer_id", "store_id", "channel_id", "order_time", "pay_time",
            "status", "delivery_type", "expected_delivery_time", "delivery_time",
            "total_amount", "discount_amount", "freight_amount", "payable_amount",
            "cost_amount", "promotion_id", "coupon_id", "is_first_order", "remark",
            "repair_flags", "source_batch", "etl_time"]
    return df.rename(columns={"batch_id": "source_batch"})[cols]


def _clean_items(raw: pd.DataFrame, orders: pd.DataFrame, stats: CleanStats,
                 rejects: List[dict], batch: str) -> pd.DataFrame:
    df = raw.drop(columns=[c for c in ("ods_id", "load_time") if c in raw.columns]).copy()
    before = len(df)
    df = df.drop_duplicates(subset=["item_id"], keep="first").reset_index(drop=True)
    stats.dedupe["E13"] = before - len(df)

    for col in ("quantity", "unit_price", "discount_amount", "freight_amount",
                "line_amount", "cost_amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    bad_qty = df["quantity"] <= 0
    no_order = df["order_id"].isna() | (df["order_id"].astype(str).str.strip() == "")
    drop_mask = bad_qty | no_order

    for idx in df.index[drop_mask]:
        row = df.loc[idx]
        code = "E14" if bool(bad_qty.loc[idx]) else "E15"
        rejects.append({
            "batch_id": batch, "source_table": "ods_order_items_raw",
            "biz_key": str(row.get("item_id") or ""), "reason_code": code,
            "reason_desc": CLEAN_RULES[code],
            "payload": json.dumps(_json_safe(row.to_dict()), ensure_ascii=False),
        })
    stats.rejects["E14"] = int(bad_qty.sum())
    stats.rejects["E15"] = int(no_order.sum())
    df = df[~drop_mask].reset_index(drop=True)

    # E18 所属订单被隔离 → 明细一并隔离
    valid_orders = set(orders["order_id"])
    orphan = ~df["order_id"].isin(valid_orders)
    for idx in df.index[orphan]:
        row = df.loc[idx]
        rejects.append({
            "batch_id": batch, "source_table": "ods_order_items_raw",
            "biz_key": str(row.get("item_id") or ""), "reason_code": "E18",
            "reason_desc": CLEAN_RULES["E18"],
            "payload": json.dumps(_json_safe(row.to_dict()), ensure_ascii=False),
        })
    stats.rejects["E18"] = int(orphan.sum())
    df = df[~orphan].reset_index(drop=True)

    # E16 单价为 0 → 用商品主数据回填
    price_map = _price_map()
    zero_price = df["unit_price"] <= 0
    if zero_price.any():
        product_key = df["bundle_id"].fillna(df["sku_id"])
        df.loc[zero_price, "unit_price"] = product_key[zero_price].map(price_map).fillna(0)
        still_zero = zero_price & (df["unit_price"] <= 0)
        if still_zero.any():
            df.loc[still_zero, "unit_price"] = 1.0  # 兜底：留 1 元待人工复核
    stats.repairs["E16"] = int(zero_price.sum())

    # E17 行金额重算
    expect = (df["quantity"] * df["unit_price"]).round(2)
    mismatch = (df["line_amount"] - expect).abs() > 0.01
    stats.repairs["E17"] = int(mismatch.sum())
    df["line_amount"] = expect

    df["quantity"] = df["quantity"].astype(int)
    df["product_type"] = df["product_type"].fillna("BUNDLE")
    df["repair_flags"] = _flags([("E16", zero_price), ("E17", mismatch)])
    df["etl_time"] = datetime.now()

    for col in ("unit_price", "discount_amount", "freight_amount", "line_amount", "cost_amount"):
        df[col] = df[col].round(2)

    cols = ["item_id", "order_id", "product_type", "sku_id", "bundle_id", "quantity",
            "unit_price", "discount_amount", "freight_amount", "line_amount",
            "cost_amount", "repair_flags", "source_batch", "etl_time"]
    return df.rename(columns={"batch_id": "source_batch"})[cols]


def _price_map() -> Dict[str, float]:
    """商品主数据价格表（来自 dim_product，属于主数据而不是交易数据）。"""
    rows = db.fetch_all("SELECT product_id, unit_price FROM dim_product", database=MYSQL_DW_DB)
    return {r["product_id"]: float(r["unit_price"] or 0) for r in rows}
