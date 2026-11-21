"""渠道与门店效率：佣金后真实利润、坪效人效。

为什么渠道分析要看"佣金后"？
--------------------------
美团抽 18%、抖音抽 12%，GMV 看着差不多，扣完佣金后可能是天壤之别。
只按 GMV 排名会得出"美团是第二大渠道"的错误结论，
按"佣金后毛利"排名才看得出谁在真正赚钱。
"""
from __future__ import annotations

from typing import Any, Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..logger import get_logger
from . import base

log = get_logger("analysis.channel")


def run() -> Dict[str, Any]:
    log.info("▸ 渠道与门店效率")
    ch = base.read_df("""
        SELECT ch.channel_id, ch.channel_name, ch.channel_type, ch.commission_rate,
               COUNT(DISTINCT f.order_id)            AS orders,
               COUNT(DISTINCT f.customer_sk)         AS customers,
               SUM(f.net_amount + f.freight_amount)  AS gmv,
               SUM(f.gross_profit)                   AS profit
        FROM bloom_dw.fact_sales f
        JOIN bloom_dw.dim_channel ch ON ch.channel_sk = f.channel_sk
        WHERE f.order_status <> '已取消'
        GROUP BY ch.channel_id, ch.channel_name, ch.channel_type, ch.commission_rate
    """)
    for c in ("orders", "customers", "gmv", "profit", "commission_rate"):
        ch[c] = pd.to_numeric(ch[c], errors="coerce").fillna(0)
    ch["aov"] = (ch["gmv"] / ch["orders"]).round(2)
    ch["gmv_share"] = (ch["gmv"] / ch["gmv"].sum()).round(4)
    ch["commission_amt"] = (ch["gmv"] * ch["commission_rate"]).round(2)
    ch["net_profit"] = (ch["profit"] - ch["commission_amt"]).round(2)
    ch["net_margin"] = (ch["net_profit"] / ch["gmv"]).round(4)
    ch = ch.sort_values("gmv", ascending=False).reset_index(drop=True)

    store = base.read_df("""
        SELECT ds.store_id, ds.store_name, ds.city, ds.area_sqm, ds.staff_count,
               COUNT(DISTINCT f.order_id)            AS orders,
               SUM(f.net_amount + f.freight_amount)  AS gmv,
               SUM(f.gross_profit)                   AS profit
        FROM bloom_dw.fact_sales f
        JOIN bloom_dw.dim_store ds ON ds.store_sk = f.store_sk
        WHERE f.order_status <> '已取消'
        GROUP BY ds.store_id, ds.store_name, ds.city, ds.area_sqm, ds.staff_count
    """)
    for c in ("orders", "gmv", "profit", "area_sqm", "staff_count"):
        store[c] = pd.to_numeric(store[c], errors="coerce").fillna(0)
    store["per_sqm"] = (store["gmv"] / store["area_sqm"].replace(0, np.nan)).round(0)
    store["per_staff"] = (store["gmv"] / store["staff_count"].replace(0, np.nan)).round(0)
    store = store.sort_values("gmv", ascending=False).reset_index(drop=True)

    # ---- 图 1：渠道效率气泡 ----
    fig, ax = plt.subplots(figsize=(9.4, 4.8))
    colors = [base.PALETTE["green"] if t == "线下" else base.PALETTE["blue"]
              for t in ch["channel_type"]]
    ax.scatter(ch["aov"], ch["net_margin"] * 100, s=ch["gmv"] / 1500, alpha=0.65,
               color=colors, edgecolors="white", linewidths=1.5)
    for _, r in ch.iterrows():
        ax.annotate(f"{r['channel_name']}\nGMV {r['gmv'] / 1e4:.0f}万",
                    (r["aov"], r["net_margin"] * 100), textcoords="offset points",
                    xytext=(0, 14), ha="center", fontsize=8.5)
    ax.set_xlabel("客单价（元）")
    ax.set_ylabel("佣金后净利率（%）")
    ax.set_title("渠道效率：气泡越大 GMV 越高（绿=线下，蓝=线上）", pad=14)
    ax.margins(y=0.25)
    chart_ch = base.save(fig, "09_channel_efficiency")

    # ---- 图 2：门店 GMV 与坪效 ----
    fig, ax1 = plt.subplots(figsize=(9.6, 3.8))
    x = np.arange(len(store))
    ax1.bar(x, store["gmv"] / 1e4, color=base.PALETTE["blue"], alpha=.85, label="GMV")
    ax1.set_xticks(x)
    ax1.set_xticklabels(store["store_name"], rotation=12, ha="right", fontsize=9)
    ax1.set_ylabel("GMV（万元）")
    ax1.set_title("门店 GMV 与坪效（每平米产出）", pad=12)
    ax2 = ax1.twinx()
    ax2.plot(x, store["per_sqm"] / 1e4, color=base.PALETTE["orange"], marker="o", lw=2,
             label="坪效（万元/㎡）")
    ax2.set_ylabel("坪效（万元/㎡）")
    ax2.grid(False)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")
    chart_store = base.save(fig, "10_store_efficiency")

    # ---- 落库 ----
    out = ch[["channel_id", "channel_name", "channel_type", "orders", "customers", "gmv",
              "aov", "gmv_share", "commission_amt", "net_profit", "net_margin"]].copy()
    out = out.rename(columns={"orders": "order_cnt", "customers": "customer_cnt"})
    out["calc_date"] = base.calc_date()
    base.write_frame("ads_channel_efficiency", out)

    # ---- 报告 ----
    top_gmv = ch.iloc[0]
    best_margin = ch.sort_values("net_margin", ascending=False).iloc[0]
    worst_margin = ch.sort_values("net_margin").iloc[0]
    online_share = ch[ch["channel_type"] == "线上"]["gmv"].sum() / ch["gmv"].sum()
    commission_total = ch["commission_amt"].sum()
    blocks = [
        "## 一、核心结论\n",
        f"1. GMV 第一是「{top_gmv['channel_name']}」（{top_gmv['gmv'] / 1e4:.0f} 万，"
        f"占 {top_gmv['gmv_share'] * 100:.1f}%），但**佣金后净利率最高的是"
        f"「{best_margin['channel_name']}」（{best_margin['net_margin'] * 100:.1f}%）**。",
        f"2. 线上渠道贡献 {online_share * 100:.1f}% 的 GMV，"
        f"但全渠道一年被平台抽走佣金 **{commission_total / 1e4:.1f} 万元**——"
        f"相当于 {commission_total / ch['gmv'].sum() * 100:.1f}% 的 GMV。",
        f"3. 净利率最低的是「{worst_margin['channel_name']}」"
        f"（{worst_margin['net_margin'] * 100:.1f}%），主要被 "
        f"{worst_margin['commission_rate'] * 100:.0f}% 的平台佣金吃掉。",
        f"4. 门店之间坪效差异明显：最高 {store['per_sqm'].max() / 1e4:.2f} 万/㎡，"
        f"最低 {store['per_sqm'].min() / 1e4:.2f} 万/㎡，"
        f"差 {store['per_sqm'].max() / store['per_sqm'].min():.1f} 倍。\n",
        "## 二、渠道效率明细\n",
    ]
    blocks += base.md_table(
        ["渠道", "类型", "订单数", "GMV（元）", "GMV占比", "客单价", "佣金率",
         "佣金（元）", "佣金后毛利（元）", "净利率"],
        [[r["channel_name"], r["channel_type"], f"{int(r['orders']):,}", f"{r['gmv']:,.0f}",
          f"{r['gmv_share'] * 100:.1f}%", f"{r['aov']:,.0f}",
          f"{r['commission_rate'] * 100:.1f}%", f"{r['commission_amt']:,.0f}",
          f"{r['net_profit']:,.0f}", f"{r['net_margin'] * 100:.1f}%"]
         for _, r in ch.iterrows()],
        align="---:")
    blocks += ["", f"![渠道效率]({chart_ch})", "",
               "> 读图：右上角是「高客单 + 高净利」的理想渠道；",
               "> 左下角是「低客单 + 低净利」的待优化渠道；气泡大小代表 GMV 体量。",
               "",
               "## 三、门店效率\n", ]
    blocks += base.md_table(
        ["门店", "城市", "订单数", "GMV（元）", "面积（㎡）", "坪效（元/㎡）", "人效（元/人）"],
        [[r["store_name"], r["city"], f"{int(r['orders']):,}", f"{r['gmv']:,.0f}",
          f"{int(r['area_sqm'])}", f"{r['per_sqm']:,.0f}", f"{r['per_staff']:,.0f}"]
         for _, r in store.iterrows()],
        align="---:")
    blocks += ["", f"![门店效率]({chart_store})", "",
               "> 读图：柱子是 GMV，折线是坪效。线上云仓面积大、坪效低是正常的（它承担发货功能），",
               "> 真正要对比的是同城门店之间的差距。",
               "",
               "## 四、渠道策略建议\n",
               "1. **把美团当「流量入口」而不是「利润中心」**：18% 佣金下净利率最低，"
               "适合做新客首单，但要引导加企微/小程序做二次复购；",
               "2. **抖音做内容种草 + 到店自提**：自提可省配送成本，"
               "且自提客户的复购率通常高于纯外卖客户；",
               "3. **小程序是利润池**：佣金几乎为零，应把高价值客户（RFM 重要价值/重要保持）沉淀到私域；",
               "4. **企业团购客单价最高**，但订单量少，需要专门的 B 端销售动作（年节礼品、开业花篮）。", ""]

    base.write_md(base.ANALYSIS_DIR / "06_渠道与门店效率.md", "渠道与门店效率", blocks)
    return {
        "top_channel": str(top_gmv["channel_name"]),
        "best_margin_channel": str(best_margin["channel_name"]),
        "best_margin": float(best_margin["net_margin"]),
        "online_share": float(online_share),
        "commission_total": float(commission_total),
        "charts": [chart_ch, chart_store],
    }
