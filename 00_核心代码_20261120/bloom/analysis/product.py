"""商品结构：ABC 帕累托 + 波士顿矩阵。

为什么这两个方法要一起用？
------------------------
- **ABC 帕累托**回答"谁贡献了销售额"：少数商品贡献多数收入，管理重心应在 A 类；
- **波士顿矩阵**回答"谁值得继续投"：光看销售额会把"正在衰退的金牛"当成宝贝，
  必须叠加增长率维度，才能区分「明星（增长+份额）」「金牛（份额但衰退）」
  「问题（增长但份额小）」「瘦狗（双低）」。

口径说明（重要）
--------------
真正的 BCG 需要"相对市场份额 = 自己份额 / 最大竞争对手份额"，
本项目是单一企业数据，拿不到竞品份额，因此用
**「本品类内相对份额 = 本商品销售额 / 该品类最大单品销售额」** 作为横轴代理，
并在报告里明确标注——这是分析诚实性的一部分。
"""
from __future__ import annotations

from typing import Any, Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..logger import get_logger
from . import base

log = get_logger("analysis.product")

QUADRANT_COLOR = {
    "明星": base.PALETTE["red"], "金牛": base.PALETTE["blue"],
    "问题": base.PALETTE["orange"], "瘦狗": base.PALETTE["gray"],
}


def _bcg(row, share_cut: float, growth_cut: float) -> str:
    high_share = row["rel_share"] >= share_cut
    high_growth = row["yoy_growth"] >= growth_cut
    if high_share and high_growth:
        return "明星"
    if high_share and not high_growth:
        return "金牛"
    if not high_share and high_growth:
        return "问题"
    return "瘦狗"


