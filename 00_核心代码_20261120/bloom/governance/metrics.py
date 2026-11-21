"""指标体系：原子指标 / 派生指标 / 复合指标，共 48 个。

什么是指标体系？
--------------
不是"能查出来的数都叫指标"，而是**分层、有口径、可复用**的一套定义：

| 层级 | 定义 | 例子 |
| --- | --- | --- |
| 原子指标 | 不可再拆的度量，直接来自事实表 | GMV、订单数、销量 |
| 派生指标 | 原子指标 + 业务限定 + 时间周期 | 近 12 个月 GMV、新客 GMV |
| 复合指标 | 多个指标运算得来 | 客单价 = GMV / 订单数、毛利率 |

为什么要把口径写成代码？
--------------------
口径一旦散落在几十份 SQL 里，就会出现"同一个毛利率，看板 42.1%、报告 42.3%"。
这里每个指标都绑定唯一的一段 SQL，跑出来的值就是全站唯一口径。

指标体系还解决了三个实际问题：
1. **对齐语言**：业务说"客单价"、技术说"单价"，看同一张表就不会吵；
2. **口径变更可追溯**：改公式只改一处，且能在 Git 里看到谁改的；
3. **自动计算当前值**：文档不是静态的，每跑一次就刷新一次真实数值。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .. import db
from ..config import MYSQL_DW_DB
from ..logger import get_logger

log = get_logger("metrics")


@dataclass(frozen=True)
class Metric:
    code: str
    name: str
    layer: str        # 原子 / 派生 / 复合
    domain: str       # 经营 / 增长 / 用户 / 商品 / 渠道 / 履约库存 / 营销
    formula: str      # 业务口径描述
    sql: str          # 取数 SQL（单值）
    unit: str = "元"
    cycle: str = "日"  # 统计周期
    percent: bool = False


_ACTIVE = "f.order_status <> '已取消'"

METRICS: List[Metric] = [
    # ---------------- 经营 ----------------
    Metric("M01", "GMV", "原子", "经营", "实付金额合计（含运费，剔除取消单）",
           f"SELECT SUM(f.net_amount + f.freight_amount) FROM bloom_dw.fact_sales f WHERE {_ACTIVE}"),
    Metric("M02", "商品实收", "原子", "经营", "商品金额合计（不含运费）",
           f"SELECT SUM(f.net_amount) FROM bloom_dw.fact_sales f WHERE {_ACTIVE}"),
    Metric("M03", "订单数", "原子", "经营", "去重订单数",
           f"SELECT COUNT(DISTINCT f.order_id) FROM bloom_dw.fact_sales f WHERE {_ACTIVE}",
           unit="单"),
    Metric("M04", "成交客户数", "原子", "经营", "去重客户数",
           f"SELECT COUNT(DISTINCT f.customer_sk) FROM bloom_dw.fact_sales f WHERE {_ACTIVE}",
           unit="人"),
    Metric("M05", "客单价 AOV", "复合", "经营", "GMV / 订单数",
           f"SELECT SUM(f.net_amount + f.freight_amount) / COUNT(DISTINCT f.order_id) "
           f"FROM bloom_dw.fact_sales f WHERE {_ACTIVE}"),
    Metric("M06", "件单价", "复合", "经营", "商品实收 / 销售件数",
           f"SELECT SUM(f.net_amount) / NULLIF(SUM(f.quantity), 0) "
           f"FROM bloom_dw.fact_sales f WHERE {_ACTIVE}"),
    Metric("M07", "毛利额", "原子", "经营", "商品实收 - 商品成本",
           f"SELECT SUM(f.gross_profit) FROM bloom_dw.fact_sales f WHERE {_ACTIVE}"),
    Metric("M08", "毛利率", "复合", "经营", "毛利额 / GMV",
           f"SELECT SUM(f.gross_profit) / SUM(f.net_amount + f.freight_amount) "
           f"FROM bloom_dw.fact_sales f WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M09", "折扣率", "复合", "经营", "优惠合计 / 原价合计",
           f"SELECT SUM(f.discount_amount) / SUM(f.gross_amount) "
           f"FROM bloom_dw.fact_sales f WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M10", "退款金额", "原子", "经营", "退款单金额合计",
           "SELECT SUM(refund_amount) FROM bloom_oltp.refunds"),

    # ---------------- 增长 ----------------
    Metric("M11", "GMV 同比", "派生", "增长", "近 12 个月 GMV / 前 12 个月 GMV - 1",
           "SELECT (SUM(CASE WHEN d.full_date > '2025-11-18' THEN f.net_amount + f.freight_amount ELSE 0 END) "
           "- SUM(CASE WHEN d.full_date > '2024-11-18' AND d.full_date <= '2025-11-18' "
           "THEN f.net_amount + f.freight_amount ELSE 0 END)) "
           "/ NULLIF(SUM(CASE WHEN d.full_date > '2024-11-18' AND d.full_date <= '2025-11-18' "
           "THEN f.net_amount + f.freight_amount ELSE 0 END), 0) "
           "FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_date d ON d.date_key = f.date_key "
           f"WHERE {_ACTIVE} AND d.full_date > '2024-11-18'", unit="%", percent=True, cycle="月"),
    Metric("M12", "GMV 环比", "派生", "增长", "本月 GMV / 上月 GMV - 1",
           "SELECT (SUM(CASE WHEN d.month_key = '2026-10' THEN f.net_amount + f.freight_amount ELSE 0 END) "
           "/ NULLIF(SUM(CASE WHEN d.month_key = '2026-09' THEN f.net_amount + f.freight_amount ELSE 0 END), 0)) - 1 "
           "FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_date d ON d.date_key = f.date_key "
           f"WHERE {_ACTIVE} AND d.month_key IN ('2026-09','2026-10')", unit="%", percent=True, cycle="月"),
    Metric("M13", "订单数同比", "派生", "增长", "近 12 个月订单数同比",
           "SELECT (COUNT(DISTINCT CASE WHEN d.full_date > '2025-11-18' THEN f.order_id END) - "
           "COUNT(DISTINCT CASE WHEN d.full_date <= '2025-11-18' THEN f.order_id END)) "
           "/ NULLIF(COUNT(DISTINCT CASE WHEN d.full_date <= '2025-11-18' THEN f.order_id END), 0) "
           "FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_date d ON d.date_key = f.date_key "
           f"WHERE {_ACTIVE} AND d.full_date > '2024-11-18'", unit="%", percent=True, cycle="月"),
    Metric("M14", "客单价同比", "派生", "增长", "近 12 个月客单价同比",
           "SELECT ((SUM(CASE WHEN d.full_date > '2025-11-18' THEN f.net_amount + f.freight_amount ELSE 0 END) "
           "/ COUNT(DISTINCT CASE WHEN d.full_date > '2025-11-18' THEN f.order_id END)) "
           "/ NULLIF(SUM(CASE WHEN d.full_date <= '2025-11-18' THEN f.net_amount + f.freight_amount ELSE 0 END) "
           "/ COUNT(DISTINCT CASE WHEN d.full_date <= '2025-11-18' THEN f.order_id END), 0)) - 1 "
           "FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_date d ON d.date_key = f.date_key "
           f"WHERE {_ACTIVE} AND d.full_date > '2024-11-18'", unit="%", percent=True, cycle="月"),
    Metric("M15", "新客 GMV 占比", "派生", "增长", "首单 GMV / 总 GMV",
           f"SELECT SUM(CASE WHEN f.is_first_order = 1 THEN f.net_amount + f.freight_amount ELSE 0 END) "
           f"/ SUM(f.net_amount + f.freight_amount) FROM bloom_dw.fact_sales f WHERE {_ACTIVE}",
           unit="%", percent=True),

    # ---------------- 用户 ----------------
    Metric("M16", "复购率", "复合", "用户", "购买 ≥2 次的客户 / 总成交客户",
           "SELECT SUM(CASE WHEN p.frequency >= 2 THEN 1 ELSE 0 END) / COUNT(*) "
           "FROM bloom_dw.dws_customer_profile p", unit="%", percent=True),
    Metric("M17", "人均订单数", "复合", "用户", "订单数 / 成交客户数",
           "SELECT SUM(p.frequency) / COUNT(*) FROM bloom_dw.dws_customer_profile p", unit="单"),
    Metric("M18", "ARPU", "复合", "用户", "累计消费 / 成交客户数",
           "SELECT AVG(p.monetary) FROM bloom_dw.dws_customer_profile p"),
    Metric("M19", "会员渗透率", "复合", "用户", "银卡及以上会员 / 个人客户",
           "SELECT SUM(CASE WHEN p.member_level IN ('银卡会员','金卡会员','钻石会员') "
           "THEN 1 ELSE 0 END) / COUNT(*) FROM bloom_dw.dws_customer_profile p "
           "WHERE p.customer_type = '个人'",
           unit="%", percent=True),
    Metric("M20", "高价值客户占比", "派生", "用户", "累计消费 ≥5000 的客户占比",
           "SELECT AVG(p.high_value_flag) FROM bloom_dw.dws_customer_profile p",
           unit="%", percent=True),
    Metric("M21", "沉睡客户占比", "派生", "用户", "91~180 天未购买的客户占比",
           "SELECT SUM(CASE WHEN p.lifecycle_stage = '沉睡' THEN 1 ELSE 0 END) / COUNT(*) "
           "FROM bloom_dw.dws_customer_profile p", unit="%", percent=True),
    Metric("M22", "流失客户占比", "派生", "用户", "超过 180 天未购买的客户占比",
           "SELECT SUM(CASE WHEN p.lifecycle_stage = '流失' THEN 1 ELSE 0 END) / COUNT(*) "
           "FROM bloom_dw.dws_customer_profile p", unit="%", percent=True),
    Metric("M23", "企业客户 GMV 占比", "派生", "用户", "企业客户 GMV / 总 GMV",
           f"SELECT SUM(CASE WHEN dc.customer_type = '企业' THEN f.net_amount + f.freight_amount ELSE 0 END) "
           f"/ SUM(f.net_amount + f.freight_amount) FROM bloom_dw.fact_sales f "
           "JOIN bloom_dw.dim_customer dc ON dc.customer_sk = f.customer_sk "
           f"WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M24", "客户年留存率", "复合", "用户", "2025 年购买且 2026 年仍购买的客户占比",
           "SELECT SUM(CASE WHEN y26 > 0 THEN 1 ELSE 0 END) / COUNT(*) FROM ("
           " SELECT f.customer_sk,"
           " SUM(CASE WHEN d.year = 2025 THEN 1 ELSE 0 END) AS y25,"
           " SUM(CASE WHEN d.year = 2026 THEN 1 ELSE 0 END) AS y26"
           " FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_date d ON d.date_key = f.date_key"
           f" WHERE {_ACTIVE} AND d.year IN (2025, 2026) GROUP BY f.customer_sk HAVING y25 > 0) t",
           unit="%", percent=True, cycle="年"),

    # ---------------- 商品 ----------------
    Metric("M25", "动销商品数", "原子", "商品", "售出过的商品（SKU+花束）数量",
           "SELECT COUNT(*) FROM bloom_dw.dws_product_profile WHERE qty > 0", unit="个"),
    Metric("M26", "商品动销率", "复合", "商品", "动销商品 / 在售商品",
           "SELECT (SELECT COUNT(*) FROM bloom_dw.dws_product_profile WHERE qty > 0) / "
           "(SELECT COUNT(*) FROM bloom_dw.dim_product)", unit="%", percent=True),
    Metric("M27", "TOP10 商品 GMV 集中度", "复合", "商品", "销售额前 10 商品占总额比",
           "SELECT SUM(revenue) / (SELECT SUM(revenue) FROM bloom_dw.dws_product_profile) FROM ("
           " SELECT revenue FROM bloom_dw.dws_product_profile ORDER BY revenue DESC LIMIT 10) t",
           unit="%", percent=True),
    Metric("M28", "花束收入占比", "复合", "商品", "花束（BUNDLE）销售额 / 总销售额",
           "SELECT SUM(CASE WHEN p.product_type = 'BUNDLE' THEN f.net_amount ELSE 0 END) / SUM(f.net_amount) "
           "FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_product p ON p.product_sk = f.product_sk "
           f"WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M29", "搭售率", "复合", "商品", "同时含花束与散卖商品的订单占比",
           "SELECT SUM(CASE WHEN has_bundle > 0 AND has_sku > 0 THEN 1 ELSE 0 END) / COUNT(*) FROM ("
           " SELECT f.order_id,"
           " SUM(CASE WHEN p.product_type = 'BUNDLE' THEN 1 ELSE 0 END) AS has_bundle,"
           " SUM(CASE WHEN p.product_type = 'SKU' THEN 1 ELSE 0 END) AS has_sku"
           " FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_product p ON p.product_sk = f.product_sk"
           f" WHERE {_ACTIVE} GROUP BY f.order_id) t", unit="%", percent=True),
    Metric("M30", "A 类商品数", "派生", "商品", "累计销售额贡献前 80% 的商品数",
           "SELECT COUNT(*) FROM bloom_dw.dws_product_profile WHERE abc_class = 'A'", unit="个"),
    Metric("M31", "A 类商品毛利贡献", "复合", "商品", "A 类商品毛利 / 全部毛利",
           "SELECT SUM(CASE WHEN abc_class = 'A' THEN gross_profit ELSE 0 END) / SUM(gross_profit) "
           "FROM bloom_dw.dws_product_profile", unit="%", percent=True),
    Metric("M32", "滞销商品数", "派生", "商品", "销量处于后 20% 的商品数",
           "SELECT COUNT(*) FROM (SELECT qty, NTILE(5) OVER (ORDER BY qty) AS bucket "
           "FROM bloom_dw.dws_product_profile) t WHERE bucket = 1", unit="个"),

    # ---------------- 渠道 ----------------
    Metric("M33", "线上 GMV 占比", "复合", "渠道", "线上渠道 GMV / 总 GMV",
           f"SELECT SUM(CASE WHEN ch.channel_type = '线上' THEN f.net_amount + f.freight_amount ELSE 0 END) "
           f"/ SUM(f.net_amount + f.freight_amount) FROM bloom_dw.fact_sales f "
           "JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk "
           f"WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M34", "平台佣金合计", "原子", "渠道", "GMV × 渠道佣金率",
           f"SELECT SUM((f.net_amount + f.freight_amount) * ch.commission_rate) "
           "FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk "
           f"WHERE {_ACTIVE}"),
    Metric("M35", "佣金后净利率", "复合", "渠道", "(毛利 - 佣金) / GMV",
           f"SELECT (SUM(f.gross_profit) - SUM((f.net_amount + f.freight_amount) * ch.commission_rate)) "
           f"/ SUM(f.net_amount + f.freight_amount) FROM bloom_dw.fact_sales f "
           "JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk "
           f"WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M36", "抖音 GMV 占比", "派生", "渠道", "抖音直播渠道 GMV / 总 GMV",
           f"SELECT SUM(CASE WHEN ch.channel_name = '抖音直播' THEN f.net_amount + f.freight_amount ELSE 0 END) "
           f"/ SUM(f.net_amount + f.freight_amount) FROM bloom_dw.fact_sales f "
           "JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk "
           f"WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M37", "最高客单价渠道", "复合", "渠道", "各渠道客单价的最大值",
           f"SELECT MAX(aov) FROM (SELECT SUM(f.net_amount + f.freight_amount) / "
           "COUNT(DISTINCT f.order_id) AS aov FROM bloom_dw.fact_sales f "
           "JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk "
           f"WHERE {_ACTIVE} GROUP BY ch.channel_name) t"),

    # ---------------- 履约 / 库存 ----------------
    Metric("M38", "订单完成率", "复合", "履约库存", "已完成订单 / 非取消订单",
           "SELECT COUNT(DISTINCT CASE WHEN f.order_status = '已完成' THEN f.order_id END) "
           "/ COUNT(DISTINCT f.order_id) FROM bloom_dw.fact_sales f "
           "WHERE f.order_status <> '已取消'", unit="%", percent=True),
    Metric("M39", "平均履约时长", "复合", "履约库存", "下单到送达的平均小时数",
           "SELECT AVG(TIMESTAMPDIFF(MINUTE, order_time, delivery_time) / 60) "
           "FROM bloom_oltp.orders WHERE delivery_time IS NOT NULL", unit="小时"),
    Metric("M40", "准时送达率", "复合", "履约库存", "实际送达 ≤ 承诺送达 的订单占比",
           "SELECT AVG(CASE WHEN delivery_time <= expected_delivery_time THEN 1 ELSE 0 END) "
           "FROM bloom_oltp.orders WHERE delivery_time IS NOT NULL",
           unit="%", percent=True),
    Metric("M41", "库存周转天数", "复合", "履约库存", "平均库存金额 / 日均出库成本",
           "SELECT SUM(i.on_hand_qty * s.cost_price) / NULLIF(SUM(i.out_qty * s.cost_price), 0) "
           "FROM bloom_oltp.inventory_daily i JOIN bloom_oltp.flower_skus s ON s.sku_id = i.sku_id",
           unit="天"),
    Metric("M42", "损耗率", "复合", "履约库存", "损耗金额 / 采购金额",
           "SELECT SUM(waste_amount) / NULLIF(SUM(purchase_amount), 0) "
           "FROM bloom_oltp.inventory_daily", unit="%", percent=True),
    Metric("M43", "低库存天数占比", "复合", "履约库存", "库存低于安全线（60）的 SKU-天 占比",
           "SELECT AVG(CASE WHEN on_hand_qty < 60 THEN 1 ELSE 0 END) "
           "FROM bloom_oltp.inventory_daily", unit="%", percent=True),

    # ---------------- 营销 ----------------
    Metric("M44", "活动订单占比", "复合", "营销", "命中促销活动的订单 / 总订单",
           "SELECT COUNT(DISTINCT CASE WHEN f.promotion_sk <> 0 THEN f.order_id END) "
           f"/ COUNT(DISTINCT f.order_id) FROM bloom_dw.fact_sales f WHERE {_ACTIVE}",
           unit="%", percent=True),
    Metric("M45", "用券订单占比", "复合", "营销", "使用优惠券的订单 / 总订单",
           "SELECT SUM(CASE WHEN coupon_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) "
           "FROM bloom_oltp.orders WHERE status <> '已取消'", unit="%", percent=True),
    Metric("M46", "促销毛利率差", "复合", "营销", "活动订单毛利率 - 非活动订单毛利率",
           f"SELECT (SUM(CASE WHEN f.promotion_sk <> 0 THEN f.gross_profit ELSE 0 END) "
           f"/ NULLIF(SUM(CASE WHEN f.promotion_sk <> 0 THEN f.net_amount + f.freight_amount ELSE 0 END), 0))"
           f" - (SUM(CASE WHEN f.promotion_sk = 0 THEN f.gross_profit ELSE 0 END) "
           f"/ NULLIF(SUM(CASE WHEN f.promotion_sk = 0 THEN f.net_amount + f.freight_amount ELSE 0 END), 0)) "
           f"FROM bloom_dw.fact_sales f WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M47", "节日 GMV 占比", "派生", "营销", "节日期间 GMV / 总 GMV",
           f"SELECT SUM(CASE WHEN d.is_festival = 1 THEN f.net_amount + f.freight_amount ELSE 0 END) "
           f"/ SUM(f.net_amount + f.freight_amount) FROM bloom_dw.fact_sales f "
           "JOIN bloom_dw.dim_date d ON d.date_key = f.date_key "
           f"WHERE {_ACTIVE}", unit="%", percent=True),
    Metric("M48", "节日客单价倍数", "复合", "营销", "节日客单价 / 平日客单价",
           f"SELECT (SUM(CASE WHEN d.is_festival = 1 THEN f.net_amount + f.freight_amount ELSE 0 END) "
           f"/ NULLIF(COUNT(DISTINCT CASE WHEN d.is_festival = 1 THEN f.order_id END), 0)) "
           f"/ (SUM(CASE WHEN d.is_festival = 0 THEN f.net_amount + f.freight_amount ELSE 0 END) "
           f"/ NULLIF(COUNT(DISTINCT CASE WHEN d.is_festival = 0 THEN f.order_id END), 0)) "
           f"FROM bloom_dw.fact_sales f JOIN bloom_dw.dim_date d ON d.date_key = f.date_key "
           f"WHERE {_ACTIVE}", unit="倍"),
]


def compute_all() -> List[Dict[str, Any]]:
    """逐个指标取数，返回带当前值的列表。"""
    out: List[Dict[str, Any]] = []
    for m in METRICS:
        value: Any = None
        try:
            row = db.fetch_one(m.sql, database=MYSQL_DW_DB)
            value = list(row.values())[0] if row else None
            value = float(value) if value is not None else None
        except Exception as exc:  # noqa: BLE001
            log.warning("指标 %s 计算失败：%s", m.code, exc)
            value = None
        out.append({
            "code": m.code, "name": m.name, "layer": m.layer, "domain": m.domain,
            "formula": m.formula, "unit": m.unit, "cycle": m.cycle,
            "value": value, "percent": m.percent,
        })
    log.info("指标计算完成：%d 个", len(out))
    return out


def format_value(item: Dict[str, Any]) -> str:
    v = item["value"]
    if v is None:
        return "—"
    if item["percent"]:
        return f"{v * 100:.2f}%"
    if item["unit"] == "元":
        return f"{v:,.2f}"
    if item["unit"] in ("天", "小时", "倍"):
        return f"{v:,.2f}"
    if item["unit"] in ("单", "人", "个"):
        return f"{v:,.0f}"
    return f"{v:,.2f}"
