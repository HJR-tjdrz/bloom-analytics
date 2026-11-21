"""脏数据注入：模拟「业务系统导出 Excel」的真实状态。

为什么必须做这一步
------------------
真实企业的数据从来不是干净的：订单导出里会有重复行、客服手工改的状态、
被 Excel 自动转成日期的时间字符串、负数的退款订单、缺失的客户 ID……
如果项目里只有干净数据，数据清洗这一步就没法体现，
招聘方也看不出你是否真的处理过脏数据。

这里对**导出层**（raw_export）做注入，干净层（clean）保持原样，
两条链路的关系是：
    业务库(clean) --导出--> 原始文件(脏) --ODS落库--> 清洗 --> DWD(干净)
这样既能证明清洗能力，又能用 clean 数据做「对账」，验证清洗没有丢数据。
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# 规则编号 → 说明（后续数据质量报告直接引用这套编号）
DQ_RULES: Dict[str, str] = {
    "DQ01": "订单号重复（重复导出/重复提交）",
    "DQ02": "客户ID缺失（游客下单/系统未落号）",
    "DQ03": "下单时间格式不规范（Excel 自动转换、手工录入）",
    "DQ04": "订单金额异常（负数、0 元、极端离群值）",
    "DQ05": "订单状态值不规范（空格、大小写、中英混写）",
    "DQ06": "城市名含空白符/全角字符（手工录入）",
    "DQ07": "手机号格式非法（位数不对、含字母）",
    "DQ08": "业务时间越界（1999 年或未来日期）",
    "DQ09": "已完成订单缺少配送时间（状态回填缺失）",
    "DQ10": "备注含乱码/emoji（跨系统编码问题）",
    "DQ11": "商品价格异常（0 元或极端高价）",
    "DQ12": "客户重复（同一手机号多条记录）",
    "DQ13": "会员等级脏值（大小写/空格/别名）",
    "DQ14": "订单明细重复（重复导出）",
    "DQ15": "数量为非正数（退货冲销未拆单）",
    "DQ16": "明细订单号缺失（孤立明细，无法归属订单）",
    "DQ17": "明细单价为 0（赠品未打标/价格缺失）",
}


# --------------------------------------------------------------------------
# 订单
# --------------------------------------------------------------------------
def dirty_orders(orders: pd.DataFrame, rng: np.random.Generator) -> Tuple[pd.DataFrame, List[dict]]:
    df = orders.copy()
    df["order_time"] = pd.to_datetime(df["order_time"])
    n = len(df)
    manifest: List[dict] = []

    def _record(rule: str, field: str, affected: int) -> None:
        manifest.append({"rule": rule, "table": "orders_raw", "field": field,
                         "affected": int(affected), "desc": DQ_RULES[rule]})

    # DQ03 时间格式混乱（先做，后面越界注入会用到字符串形式）
    idx = rng.choice(n, size=int(n * 0.010), replace=False)
    df["order_time"] = df["order_time"].astype(str)
    for i in idx:
        ts = pd.Timestamp(df.at[i, "order_time"])
        style = int(rng.integers(0, 3))
        if style == 0:
            df.at[i, "order_time"] = f"{ts.year}/{ts.month}/{ts.day} {ts.hour}:{ts.minute}"
        elif style == 1:
            df.at[i, "order_time"] = f"{ts.year}年{ts.month}月{ts.day}日 {ts:%H:%M}"
        else:
            df.at[i, "order_time"] = ts.strftime("%d/%m/%Y %H:%M")
    _record("DQ03", "order_time", len(idx))

    # DQ08 时间越界
    idx = rng.choice(n, size=int(n * 0.003), replace=False)
    for i in idx:
        df.at[i, "order_time"] = str(rng.choice(["1999-01-01 10:00:00", "2030-06-01 12:30:00"]))
    _record("DQ08", "order_time", len(idx))

    # DQ02 客户ID缺失
    idx = rng.choice(n, size=int(n * 0.020), replace=False)
    df.loc[df.index[idx], "customer_id"] = None
    _record("DQ02", "customer_id", len(idx))

    # DQ04 金额异常
    idx = rng.choice(n, size=int(n * 0.006), replace=False)
    for i in idx:
        mode = int(rng.integers(0, 3))
        if mode == 0:
            df.at[i, "payable_amount"] = -round(float(rng.uniform(50, 500)), 2)
        elif mode == 1:
            df.at[i, "payable_amount"] = 0.0
        else:
            df.at[i, "payable_amount"] = round(float(df.at[i, "payable_amount"]) * 50, 2)
    _record("DQ04", "payable_amount", len(idx))

    # DQ05 状态值不规范
    idx = rng.choice(n, size=int(n * 0.008), replace=False)
    variants = {"已完成": ["已完成 ", "已 完成", "COMPLETED", "完成"],
                "已取消": ["已取消 ", "CANCELLED", "已 取消"],
                "已退款": ["已退款 ", "REFUNDED"]}
    for i in idx:
        cur = str(df.at[i, "status"])
        pool = variants.get(cur, [cur + " "])
        df.at[i, "status"] = str(rng.choice(pool))
    _record("DQ05", "status", len(idx))

    # DQ09 已完成订单缺配送时间
    done_mask = df["status"].astype(str).str.contains("完成")
    done_idx = df.index[done_mask].to_numpy()
    if len(done_idx):
        pick = rng.choice(done_idx, size=min(len(done_idx), int(n * 0.030)), replace=False)
        df.loc[pick, "delivery_time"] = None
        _record("DQ09", "delivery_time", len(pick))

    # DQ10 备注乱码
    idx = rng.choice(n, size=int(n * 0.005), replace=False)
    garbage = ["救???", "café ☕ 快点", "花材??坏了", "Please call 138****", "囧rz"]
    for i in idx:
        df.at[i, "remark"] = str(rng.choice(garbage))
    _record("DQ10", "remark", len(idx))

    # DQ01 重复行（同一订单号整行重复）
    dup_n = int(n * 0.012)
    dup_rows = df.sample(n=dup_n, random_state=int(rng.integers(0, 10_000)))
    df_out = pd.concat([df, dup_rows], ignore_index=True)
    df_out = df_out.sample(frac=1.0, random_state=int(rng.integers(0, 10_000))).reset_index(drop=True)
    _record("DQ01", "order_id", dup_n)

    return df_out, manifest


# --------------------------------------------------------------------------
# 客户
# --------------------------------------------------------------------------
def dirty_customers(customers: pd.DataFrame, rng: np.random.Generator) -> Tuple[pd.DataFrame, List[dict]]:
    df = customers.copy()
    n = len(df)
    manifest: List[dict] = []

    def _record(rule: str, field: str, affected: int) -> None:
        manifest.append({"rule": rule, "table": "customers_raw", "field": field,
                         "affected": int(affected), "desc": DQ_RULES[rule]})

    # DQ06 城市名脏值
    idx = rng.choice(n, size=int(n * 0.015), replace=False)
    for i in idx:
        city = str(df.at[i, "city"])
        style = int(rng.integers(0, 3))
        df.at[i, "city"] = city + " " if style == 0 else (city.replace("市", " 市") if style == 1 else city + "市")
    _record("DQ06", "city", len(idx))

    # DQ07 手机号非法
    idx = rng.choice(n, size=int(n * 0.005), replace=False)
    for i in idx:
        phone = str(df.at[i, "phone"])
        style = int(rng.integers(0, 3))
        df.at[i, "phone"] = phone[:-1] if style == 0 else (phone + "88" if style == 1 else "unknow")
    _record("DQ07", "phone", len(idx))

    # DQ13 会员等级脏值
    idx = rng.choice(n, size=int(n * 0.010), replace=False)
    variants = {"普通会员": "普通", "银卡会员": "silver", "金卡会员": " 金卡会员 ",
                "钻石会员": "Diamond", "企业客户": "企业"}
    for i in idx:
        cur = str(df.at[i, "member_level"])
        df.at[i, "member_level"] = variants.get(cur, cur)
    _record("DQ13", "member_level", len(idx))

    # DQ12 客户重复（同一手机号出现两条）
    dup_n = int(n * 0.008)
    dup_rows = df.sample(n=dup_n, random_state=int(rng.integers(0, 10_000)))
    df_out = pd.concat([df, dup_rows], ignore_index=True)
    _record("DQ12", "phone", dup_n)

    return df_out, manifest


# --------------------------------------------------------------------------
# 商品
# --------------------------------------------------------------------------
def dirty_products(skus: pd.DataFrame, rng: np.random.Generator) -> Tuple[pd.DataFrame, List[dict]]:
    df = skus.copy()
    n = len(df)
    manifest: List[dict] = []
    idx = rng.choice(n, size=max(1, int(n * 0.05)), replace=False)
    for i in idx:
        df.at[i, "list_price"] = 0.0 if rng.random() < 0.5 else 99999.0
    manifest.append({"rule": "DQ11", "table": "products_raw", "field": "list_price",
                     "affected": len(idx), "desc": DQ_RULES["DQ11"]})
    return df, manifest


def apply_all(
    orders: pd.DataFrame,
    customers: pd.DataFrame,
    skus: pd.DataFrame,
    rng: np.random.Generator,
    order_items: pd.DataFrame | None = None,
) -> Tuple[Dict[str, pd.DataFrame], List[dict]]:
    """一次性生成脏数据副本，返回 (表名→DataFrame, 问题清单)。"""
    o, m1 = dirty_orders(orders, rng)
    c, m2 = dirty_customers(customers, rng)
    p, m3 = dirty_products(skus, rng)
    out = {"orders_raw": o, "customers_raw": c, "products_raw": p}
    manifest = m1 + m2 + m3
    if order_items is not None:
        i, m4 = dirty_order_items(order_items, rng)
        out["order_items_raw"] = i
        manifest += m4
    return out, manifest


def dirty_order_items(order_items: pd.DataFrame, rng: np.random.Generator) -> Tuple[pd.DataFrame, List[dict]]:
    """订单明细的脏数据：重复行、负数量、孤立行、零单价。"""
    df = order_items.copy()
    n = len(df)
    manifest: List[dict] = []

    def _record(rule: str, field: str, affected: int) -> None:
        manifest.append({"rule": rule, "table": "order_items_raw", "field": field,
                         "affected": int(affected), "desc": DQ_RULES[rule]})

    # DQ15 数量非正
    idx = rng.choice(n, size=int(n * 0.004), replace=False)
    for i in idx:
        df.at[i, "quantity"] = int(rng.choice([-1, -2, 0]))
    _record("DQ15", "quantity", len(idx))

    # DQ16 孤立明细（订单号缺失）
    idx = rng.choice(n, size=int(n * 0.003), replace=False)
    df.loc[df.index[idx], "order_id"] = None
    _record("DQ16", "order_id", len(idx))

    # DQ17 单价为 0
    idx = rng.choice(n, size=int(n * 0.005), replace=False)
    df.loc[df.index[idx], "unit_price"] = 0.0
    _record("DQ17", "unit_price", len(idx))

    # DQ14 重复行
    dup_n = int(n * 0.010)
    dup_rows = df.sample(n=dup_n, random_state=int(rng.integers(0, 10_000)))
    out = pd.concat([df, dup_rows], ignore_index=True)
    out = out.sample(frac=1.0, random_state=int(rng.integers(0, 10_000))).reset_index(drop=True)
    _record("DQ14", "item_id", dup_n)

    return out, manifest