def run() -> Dict[str, Any]:
    log.info("▸ 商品结构与波士顿矩阵")
    prof = base.read_df("""
        SELECT product_id, product_name, product_type, category, price_band, qty,
               revenue, cost, gross_profit, profit_rate, order_cnt, customer_cnt,
               repeat_customer_cnt, festival_qty_ratio, yoy_growth, revenue_share, abc_class
        FROM bloom_dw.dws_product_profile
    """)
    for c in ("qty", "revenue", "cost", "gross_profit", "profit_rate", "order_cnt",
              "customer_cnt", "repeat_customer_cnt", "festival_qty_ratio",
              "yoy_growth", "revenue_share"):
        prof[c] = pd.to_numeric(prof[c], errors="coerce")
    prof["yoy_growth"] = prof["yoy_growth"].fillna(0.0)
    prof = prof.sort_values("revenue", ascending=False).reset_index(drop=True)

    # ---- ABC 帕累托 ----
    prof["cum_share"] = prof["revenue"].cumsum() / prof["revenue"].sum()
    prof["rank"] = np.arange(1, len(prof) + 1)
    a_cnt = int((prof["cum_share"] <= 0.80).sum()) + 1
    b_cnt = int((prof["cum_share"] <= 0.95).sum()) + 1 - a_cnt
    c_cnt = len(prof) - a_cnt - b_cnt

    # ---- 波士顿矩阵 ----
    cat_max = prof.groupby("category")["revenue"].transform("max")
    prof["rel_share"] = (prof["revenue"] / cat_max).fillna(0)
    # 象限切分用中位数（数据自适应），而不是拍 0.5 / 0 这种固定阈值：
    # 固定阈值会让"品类内只有 2-3 个商品"的品类里几乎全是高份额，导致满地"明星"。
    share_cut = float(prof["rel_share"].median())
    growth_cut = float(prof["yoy_growth"].median())
    prof["bcg_class"] = prof.apply(lambda r: _bcg(r, share_cut, growth_cut), axis=1)
    quad = (prof.groupby("bcg_class", as_index=False)
            .agg(products=("product_id", "count"), revenue=("revenue", "sum"),
                 profit=("gross_profit", "sum")))
    quad["rev_share"] = quad["revenue"] / quad["revenue"].sum()
    quad["profit_share"] = quad["profit"] / quad["profit"].sum()

    # ---- 图 1：帕累托 ----
    fig, ax1 = plt.subplots(figsize=(9.6, 3.6))
    ax1.bar(prof["rank"], prof["revenue"] / 1e4, color=base.PALETTE["blue"], alpha=.85)
    ax1.set_xlabel("商品按销售额排名")
    ax1.set_ylabel("销售额（万元）")
    ax1.set_title(f"ABC 帕累托：{a_cnt} 个商品（{a_cnt / len(prof) * 100:.0f}%）贡献约 80% 销售额",
                  pad=12)
    ax2 = ax1.twinx()
    ax2.plot(prof["rank"], prof["cum_share"] * 100, color=base.PALETTE["orange"], lw=2.2)
    ax2.axhline(80, color=base.PALETTE["gray"], linestyle="--", lw=1)
    ax2.axhline(95, color=base.PALETTE["gray"], linestyle=":", lw=1)
    ax2.set_ylabel("累计占比（%）")
    ax2.set_ylim(0, 105)
    ax2.grid(False)
    chart_pareto = base.save(fig, "05_abc_pareto")

    # ---- 图 2：波士顿矩阵气泡图 ----
    fig, ax = plt.subplots(figsize=(9.6, 5.4))
    for cls, color in QUADRANT_COLOR.items():
        part = prof[prof["bcg_class"] == cls]
        if part.empty:
            continue
        ax.scatter(part["rel_share"], part["yoy_growth"] * 100,
                   s=np.clip(part["revenue"] / 400, 20, 900), alpha=0.65,
                   color=color, label=f"{cls}（{len(part)} 个）", edgecolors="white", linewidths=1)
    ax.axvline(share_cut, color=base.PALETTE["gray"], linestyle="--", lw=1)
    ax.axhline(growth_cut * 100, color=base.PALETTE["gray"], linestyle="--", lw=1)
    # 标注销售额 TOP6
    for i, (_, r) in enumerate(prof.head(6).iterrows()):
        offset = (0, 16) if i % 2 == 0 else (0, -20)
        ax.annotate(r["product_name"][:12], (r["rel_share"], r["yoy_growth"] * 100),
                    textcoords="offset points", xytext=offset, ha="center", fontsize=8,
                    color=base.PALETTE["navy"],
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.8))
    ax.set_xlabel("品类内相对份额（本商品销售额 / 品类第一）")
    ax.set_ylabel("同比增长率（%）")
    ax.set_title(f"波士顿矩阵：份额 × 增长（虚线为各自中位数：份额 {share_cut:.2f} / "
                 f"增长 {growth_cut * 100:.1f}%）", pad=12)
    ax.legend(loc="lower right", fontsize=9)
    chart_bcg = base.save(fig, "06_bcg_matrix")

    # ---- 落库 ----
    out = pd.DataFrame({
        "product_id": prof["product_id"], "product_name": prof["product_name"],
        "product_type": prof["product_type"], "category": prof["category"],
        "sales_qty": prof["qty"].astype(int), "sales_amt": prof["revenue"].round(2),
        "gross_profit": prof["gross_profit"].round(2),
        "profit_rate": prof["profit_rate"].round(4),
        "qty_share": (prof["qty"] / prof["qty"].sum()).round(4),
        "growth_rate": prof["yoy_growth"].round(4),
        "abc_class": prof["abc_class"], "bcg_class": prof["bcg_class"],
    })
    out["calc_date"] = base.calc_date()
    base.write_frame("ads_product_performance", out)

    # ---- 报告 ----
    a_row = prof[prof["abc_class"] == "A"]
    blocks = [
        "## 一、核心结论\n",
        f"1. **{a_cnt} 个商品（占 {a_cnt / len(prof) * 100:.0f}%）贡献了 80% 的销售额**，"
        f"其中 TOP1「{prof.iloc[0]['product_name']}」单品就占 {prof.iloc[0]['revenue_share'] * 100:.1f}%。",
        f"2. 波士顿矩阵：明星 {int((prof['bcg_class'] == '明星').sum())} 个、"
        f"金牛 {int((prof['bcg_class'] == '金牛').sum())} 个、"
        f"问题 {int((prof['bcg_class'] == '问题').sum())} 个、"
        f"瘦狗 {int((prof['bcg_class'] == '瘦狗').sum())} 个。"
        f"金牛贡献 {float(quad[quad['bcg_class'] == '金牛']['profit_share'].sum()) * 100:.0f}% 的毛利，"
        f"是现金来源。",
        f"3. **{int((prof['yoy_growth'] < 0).sum())} 个商品负增长**，其中包含部分 A 类商品——"
        "这类「高份额、负增长」的组合正是需要警惕的衰退信号。",
        f"4. 花束（BUNDLE）贡献 {prof[prof['product_type'] == 'BUNDLE']['revenue'].sum() / prof['revenue'].sum() * 100:.1f}% 的销售额，"
        "但花束单价高、复购低；花材（SKU）单价低、复购高，是「高频带低频」的引流品。\n",
        "## 二、ABC 分类结果\n",
    ]
    blocks += base.md_table(
        ["类别", "商品数", "销售额占比", "毛利占比", "管理策略"],
        [["A 类（累计 0-80%）", a_cnt,
          f"{prof[prof['abc_class'] == 'A']['revenue'].sum() / prof['revenue'].sum() * 100:.1f}%",
          f"{prof[prof['abc_class'] == 'A']['gross_profit'].sum() / prof['gross_profit'].sum() * 100:.1f}%",
          "重点保障供应、优先陈列、控损耗"],
         ["B 类（80-95%）", b_cnt,
          f"{prof[prof['abc_class'] == 'B']['revenue'].sum() / prof['revenue'].sum() * 100:.1f}%",
          f"{prof[prof['abc_class'] == 'B']['gross_profit'].sum() / prof['gross_profit'].sum() * 100:.1f}%",
          "常规补货、组合搭售"],
         ["C 类（95%+）", c_cnt,
          f"{prof[prof['abc_class'] == 'C']['revenue'].sum() / prof['revenue'].sum() * 100:.1f}%",
          f"{prof[prof['abc_class'] == 'C']['gross_profit'].sum() / prof['gross_profit'].sum() * 100:.1f}%",
          "清库存、减少采购、评估下架"]],
        align="---")
    blocks += ["", f"![帕累托]({chart_pareto})", "",
               "## 三、波士顿矩阵\n", ]
    blocks += base.md_table(
        ["象限", "商品数", "销售额占比", "毛利占比", "策略"],
        [[r["bcg_class"], int(r["products"]), f"{r['rev_share'] * 100:.1f}%",
          f"{r['profit_share'] * 100:.1f}%",
          {"明星": "加大投入、保供应", "金牛": "维持、提毛利、控成本",
           "问题": "选择性投入、测试爆款", "瘦狗": "收缩、清库存或下架"}[r["bcg_class"]]]
         for _, r in quad.sort_values("revenue", ascending=False).iterrows()],
        align="---")
    blocks += ["", f"![波士顿矩阵]({chart_bcg})", "",
               f"> 读图：横轴是品类内相对份额（虚线为全品中位数 {share_cut:.2f}），"
               f"纵轴是同比增长率（虚线为中位数 {growth_cut * 100:.1f}%）。",
               "> 气泡越大销售额越高。理想位置是右上角（明星），"
               "最需要警惕的是「右下角」——高份额但负增长，说明正在被替代。",
               "",
               "## 四、TOP10 商品明细\n", ]
    blocks += base.md_table(
        ["排名", "商品", "类型", "品类", "销量", "销售额（元）", "毛利率", "同比", "ABC", "BCG"],
        [[int(r["rank"]), r["product_name"], r["product_type"], r["category"],
          int(r["qty"]), f"{r['revenue']:,.0f}", base.fmt_pct(r["profit_rate"]),
          base.fmt_pct(r["yoy_growth"]), r["abc_class"], r["bcg_class"]]
         for _, r in prof.head(10).iterrows()],
        align="---:")
    blocks += ["", "## 五、选品与定价建议\n",
               "1. **A 类 + 明星**：保障供应优先级最高，宁可多备 10% 也不能断货；",
               "2. **A 类 + 金牛**：是利润基本盘，不要轻易打折，用搭售提升客单；",
               "3. **问题类**：小批量测试 + 快速迭代，跑通再放量（避免一次性压货）；",
               "4. **瘦狗类**：优先清库存，SKU 数量从 56 个精简到 40 个左右，"
               "减少长尾采购与损耗。", ""]

    base.write_md(base.ANALYSIS_DIR / "04_商品结构与波士顿矩阵.md", "商品结构与波士顿矩阵", blocks)
    return {
        "products": int(len(prof)), "a_cnt": a_cnt, "b_cnt": b_cnt, "c_cnt": c_cnt,
        "top1": str(prof.iloc[0]["product_name"]),
        "top1_share": float(prof.iloc[0]["revenue_share"]),
        "quadrants": {r["bcg_class"]: int(r["products"]) for _, r in quad.iterrows()},
        "charts": [chart_pareto, chart_bcg],
    }
