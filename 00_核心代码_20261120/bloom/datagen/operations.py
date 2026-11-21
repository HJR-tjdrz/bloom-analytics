"""履约侧数据：退款、订单状态流转、每日库存。

三个衍生对象的价值
------------------
- **退款**：支撑"退款率/退款原因 TOP/退款损失"分析，也是毛利率修正项；
- **状态流转日志**：可以算「付款→制作→配送→完成」各环节耗时，
  这是花店履约效率的核心指标（节日爆单时最容易崩）；
- **每日库存**：花材是生鲜，靠它算周转率、损耗率、滞销与缺货，
  库存是按 BOM 把花束销量还原成花材消耗量得到的（不是拍脑袋）。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .orders import REFUND_REASONS, CustomerAllocator, CatalogCtx

TRANSITIONS = {
    "已付款": ["已付款"],
    "制作中": ["已付款", "制作中"],
    "待配送": ["已付款", "制作中", "待配送"],
    "已完成": ["已付款", "制作中", "待配送", "已完成"],
    "已退款": ["已付款", "制作中", "待配送", "已完成", "已退款"],
    "已取消": ["已付款", "已取消"],
}


def build_refunds(orders: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """为「已退款」订单生成退款单。"""
    refunded = orders[orders["status"] == "已退款"].copy()
    rows: List[dict] = []
    reasons = [r[0] for r in REFUND_REASONS]
    probs = np.array([r[1] for r in REFUND_REASONS])
    probs = probs / probs.sum()

    for i, row in enumerate(refunded.itertuples(), start=1):
        base_time = row.delivery_time if pd.notna(row.delivery_time) else row.order_time
        refund_time = base_time + timedelta(
            hours=int(rng.integers(1, 72)), minutes=int(rng.integers(0, 60))
        )
        ratio = float(rng.choice([1.0, 1.0, 0.7, 0.5], p=[0.55, 0.2, 0.17, 0.08]))
        rows.append(
            {
                "refund_id": f"RF{i:06d}",
                "order_id": row.order_id,
                "refund_time": refund_time,
                "refund_amount": round(float(row.payable_amount) * ratio, 2),
                "refund_reason": str(rng.choice(reasons, p=probs)),
                "refund_type": "全额退款" if ratio >= 1.0 else "部分退款",
                "status": "已退款",
            }
        )
    return pd.DataFrame(rows)


def build_status_log(
    orders: pd.DataFrame, employees: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """生成近 180 天订单的状态流转日志（用于履约时效分析）。"""
    emp_by_store: Dict[str, List[str]] = {}
    for row in employees.itertuples():
        emp_by_store.setdefault(row.store_id, []).append(row.employee_id)

    recent = orders.copy()
    recent["order_time"] = pd.to_datetime(recent["order_time"])
    cutoff = recent["order_time"].max() - pd.Timedelta(days=180)
    recent = recent[recent["order_time"] >= cutoff]

    rows: List[dict] = []
    seq = 1
    for row in recent.itertuples():
        chain = TRANSITIONS.get(row.status, ["已完成"])
        cur = row.pay_time if pd.notna(row.pay_time) else row.order_time
        ops = emp_by_store.get(row.store_id) or ["EMP0101"]
        prev = chain[0]
        rows.append(
            {
                "log_id": f"SL{seq:07d}", "order_id": row.order_id,
                "from_status": "待付款", "to_status": prev,
                "changed_at": cur, "operator_id": str(rng.choice(ops)),
            }
        )
        seq += 1
        for nxt in chain[1:]:
            cur = cur + timedelta(minutes=int(rng.integers(8, 45)))
            rows.append(
                {
                    "log_id": f"SL{seq:07d}", "order_id": row.order_id,
                    "from_status": prev, "to_status": nxt,
                    "changed_at": cur, "operator_id": str(rng.choice(ops)),
                }
            )
            prev = nxt
            seq += 1
    return pd.DataFrame(rows)


def build_inventory(
    orders: pd.DataFrame,
    items: pd.DataFrame,
    ctx: CatalogCtx,
    start: date,
    end: date,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """按 BOM 把花束销量还原为花材消耗，模拟每日库存与损耗。

    花束卖出去 1 束，消耗的是 BOM 里的 99 支玫瑰 + 2 扎尤加利，
    所以库存口径必须做 BOM 展开，否则库存和销量永远对不上。
    """
    # 1) 只统计真实发生销售的单（排除已取消）
    valid = orders[orders["status"] != "已取消"][["order_id", "order_time"]].copy()
    valid["stat_date"] = pd.to_datetime(valid["order_time"]).dt.date
    merged = items.merge(valid[["order_id", "stat_date"]], on="order_id", how="inner")

    consumption: List[dict] = []
    for row in merged.itertuples():
        if row.product_type == "BUNDLE":
            meta = ctx.bundle_meta.get(row.bundle_id)
            if not meta:
                continue
            for sku_id, qty in meta["items"]:
                consumption.append({"stat_date": row.stat_date, "sku_id": sku_id,
                                    "qty": qty * int(row.quantity)})
        else:
            consumption.append({"stat_date": row.stat_date, "sku_id": row.sku_id,
                                "qty": int(row.quantity)})

    cons_df = pd.DataFrame(consumption)
    daily = cons_df.groupby(["stat_date", "sku_id"], as_index=False)["qty"].sum()

    # 2) 逐 SKU 逐日推进库存
    sales_map: Dict[Tuple[date, str], int] = {
        (r.stat_date, r.sku_id): int(r.qty) for r in daily.itertuples()
    }
    rows: List[dict] = []
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    for sku_id, meta in ctx.sku_meta.items():
        perishable = meta["shelf"] < 900
        cost = meta["cost"]
        on_hand = float(max(60, int(rng.integers(120, 400))))
        safety = 120.0 if perishable else 30.0
        target = 480.0 if perishable else 60.0
        for day in days:
            out_qty = sales_map.get((day, sku_id), 0)
            in_qty = 0
            projected = on_hand - out_qty
            if projected < safety:
                in_qty = int(max(0, target - projected))
            waste_qty = 0
            if perishable and out_qty > 0:
                waste_qty = int(out_qty * float(rng.uniform(0.02, 0.09)))
            elif perishable and rng.random() < 0.15:
                waste_qty = int(rng.integers(1, 6))
            on_hand = max(0.0, on_hand - out_qty - waste_qty + in_qty)
            rows.append(
                {
                    "stat_date": day, "sku_id": sku_id,
                    "in_qty": in_qty, "out_qty": out_qty, "waste_qty": waste_qty,
                    "on_hand_qty": int(on_hand),
                    "purchase_amount": round(in_qty * cost, 2),
                    "waste_amount": round(waste_qty * cost, 2),
                }
            )
    return pd.DataFrame(rows)
