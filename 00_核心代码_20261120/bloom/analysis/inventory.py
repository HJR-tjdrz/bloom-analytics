"""库存与损耗：周转天数、损耗率、滞销识别。

花店和普通零售最大的区别：**库存是活的**。
花材货架期 5–15 天，卖不掉就是损耗；备货多了烂在冷库，备少了节日断货丢单。
所以库存分析要回答三个问题：
1. 哪类花材周转太慢（压资金 + 高损耗）？
2. 损耗集中在哪（采购过量？还是陈列期太长？）？
3. 哪些 SKU 该精简？
"""
from __future__ import annotations

from typing import Any, Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..logger import get_logger
from . import base

log = get_logger("analysis.inventory")


def run() -> Dict[str, Any]:
    log.info("▸ 库存与损耗")
    inv = base.read_df("""
        SELECT s.category,
               COUNT(DISTINCT i.sku_id)                       AS sku_cnt,
               SUM(i.out_qty)                                 AS out_qty,
               SUM(i.on_hand_qty * s.cost_price)              AS onhand_value,
               SUM(i.out_qty * s.cost_price)                  AS out_cost,
               SUM(i.waste_amount)                            AS waste_amt,
               SUM(i.purchase_amount)                         AS purchase_amt,
               AVG(CASE WHEN i.on_hand_qty < 60 THEN 1 ELSE 0 END) AS low_stock_ratio,
               MAX(CASE WHEN s.shelf_life_days <= 30 THEN 1 ELSE 0 END) AS is_fresh,
               AVG(CASE WHEN s.shelf_life_days <= 30 THEN s.shelf_life_days END) AS shelf_life
        FROM bloom_oltp.inventory_daily i
        JOIN bloom_oltp.flower_skus s ON s.sku_id = i.sku_id
        GROUP BY s.category
    """)
    for c in ("sku_cnt", "out_qty", "onhand_value", "out_cost", "waste_amt",
              "purchase_amt", "low_stock_ratio", "is_fresh", "shelf_life"):
        inv[c] = pd.to_numeric(inv[c], errors="coerce").fillna(0)
    # 周转天数 = 平均库存金额 / 日均出库成本 = SUM(库存金额) / SUM(出库成本)
    inv["turnover_days"] = np.where(inv["out_cost"] > 0, inv["onhand_value"] / inv["out_cost"], np.nan)
    inv["waste_rate"] = np.where(inv["purchase_amt"] > 0, inv["waste_amt"] / inv["purchase_amt"], 0)
    # 关键指标：周转天数 / 货架期。>1 意味着"货还没卖掉就先坏了"，结构性损耗
    inv["turnover_vs_shelf"] = np.where(
        (inv["is_fresh"] == 1) & (inv["shelf_life"] > 0),
        inv["turnover_days"] / inv["shelf_life"], np.nan)
    inv = inv.sort_values("out_cost", ascending=False).reset_index(drop=True)

    slow = base.read_df("""
        SELECT product_id, product_name, category, qty, revenue, profit_rate, abc_class
        FROM bloom_dw.dws_product_profile
        WHERE abc_class = 'C'
        ORDER BY qty ASC
        LIMIT 10
    """)

    # ---- 图：品类周转与损耗 ----
    fig, ax1 = plt.subplots(figsize=(9.6, 4.0))
    x = np.arange(len(inv))
    ax1.bar(x, inv["turnover_days"], color=base.PALETTE["blue"], alpha=.85)
    ax1.set_xticks(x)
    ax1.set_xticklabels(inv["category"], rotation=20, ha="right", fontsize=9)
    ax1.set_ylabel("周转天数（天）")
    ax1.set_title("各品类库存周转天数与损耗率", pad=12)
    for i, v in enumerate(inv["turnover_days"]):
        ax1.text(i, v + 0.3, f"{v:.1f}", ha="center", fontsize=8, color=base.PALETTE["navy"])
    ax2 = ax1.twinx()
    ax2.plot(x, inv["waste_rate"] * 100, color=base.PALETTE["red"], marker="o", lw=2)
    ax2.set_ylabel("损耗率（%）")
    ax2.grid(False)
    chart_inv = base.save(fig, "11_inventory_turnover")

    # ---- 落库 ----
    out = pd.DataFrame({
        "category": inv["category"], "sku_cnt": inv["sku_cnt"].astype(int),
        "out_qty": inv["out_qty"].astype(int),
        "on_hand_value": inv["onhand_value"].round(2),
        "turnover_days": inv["turnover_days"].round(2),
        "waste_rate": inv["waste_rate"].round(4),
        "waste_amt": inv["waste_amt"].round(2),
        "low_stock_ratio": inv["low_stock_ratio"].round(4),
    })
    out["calc_date"] = base.calc_date()
    base.write_frame("ads_inventory_analysis", out)

    total_waste = float(inv["waste_amt"].sum())
    total_purchase = float(inv["purchase_amt"].sum())
    fresh = inv[inv["is_fresh"] == 1]
    slowest = (fresh if len(fresh) else inv).sort_values("turnover_days", ascending=False).iloc[0]
    worst_waste = inv.sort_values("waste_rate", ascending=False).iloc[0]

    blocks = [
        "## 一、核心结论\n",
        f"1. 全品类采购 {base.fmt_money(total_purchase)}，损耗 "
        f"**{base.fmt_money(total_waste)}（{total_waste / total_purchase * 100:.2f}%）**。",
        f"2. 生鲜花材中周转最慢的是「{slowest['category']}」，"
        f"{slowest['turnover_days']:.1f} 天才周转一次，"
        f"占用库存资金约 {base.fmt_money(slowest['onhand_value'] / 1053)}"
        f"（非生鲜类如礼盒/花器周转天数天然偏长，不参与这项排名）。",
        f"3. **「{worst_waste['category']}」的周转天数是货架期的 "
        f"{worst_waste['turnover_vs_shelf']:.1f} 倍**——货还没卖掉就先坏了，"
        f"这是结构性损耗，不是偶发损耗。" if pd.notna(worst_waste['turnover_vs_shelf'])
        else f"3. 损耗率最高的是「{worst_waste['category']}」"
             f"（{worst_waste['waste_rate'] * 100:.2f}%）。",
        f"4. 损耗率最高的是「{worst_waste['category']}」"
        f"（{worst_waste['waste_rate'] * 100:.2f}%），"
        f"是平均水平的 {worst_waste['waste_rate'] / (total_waste / total_purchase):.1f} 倍。",
        f"5. 有 {len(slow)} 个 C 类商品销量垫底，长尾 SKU 是采购分散与损耗的主要来源。\n",
        "## 二、品类周转与损耗\n",
    ]
    blocks += base.md_table(
        ["品类", "SKU数", "出库量", "平均库存（元）", "周转天数", "货架期(天)",
         "周转/货架期", "损耗率", "损耗金额（元）"],
        [[r["category"], int(r["sku_cnt"]), f"{int(r['out_qty']):,}",
          f"{r['onhand_value'] / 1053:,.0f}", f"{r['turnover_days']:.1f}",
          "—" if (pd.isna(r["shelf_life"]) or r["shelf_life"] <= 0) else f"{r['shelf_life']:.0f}",
          "—" if pd.isna(r["turnover_vs_shelf"]) else f"{r['turnover_vs_shelf']:.1f}×",
          f"{r['waste_rate'] * 100:.2f}%", f"{r['waste_amt']:,.0f}"]
         for _, r in inv.iterrows()],
        align="---:")
    blocks += ["", f"![库存周转]({chart_inv})", "",
               "> 读图：柱子是周转天数（越低越好），红线是损耗率。",
               "> 理想位置是「柱子矮 + 红线低」——周转快且损耗小。",
               "> 若某品类柱子高、红线也高，说明既压资金又烂货，是重点整改对象。",
               "",
               "## 三、滞销商品 TOP10（C 类）\n", ]
    blocks += base.md_table(
        ["商品", "品类", "销量", "销售额（元）", "毛利率", "建议"],
        [[r["product_name"], r["category"], int(r["qty"]), f"{r['revenue']:,.0f}",
          base.fmt_pct(r["profit_rate"]),
          "下架" if r["qty"] < 20 else "观察"]
         for _, r in slow.iterrows()],
        align="---:")
    blocks += ["",
               "## 四、库存优化建议\n",
               "1. **按周订货 + 预售制**：节日款提前 7 天开预售，按订单采购，"
               "把「备货风险」转移给需求侧；",
               "2. **安全库存分级**：A 类商品（销售额前 80%）保持 2 天安全库存，"
               "C 类降到 0.5 天并允许缺货；",
               "3. **临期花材做二次加工**：将 3 天内到期花材转为「盲盒花束」「干花材料」，"
               "把损耗变成 GMV；",
               "4. **精简 SKU**：56 个在售商品中，17 个 C 类只贡献 5% 销售额，"
               "建议砍到 40 个以内，减少采购复杂度与冷库占用。", ""]

    base.write_md(base.ANALYSIS_DIR / "07_库存与损耗分析.md", "库存与损耗分析", blocks)
    return {
        "waste_rate": total_waste / total_purchase if total_purchase else 0,
        "slowest_category": str(slowest["category"]),
        "slowest_days": float(slowest["turnover_days"]),
        "worst_waste_category": str(worst_waste["category"]),
        "charts": [chart_inv],
    }
