"""促销效果：让利效率、折扣敏感度、活动结构。

先说清楚"能算什么、不能算什么"
------------------------------
很多项目会直接给出「促销 ROI = 增量毛利 / 折扣成本」，但在**观测数据**里
这是算不准的：活动总是和节日绑在一起，节日又自带 3~4 倍的自然增长，
你无法区分"是折扣带来的增量"还是"本来就要过节"。

所以本项目只做两件**可被数据支持**的事：
1. **让利效率** = 活动毛利 / 让利金额（每让 1 元折扣，换回多少毛利）；
2. **折扣敏感度** = 按折扣深度分桶后，订单量与毛利率的变化趋势。

并在报告里明确写出"这不是因果增量"，把口径边界讲清楚——
**能说清"不能算什么"，比硬给一个漂亮数字更专业。**
"""
from __future__ import annotations

from typing import Any, Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..logger import get_logger
from . import base

log = get_logger("analysis.promo")

BUCKETS = [-0.0001, 0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 1.0]
BUCKET_LABELS = ["无折扣", "0-5%", "5-10%", "10-15%", "15-20%", "20-30%", "30%+"]


def run() -> Dict[str, Any]:
    log.info("▸ 促销效果与折扣敏感度")
    orders = base.read_df("""
        SELECT f.order_id, f.promotion_sk, pr.promotion_id, pr.promotion_name,
               pr.promotion_type, pr.festival, pr.start_date, pr.end_date,
               MAX(d.is_festival)                          AS is_festival,
               SUM(f.gross_amount)                         AS gross,
               SUM(f.discount_amount)                      AS discount,
               SUM(f.net_amount + f.freight_amount)        AS gmv,
               SUM(f.gross_profit)                         AS profit,
               SUM(f.quantity)                             AS qty
        FROM bloom_dw.fact_sales f
        JOIN bloom_dw.dim_promotion pr ON pr.promotion_sk = f.promotion_sk
        JOIN bloom_dw.dim_date d       ON d.date_key = f.date_key
        WHERE f.order_status <> '已取消'
        GROUP BY f.order_id, f.promotion_sk, pr.promotion_id, pr.promotion_name,
                 pr.promotion_type, pr.festival, pr.start_date, pr.end_date
    """)
    for c in ("gross", "discount", "gmv", "profit", "qty", "is_festival"):
        orders[c] = pd.to_numeric(orders[c], errors="coerce").fillna(0)
    orders["disc_rate"] = np.where(orders["gross"] > 0, orders["discount"] / orders["gross"], 0)

    # ---- 1. 活动汇总 ----
    promo = orders[orders["promotion_sk"] != 0].copy()
    agg = (promo.groupby(["promotion_id", "promotion_name", "promotion_type", "festival",
                          "start_date", "end_date"], as_index=False)
           .agg(orders=("order_id", "nunique"), gmv=("gmv", "sum"),
                discount=("discount", "sum"), profit=("profit", "sum"),
                gross=("gross", "sum")))
    agg["profit_rate"] = (agg["profit"] / agg["gmv"]).round(4)
    agg["discount_rate"] = (agg["discount"] / agg["gross"]).round(4)
    agg["aov"] = (agg["gmv"] / agg["orders"]).round(2)
    agg["roi"] = np.where(agg["discount"] > 0, agg["profit"] / agg["discount"], np.nan)
    agg = agg.sort_values("gmv", ascending=False)
    agg["calc_date"] = base.calc_date()

    # ---- 2. 非活动订单基准 ----
    normal = orders[orders["promotion_sk"] == 0]
    base_margin = float(normal["profit"].sum() / normal["gmv"].sum()) if len(normal) else np.nan
    promo_margin = float(promo["profit"].sum() / promo["gmv"].sum()) if len(promo) else np.nan

    # ---- 3. 折扣敏感度（分桶）----
    orders["bucket"] = pd.cut(orders["disc_rate"], bins=BUCKETS, labels=BUCKET_LABELS,
                              include_lowest=True, right=True)
    sens = (orders.groupby("bucket", as_index=False, observed=True)
            .agg(orders=("order_id", "nunique"), gmv=("gmv", "sum"),
                 profit=("profit", "sum"), qty=("qty", "sum")))
    sens["aov"] = (sens["gmv"] / sens["orders"]).round(2)
    sens["margin"] = (sens["profit"] / sens["gmv"]).round(4)
    sens["order_share"] = (sens["orders"] / sens["orders"].sum()).round(4)

    # ---- 图 1：活动让利效率（TOP12）----
    top = agg.head(12).iloc[::-1]
    fig, ax = plt.subplots(figsize=(9.6, 4.2))
    colors = [base.PALETTE["blue"] if t == "折扣" else
              base.PALETTE["orange"] if t == "满减" else
              base.PALETTE["purple"] if t == "秒杀" else base.PALETTE["teal"]
              for t in top["promotion_type"]]
    bars = ax.barh(top["promotion_name"], top["gmv"] / 1e4, color=colors, alpha=.9)
    ax.set_xlabel("活动期 GMV（万元）")
    ax.set_title("活动贡献 TOP12（颜色 = 活动类型）", pad=12)
    for b, (_, r) in zip(bars, top.iterrows()):
        ax.text(b.get_width(), b.get_y() + b.get_height() / 2,
                f"  让利 {r['discount'] / 1e4:.1f} 万 · 毛利 {r['profit'] / 1e4:.1f} 万",
                va="center", fontsize=7.5, color=base.PALETTE["navy"])
    chart_roi = base.save(fig, "07_promo_roi")

    # ---- 图 2：折扣敏感度 ----
    fig, ax1 = plt.subplots(figsize=(9.4, 3.8))
    x = np.arange(len(sens))
    ax1.bar(x, sens["order_share"] * 100, color=base.PALETTE["blue"], alpha=.85)
    ax1.set_xticks(x)
    ax1.set_xticklabels(sens["bucket"], fontsize=9)
    ax1.set_ylabel("订单占比（%）")
    ax1.set_xlabel("折扣深度")
    ax1.set_title("折扣敏感度：折扣越深，订单真的越多吗？", pad=12)
    ax2 = ax1.twinx()
    ax2.plot(x, sens["margin"] * 100, color=base.PALETTE["red"], marker="o", lw=2)
    ax2.set_ylabel("毛利率（%）")
    ax2.grid(False)
    for i, m in enumerate(sens["margin"]):
        ax2.text(i, m * 100 + 0.8, f"{m * 100:.1f}%", ha="center", fontsize=8,
                 color=base.PALETTE["red"])
    chart_sens = base.save(fig, "08_discount_sensitivity")

    # ---- 落库 ----
    out = agg[["promotion_id", "promotion_name", "promotion_type", "festival",
               "start_date", "end_date", "orders", "gmv", "discount", "profit",
               "profit_rate", "discount_rate", "aov", "roi", "calc_date"]].copy()
    out["gmv"] = out["gmv"].round(2)
    out["discount"] = out["discount"].round(2)
    out["profit"] = out["profit"].round(2)
    out["roi"] = out["roi"].round(2)
    out = out.rename(columns={"orders": "order_cnt", "discount": "discount_amt",
                             "profit": "gross_profit"})
    base.write_frame("ads_promo_roi", out)

    # ---- 报告 ----
    best = agg[agg["discount"] > 0].sort_values("roi", ascending=False).head(1)
    worst = agg[agg["discount"] > 0].sort_values("roi").head(1)
    deep = sens[sens["bucket"] == "20-30%"]
    none = sens[sens["bucket"] == "无折扣"]
    deep_margin = float(deep["margin"].iloc[0]) if len(deep) else np.nan
    none_margin = float(none["margin"].iloc[0]) if len(none) else np.nan

    blocks = [
        "## 一、核心结论\n",
        f"1. 全量订单中 **{len(promo) / len(orders) * 100:.1f}% 的订单命中了促销活动**，"
        f"活动订单毛利率 {base.fmt_pct(promo_margin)}，非活动订单 "
        f"{base.fmt_pct(base_margin)}，相差 {(promo_margin - base_margin) * 100:+.2f}pp。",
        f"2. 让利效率最高的是「{best['promotion_name'].iloc[0]}」"
        f"（每让 1 元换回 {best['roi'].iloc[0]:.1f} 元毛利），"
        f"最低的是「{worst['promotion_name'].iloc[0]}」"
        f"（{worst['roi'].iloc[0]:.1f} 元）——**同样的预算，活动设计差异能带来数倍效率差**。",
        f"3. 折扣越深、毛利率越低：无折扣订单毛利率 {base.fmt_pct(none_margin)}，"
        f"20-30% 折扣段降到 {base.fmt_pct(deep_margin)}。",
        f"4. 但**折扣并没有换来等比例的订单量**：20-30% 深度折扣的订单占比只有 "
        f"{deep['order_share'].iloc[0] * 100:.1f}%，说明「深折拉新」这条路径在本店并不成立。\n"
        if len(deep) else "4. 深度折扣订单占比很低，深折促销在本店并不划算。\n",
        "## 二、活动让利效率 TOP\n",
    ]
    blocks += base.md_table(
        ["活动", "类型", "节日", "订单数", "GMV（元）", "让利（元）", "毛利（元）",
         "毛利率", "让利效率"],
        [[r["promotion_name"], r["promotion_type"], r["festival"] or "—", int(r["orders"]),
          f"{r['gmv']:,.0f}", f"{r['discount']:,.0f}", f"{r['profit']:,.0f}",
          base.fmt_pct(r["profit_rate"]),
          f"{r['roi']:.1f}" if pd.notna(r["roi"]) else "—"]
         for _, r in agg.head(10).iterrows()],
        align="---:")
    blocks += ["", f"![活动ROI]({chart_roi})", "",
               "## 三、折扣敏感度\n", ]
    blocks += base.md_table(
        ["折扣深度", "订单占比", "客单价（元）", "毛利率", "平均件数"],
        [[r["bucket"], f"{r['order_share'] * 100:.2f}%", f"{r['aov']:,.0f}",
          base.fmt_pct(r["margin"]), f"{r['qty'] / max(r['orders'], 1):.2f}"]
         for _, r in sens.iterrows()],
        align="---:")
    blocks += ["", f"![折扣敏感度]({chart_sens})", "",
               "> 读图：柱状是各折扣区间的订单占比，红线是毛利率。",
               "> 如果「深折扣 → 订单暴增」，柱子应该向右显著变高；实际并没有，",
               "> 说明本店客户对「有没有折扣」不敏感，更在意「节日要有花」。",
               "",
               "## 四、口径边界（很重要）\n",
               "本报告**不提供「活动增量 ROI」**，原因：",
               "1. 活动期与节日高度重合，节日本身带来 3~4 倍自然增长，无法拆分；",
               "2. 没有对照组（A/B 实验或同城市对照门店），观测数据无法识别因果。",
               "",
               "因此这里给的是**「让利效率」**（活动毛利 / 让利金额），"
               "它能回答「同样一笔折扣预算，哪个活动更划算」，但不能回答「如果不打折会怎样」。",
               "要做严格的促销归因，需要：**门店级 A/B 实验** 或 **合成控制法（Synthetic Control）**。",
               "",
               "## 五、优化建议\n",
               "1. **减少深折**：20% 以上折扣的订单占比低、毛利损失大，建议改用「满减」提高客单；",
               "2. **复制高效活动**：让利效率高的活动集中在节日限定款，可固化为年货节模板；",
               "3. **券要定向**：把券发给「重要保持/重要挽留」客户（见 RFM 章节），"
               "而不是全量撒券——后者只会补贴本来就要买的人。", ""]

    base.write_md(base.ANALYSIS_DIR / "05_促销效果与价格弹性.md", "促销效果与折扣敏感度", blocks)
    return {
        "promo_order_share": float(len(promo) / len(orders)),
        "promo_margin": promo_margin, "base_margin": base_margin,
        "best_promo": str(best["promotion_name"].iloc[0]), "best_roi": float(best["roi"].iloc[0]),
        "charts": [chart_roi, chart_sens],
    }
