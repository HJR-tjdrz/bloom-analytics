"""订单交易引擎：按业务规律生成 3 年订单明细。

生成逻辑（也是这份数据"像真的"的原因）
--------------------------------------
1. 日订单量 = 基准量 × 季节系数 × 星期系数 × 年度增长 × 节日系数，再取泊松抽样；
2. 渠道结构随时间演化（抖音直播从 4% 涨到 20%，门店占比逐年下滑）；
3. 客户 72% 复购、28% 新客，复购概率按"忠诚度"分层（帕累托效应）；
4. 商品结构随节日变化：情人节/520/七夕偏爱情告白，清明偏祭扫追思；
5. 促销按活动表命中日期生效，折扣、优惠券、运费分别计入不同金额字段；
6. 订单状态按"距今时长"分布：越近的订单越可能还在制作/配送中。

所有随机数来自同一个 `numpy.random.Generator`（固定种子），
因此同样的配置每次生成的数据完全一致，便于复现与回归测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import BASE_DAILY_ORDERS, REPEAT_ORDER_RATIO
from .calendar_cn import festival_affinity
from .catalog import channel_weight

# 各城市的门店（同城配送/自提用）
CITY_STORES: Dict[str, List[str]] = {
    "上海市": ["ST01", "ST02", "ST03"],
    "杭州市": ["ST04", "ST05"],
    "南京市": ["ST06"],
    "苏州市": ["ST07"],
}
CLOUD_STORE = "ST08"

# 下单时段分布（按渠道，模拟真实行为：门店下午茶时段、直播晚间）
HOUR_PROFILE: Dict[str, Dict[int, float]] = {
    "CH01": {8: 0.02, 9: 0.03, 10: 0.05, 11: 0.07, 12: 0.07, 13: 0.07, 14: 0.08,
             15: 0.08, 16: 0.09, 17: 0.09, 18: 0.09, 19: 0.10, 20: 0.09, 21: 0.05, 22: 0.02},
    "CH02": {8: 0.02, 9: 0.03, 10: 0.05, 11: 0.05, 12: 0.04, 13: 0.04, 14: 0.05,
             15: 0.05, 16: 0.05, 17: 0.06, 18: 0.07, 19: 0.09, 20: 0.10, 21: 0.09,
             22: 0.07, 23: 0.04},
    "CH03": {9: 0.02, 10: 0.05, 11: 0.10, 12: 0.11, 13: 0.08, 14: 0.05, 15: 0.05,
             16: 0.06, 17: 0.10, 18: 0.10, 19: 0.08, 20: 0.06, 21: 0.03},
    "CH04": {17: 0.03, 18: 0.05, 19: 0.12, 20: 0.20, 21: 0.22, 22: 0.18, 23: 0.12, 0: 0.05},
    "CH05": {9: 0.03, 10: 0.05, 11: 0.06, 12: 0.06, 13: 0.06, 14: 0.06, 15: 0.06,
             16: 0.06, 17: 0.07, 18: 0.08, 19: 0.09, 20: 0.09, 21: 0.08, 22: 0.06, 23: 0.04},
    "CH06": {9: 0.12, 10: 0.16, 11: 0.14, 12: 0.08, 13: 0.08, 14: 0.12, 15: 0.14,
             16: 0.10, 17: 0.06},
}

# 配送方式（按渠道）
DELIVERY_PROFILE: Dict[str, List[Tuple[str, float]]] = {
    "CH01": [("自提", 0.62), ("同城配送", 0.38)],
    "CH02": [("同城配送", 0.74), ("自提", 0.22), ("快递", 0.04)],
    "CH03": [("同城配送", 0.92), ("自提", 0.08)],
    "CH04": [("快递", 0.52), ("同城配送", 0.45), ("自提", 0.03)],
    "CH05": [("快递", 0.60), ("同城配送", 0.38), ("自提", 0.02)],
    "CH06": [("同城配送", 1.00)],
}

# 订单状态分布：按"距今天数"分档（已完成/已退款/已取消/待配送/制作中/已付款）
STATUS_BY_AGE: List[Tuple[int, int, List[Tuple[str, float]]]] = [
    (31, 9999, [("已完成", 0.925), ("已退款", 0.037), ("已取消", 0.038)]),
    (8, 30, [("已完成", 0.900), ("待配送", 0.020), ("已退款", 0.035), ("已取消", 0.045)]),
    (3, 7, [("已完成", 0.720), ("待配送", 0.140), ("制作中", 0.070), ("已退款", 0.020), ("已取消", 0.050)]),
    (1, 2, [("已完成", 0.450), ("待配送", 0.300), ("制作中", 0.170), ("已付款", 0.040), ("已取消", 0.040)]),
    (0, 0, [("已付款", 0.450), ("制作中", 0.350), ("待配送", 0.150), ("已完成", 0.030), ("已取消", 0.020)]),
]

REFUND_REASONS = [("花材不新鲜", 0.34), ("配送超时", 0.26), ("与图片不符", 0.16),
                   ("客户自身取消", 0.14), ("数量缺失", 0.10)]

REMARKS_FESTIVAL = ["请务必准时送达", "帮我写张卡片：爱你每一天", "加急，下午必须到", "请包装精致一些", ""]
REMARKS_DAILY = ["", "", "", "送到公司前台", "不要百合，家里有人过敏", "请电话联系收花人",
                 "希望多一点满天星", "礼物卡片写：生日快乐"]
REMARKS_B2B = ["开业花篮，需提前一小时到", "请安排两人搬运", "公司前台签收", "发票开公司抬头"]


@dataclass
class CatalogCtx:
    """预计算好的商品上下文，避免在 10 万次循环里反复查 DataFrame。"""

    bundles: pd.DataFrame
    bom: pd.DataFrame
    skus: pd.DataFrame
    bundle_ids_by_cat: Dict[str, List[str]] = field(default_factory=dict)
    bundle_meta: Dict[str, dict] = field(default_factory=dict)
    bundle_launch: Dict[str, date] = field(default_factory=dict)
    sku_meta: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def build(cls, bundles: pd.DataFrame, bom: pd.DataFrame, skus: pd.DataFrame) -> "CatalogCtx":
        ctx = cls(bundles=bundles, bom=bom, skus=skus)
        bom_group = bom.groupby("bundle_id")[["sku_id", "quantity"]].apply(
            lambda g: [(r.sku_id, int(r.quantity)) for r in g.itertuples()]
        ).to_dict()

        for row in bundles.itertuples():
            ctx.bundle_meta[row.bundle_id] = {
                "category": row.category,
                "scene": row.scene,
                "price": float(row.list_price),
                "cost": float(row.cost_price),
                "items": bom_group.get(row.bundle_id, []),
            }
            ctx.bundle_launch[row.bundle_id] = pd.Timestamp(row.launch_date).date()
            ctx.bundle_ids_by_cat.setdefault(row.category, []).append(row.bundle_id)

        for row in skus.itertuples():
            ctx.sku_meta[row.sku_id] = {
                "category": row.category,
                "price": float(row.list_price),
                "cost": float(row.cost_price),
                "shelf": int(row.shelf_life_days),
            }
        return ctx


def _hour_probs() -> Dict[str, np.ndarray]:
    out = {}
    for ch, spec in HOUR_PROFILE.items():
        arr = np.zeros(24)
        for h, w in spec.items():
            arr[h] = w
        out[ch] = arr / arr.sum()
    return out


HOUR_PROBS = _hour_probs()


def _pick_weighted(rng: np.random.Generator, pairs: List[Tuple[str, float]]) -> str:
    values = [p[0] for p in pairs]
    probs = np.array([p[1] for p in pairs])
    return str(rng.choice(values, p=probs / probs.sum()))


def _status_for_age(rng: np.random.Generator, days_ago: int) -> str:
    for lo, hi, dist in STATUS_BY_AGE:
        if lo <= days_ago <= hi:
            return _pick_weighted(rng, dist)
    return "已完成"


class CustomerAllocator:
    """客户分配器：负责"新客激活"与"老客按忠诚度加权抽取"。"""

    def __init__(self, pool: List[dict], rng: np.random.Generator):
        self.pool = pool
        self.rng = rng
        self.next_personal = 0
        # 企业客户排在池子的后半段，必须从它们的真实起始位置开始取，
        # 否则会把个人客户当成企业客户用（早期版本的 bug：企业 GMV 占比恒为 0）
        self.ent_start = next(
            (i for i, p in enumerate(pool) if p.get("customer_type") == "企业"), len(pool)
        )
        self.next_ent = self.ent_start
        self.buckets: List[List[int]] = [[] for _ in range(10)]
        self.bucket_sizes = np.zeros(10)
        self.bucket_weight = np.array([0.35, 0.55, 0.75, 1.00, 1.30, 1.65, 2.05, 2.45, 2.90, 3.50])
        # 企业客户单独管理（B2B 复购率更高、单量大）
        self.ent_ids: List[int] = []

    def _bucket_of(self, loyalty: float) -> int:
        return min(9, max(0, int(loyalty * 10)))

    def activate(self, day: date) -> dict:
        idx = self.next_personal
        self.next_personal += 1
        cust = self.pool[idx]
        cust["register_date"] = day
        b = self._bucket_of(cust["_loyalty"])
        self.buckets[b].append(idx)
        self.bucket_sizes[b] += 1
        return cust

    def pick_or_create(self, day: date) -> Tuple[dict, bool]:
        """返回 (客户, 是否首单)。"""
        total = self.bucket_sizes.sum()
        if total > 0 and self.rng.random() < REPEAT_ORDER_RATIO:
            w = self.bucket_sizes * self.bucket_weight
            b = int(self.rng.choice(10, p=w / w.sum()))
            members = self.buckets[b]
            idx = members[int(self.rng.integers(len(members)))]
            return self.pool[idx], False
        return self.activate(day), True

    def pick_enterprise(self, day: date) -> Tuple[dict, bool]:
        """企业客户：40% 新客户，60% 复购。"""
        if self.ent_ids and self.rng.random() < 0.60:
            idx = self.ent_ids[int(self.rng.integers(len(self.ent_ids)))]
            return self.pool[idx], False
        if self.next_ent >= len(self.pool):
            # 企业池用尽时复用老客户，避免下标越界
            idx = self.ent_ids[int(self.rng.integers(len(self.ent_ids)))]
            return self.pool[idx], False
        idx = self.next_ent
        self.next_ent += 1
        cust = self.pool[idx]
        cust["register_date"] = day
        self.ent_ids.append(idx)
        return cust, True


def generate_transactions(
    calendar_df: pd.DataFrame,
    ctx: CatalogCtx,
    pool: List[dict],
    promotions: pd.DataFrame,
    coupons: pd.DataFrame,
    rng: np.random.Generator,
    data_end: date,
) -> dict:
    """核心：逐日生成订单、明细、促销关联、退款与履约时间。"""

    promo_by_date: Dict[date, List[dict]] = {}
    for row in promotions.itertuples():
        cur = pd.Timestamp(row.start_date).date()
        stop = pd.Timestamp(row.end_date).date()
        while cur <= stop:
            promo_by_date.setdefault(cur, []).append(
                {
                    "promotion_id": row.promotion_id,
                    "type": row.promotion_type,
                    "rate": float(row.discount_rate),
                    "threshold": float(row.threshold_amount),
                    "reduce": float(row.reduce_amount),
                }
            )
            cur += timedelta(days=1)

    coupon_list = coupons.to_dict("records")
    allocator = CustomerAllocator(pool, rng)
    ref_date = data_end + timedelta(days=1)

    orders: List[dict] = []
    items: List[dict] = []
    order_promos: List[dict] = []

    # 单支/散卖商品池（按品类固定权重）
    loose_pool = [
        ("SKU001", 0.16), ("SKU002", 0.10), ("SKU003", 0.08), ("SKU004", 0.06),
        ("SKU013", 0.13), ("SKU007", 0.09), ("SKU014", 0.11), ("SKU011", 0.06),
        ("SKU023", 0.06), ("SKU025", 0.05), ("SKU026", 0.04), ("SKU018", 0.03),
        ("SKU010", 0.03),
    ]
    loose_values = [p[0] for p in loose_pool]
    loose_probs = np.array([p[1] for p in loose_pool])
    loose_probs = loose_probs / loose_probs.sum()

    order_seq: Dict[date, int] = {}

    for row in calendar_df.itertuples():
        day: date = row.date
        lam = BASE_DAILY_ORDERS * row.demand_index
        n_orders = int(rng.poisson(lam))
        if n_orders == 0:
            continue
        festival = row.festival_name or ""
        festive = festival != ""
        cw = channel_weight(day.year, day.month)
        ch_ids = list(cw.keys())
        ch_probs = np.array(list(cw.values()))
        ch_probs = ch_probs / ch_probs.sum()
        affinity_pairs = festival_affinity(festival)

        # 当天的订单先收集，再按时间排序后编号（保证单号与时间严格一致）
        day_orders: List[dict] = []
        for _ in range(n_orders):
            channel_id = str(rng.choice(ch_ids, p=ch_probs))
            is_b2b = channel_id == "CH06"

            # ---- 客户 ----
            if is_b2b:
                customer, is_first = allocator.pick_enterprise(day)
            else:
                customer, is_first = allocator.pick_or_create(day)

            # ---- 配送方式 ----
            delivery_type = _pick_weighted(rng, DELIVERY_PROFILE[channel_id])
            city = customer["city"]
            if delivery_type != "快递" and city not in CITY_STORES:
                delivery_type = "快递"

            # ---- 门店 ----
            if delivery_type == "快递" or is_b2b:
                store_id = CLOUD_STORE if delivery_type == "快递" else CITY_STORES[city][0]
            else:
                store_id = str(rng.choice(CITY_STORES[city]))

            # ---- 商品组合 ----
            if is_b2b:
                category = "开业庆典"
            else:
                cats = [c for c in affinity_pairs if ctx.bundle_ids_by_cat.get(c)]
                probs = np.array([affinity_pairs[c] for c in cats])
                category = str(rng.choice(cats, p=probs / probs.sum()))

            candidate_ids = [
                bid for bid in ctx.bundle_ids_by_cat.get(category, [])
                if ctx.bundle_launch[bid] <= day
            ]
            is_loose = (not is_b2b) and rng.random() < 0.22

            lines: List[Tuple[str, Optional[str], Optional[str], int, float, float]] = []
            # (product_type, sku_id, bundle_id, qty, unit_price, unit_cost)

            if is_loose:
                sku_id = str(rng.choice(loose_values, p=loose_probs))
                meta = ctx.sku_meta[sku_id]
                qty = int(rng.integers(1, 12)) if meta["shelf"] < 900 else 1
                lines.append(("SKU", sku_id, None, qty, meta["price"], meta["cost"]))
            else:
                if not candidate_ids:  # 极端兜底
                    bid = ctx.bundles.iloc[0]["bundle_id"]
                else:
                    bid = str(rng.choice(candidate_ids))
                meta = ctx.bundle_meta[bid]
                qty = int(rng.integers(2, 6)) if is_b2b else (2 if rng.random() < 0.02 else 1)
                lines.append(("BUNDLE", None, bid, qty, meta["price"], meta["cost"]))

            # ---- 加购（花束搭售：巧克力 / 贺卡 / 玩偶）----
            if not is_b2b:
                if festival in ("情人节", "520", "七夕节") and rng.random() < 0.22:
                    sku_id = "SKU035" if rng.random() < 0.65 else "SKU036"
                    m = ctx.sku_meta[sku_id]
                    lines.append(("SKU", sku_id, None, 1, m["price"], m["cost"]))
                card_p = 0.28 if festive else 0.07
                if rng.random() < card_p:
                    sku_id = "SKU037" if rng.random() < 0.62 else "SKU038"
                    m = ctx.sku_meta[sku_id]
                    lines.append(("SKU", sku_id, None, 1, m["price"], m["cost"]))
                if festival in ("母亲节", "教师节") and rng.random() < 0.10:
                    m = ctx.sku_meta["SKU032"]
                    lines.append(("SKU", "SKU032", None, 1, m["price"], m["cost"]))

            # ---- 金额 ----
            gross = sum(q * p for _, _, _, q, p, _ in lines)
            cost = sum(q * c for _, _, _, q, _, c in lines)

            # 会员等级折扣（金卡/钻石有常驻权益）
            member_discount = 0.0
            if customer["member_level"] == "金卡会员":
                member_discount = gross * 0.02
            elif customer["member_level"] == "钻石会员":
                member_discount = gross * 0.04

            # ---- 促销 ----
            promotion_id: Optional[str] = None
            promo_discount = 0.0
            active = promo_by_date.get(day, [])
            if active and rng.random() < 0.55:
                promo = active[int(rng.integers(len(active)))]
                ptype = promo["type"]
                if ptype == "折扣":
                    promo_discount = gross * (1 - promo["rate"])
                elif ptype == "满减":
                    if gross >= promo["threshold"]:
                        promo_discount = promo["reduce"]
                elif ptype == "买赠":
                    promo_discount = gross * 0.05
                elif ptype == "秒杀":
                    promo_discount = gross * (1 - promo["rate"])
                if promo_discount > 0:
                    promotion_id = promo["promotion_id"]

            # ---- 优惠券 ----
            coupon_id: Optional[str] = None
            coupon_discount = 0.0
            if rng.random() < 0.10:
                usable = [
                    c for c in coupon_list
                    if pd.Timestamp(c["valid_from"]).date() <= day <= pd.Timestamp(c["valid_to"]).date()
                    and gross >= float(c["threshold_amount"])
                ]
                if usable:
                    coupon = usable[int(rng.integers(len(usable)))]
                    coupon_id = coupon["coupon_id"]
                    face = float(coupon["face_value"])
                    coupon_discount = face if face > 0 else gross * 0.10

            discount = min(member_discount + promo_discount + coupon_discount, gross * 0.60)
            discount = round(discount, 2)

            # ---- 运费 ----
            freight = {"自提": 0.0, "同城配送": 9.9 if gross < 199 else 0.0, "快递": 12.0}[delivery_type]
            if is_b2b:
                freight = 0.0

            payable = round(gross - discount + freight, 2)
            freight_cost = {"自提": 0.0, "同城配送": 8.0, "快递": 11.0}[delivery_type]

            # ---- 时间 ----
            hour = int(rng.choice(24, p=HOUR_PROBS[channel_id]))
            order_time = datetime.combine(day, time(hour, int(rng.integers(0, 60))))
            pay_time = order_time + timedelta(minutes=int(rng.integers(1, 15)))
            if delivery_type == "自提":
                expect_hours, expect_noise = 1, 1
            elif delivery_type == "同城配送":
                expect_hours, expect_noise = 4, 2
            else:
                expect_hours, expect_noise = 48, 12
            expected_delivery = order_time + timedelta(
                hours=expect_hours, minutes=int(rng.integers(0, 60 * expect_noise))
            )

            days_ago = (ref_date - day).days
            status = _status_for_age(rng, days_ago)

            delivery_time = None
            if status in ("已完成", "已退款"):
                is_late = rng.random() < 0.08
                # 未迟到的订单必须"早于承诺时间"，否则会凭空制造 30% 的迟到率
                delay = int(rng.integers(30, 240)) if is_late else int(rng.integers(-120, 1))
                delivery_time = expected_delivery + timedelta(minutes=delay)
                if delivery_time < order_time:  # 自提极快
                    delivery_time = order_time + timedelta(minutes=int(rng.integers(20, 90)))

            # ---- 备注 ----
            if is_b2b:
                remark = REMARKS_B2B[int(rng.integers(len(REMARKS_B2B)))]
            elif festive:
                remark = REMARKS_FESTIVAL[int(rng.integers(len(REMARKS_FESTIVAL)))]
            else:
                remark = REMARKS_DAILY[int(rng.integers(len(REMARKS_DAILY)))]

            day_orders.append(
                {
                    "order_no": 0,
                    "customer_id": customer["customer_id"],
                    "store_id": store_id,
                    "channel_id": channel_id,
                    "order_time": order_time,
                    "pay_time": pay_time,
                    "status": status,
                    "delivery_type": delivery_type,
                    "expected_delivery_time": expected_delivery,
                    "delivery_time": delivery_time,
                    "total_amount": round(gross, 2),
                    "discount_amount": discount,
                    "freight_amount": freight,
                    "payable_amount": payable,
                    "cost_amount": round(cost + freight_cost + 2.0, 2),
                    "promotion_id": promotion_id,
                    "coupon_id": coupon_id,
                    "is_first_order": 1 if is_first else 0,
                    "remark": remark,
                    "_lines": lines,
                    "_cost": cost,
                }
            )

        # ---- 当天订单按时间排序 + 编号 ----
        day_orders.sort(key=lambda o: o["order_time"])
        for seq, o in enumerate(day_orders, start=1):
            order_id = f"SO{day:%Y%m%d}{seq:05d}"
            o["order_no"] = seq
            gross_total = o["total_amount"]
            lines = o.pop("_lines")
            prod_cost = o.pop("_cost")

            # 折扣与运费按行金额比例分摊（保证行合计 = 订单合计）
            line_gross = [q * p for _, _, _, q, p, _ in lines]
            sum_gross = sum(line_gross) or 1.0
            disc_left, freight_left = o["discount_amount"], o["freight_amount"]
            for i, (ptype, sku_id, bundle_id, qty, price, unit_cost) in enumerate(lines):
                is_last = i == len(lines) - 1
                line_g = round(line_gross[i], 2)
                if is_last:
                    disc_i = round(disc_left, 2)
                    freight_i = round(freight_left, 2)
                else:
                    disc_i = round(o["discount_amount"] * line_g / sum_gross, 2)
                    freight_i = round(o["freight_amount"] * line_g / sum_gross, 2)
                    disc_left -= disc_i
                    freight_left -= freight_i
                items.append(
                    {
                        "item_id": f"{order_id}-{i + 1:02d}",
                        "order_id": order_id,
                        "product_type": ptype,
                        "sku_id": sku_id,
                        "bundle_id": bundle_id,
                        "quantity": qty,
                        "unit_price": round(price, 2),
                        "discount_amount": disc_i,
                        "freight_amount": freight_i,
                        "line_amount": round(qty * price, 2),
                        "cost_amount": round(qty * unit_cost, 2),
                    }
                )

            if o["promotion_id"]:
                order_promos.append(
                    {
                        "order_id": order_id,
                        "promotion_id": o["promotion_id"],
                        "discount_amount": round(min(o["discount_amount"], gross_total * 0.55), 2),
                    }
                )

            o["order_id"] = order_id
            orders.append(o)

    orders_df = pd.DataFrame(orders).drop(columns=["order_no"])
    items_df = pd.DataFrame(items)

    # 字段顺序与数据库表保持一致（装载时按表头写列）
    orders_df = orders_df[
        ["order_id", "customer_id", "store_id", "channel_id", "order_time", "pay_time",
         "status", "delivery_type", "expected_delivery_time", "delivery_time",
         "total_amount", "discount_amount", "freight_amount", "payable_amount",
         "cost_amount", "promotion_id", "coupon_id", "is_first_order", "remark"]
    ]
    items_df = items_df[
        ["item_id", "order_id", "product_type", "sku_id", "bundle_id", "quantity",
         "unit_price", "discount_amount", "freight_amount", "line_amount", "cost_amount"]
    ]

    return {
        "orders": orders_df,
        "order_items": items_df,
        "order_promotions": pd.DataFrame(order_promos),
        "refs": {"ref_date": ref_date},
    }
