"""关联规则：自己实现 Apriori，挖掘"买 A 的人也会买 B"。

为什么要自己写，而不是 pip install mlxtend？
-------------------------------------------
因为这个算法的每一步都能讲清楚，而且能演示对"支持度-置信度-提升度"的理解：

    support(A→B)    = P(A∩B)         同时出现的订单占比
    confidence(A→B) = P(B|A)         买 A 的人里有多少也买了 B
    lift(A→B)       = P(B|A) / P(B)  提升度 >1 才说明"A 真的带动了 B"

Apriori 的核心是"先验性质"：**一个项集频繁，它的所有子集必须频繁**。
所以可以逐层剪枝：先找出频繁 1 项集，再由它生成候选 2 项集……
候选数量指数级下降，这就是它比暴力枚举快的原因。

本项目做两个层级：
- **商品级**：具体商品之间的搭配（如 99 朵玫瑰 → 费列罗）；
- **品类级**：品类之间的搭配（如 爱情告白 → 礼盒），更适合指导陈列与套餐设计。
"""
from __future__ import annotations

import itertools
from collections import Counter
from typing import Any, Dict, Iterable, List, Set, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..logger import get_logger
from . import base

log = get_logger("analysis.assoc")

MIN_SUPPORT = 0.006      # 至少 0.6% 的订单同时包含
MIN_CONFIDENCE = 0.08    # 至少 8% 的条件概率
MIN_LIFT = 1.05          # 提升度显著大于 1


def _frequent_itemsets(transactions: List[Set[str]], min_support: float,
                       max_len: int = 2) -> Dict[frozenset, int]:
    """Apriori 主循环：逐层生成候选、计数、剪枝。"""
    n = len(transactions)
    min_count = max(1, int(np.ceil(min_support * n)))

    # 第 1 层
    counts: Counter = Counter()
    for t in transactions:
        counts.update(t)
    frequent: Dict[frozenset, int] = {
        frozenset([item]): c for item, c in counts.items() if c >= min_count
    }

    current = set(frequent.keys())
    k = 2
    while current and k <= max_len:
        # 由 k-1 频繁项集两两连接生成 k 候选（简化版，不做全部剪枝）
        candidates: Set[frozenset] = set()
        keys = sorted(current, key=lambda s: sorted(s))
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                union = keys[i] | keys[j]
                if len(union) == k:
                    candidates.add(union)
        if not candidates:
            break

        # 只统计"在交易里真实出现"的候选，避免 O(n × 候选数)
        cand_counts: Counter = Counter()
        for t in transactions:
            if len(t) < k:
                continue
            for combo in itertools.combinations(sorted(t), k):
                fs = frozenset(combo)
                if fs in candidates:
                    cand_counts[fs] += 1
        for itemset, c in cand_counts.items():
            if c >= min_count:
                frequent[itemset] = c
        current = {s for s in frequent if len(s) == k}
        k += 1

    return frequent


def _rules(frequent: Dict[frozenset, int], n: int) -> List[Dict[str, Any]]:
    """由频繁项集生成关联规则，并计算 support / confidence / lift。"""
    rules: List[Dict[str, Any]] = []
    for itemset, count in frequent.items():
        if len(itemset) != 2:
            continue
        a, b = list(itemset)
        for x, y in ((a, b), (b, a)):
            sup_x = frequent.get(frozenset([x]), 0)
            sup_y = frequent.get(frozenset([y]), 0)
            if sup_x == 0 or sup_y == 0:
                continue
            support = count / n
            confidence = count / sup_x
            lift = confidence / (sup_y / n)
            if confidence >= MIN_CONFIDENCE and lift >= MIN_LIFT:
                rules.append({
                    "antecedent": x, "consequent": y,
                    "support": round(support, 4),
                    "confidence": round(confidence, 4),
                    "lift": round(lift, 2),
                    "order_cnt": count,
                })
    return sorted(rules, key=lambda r: (-r["lift"], -r["support"]))


def _baskets(items: pd.DataFrame, level: str) -> List[Set[str]]:
    if level == "商品级":
        key = "product_name"
    else:
        key = "label"
    grouped = items.drop_duplicates(["order_id", key]).groupby("order_id")[key].apply(set)
    return [s for s in grouped.tolist() if len(s) >= 2]


def run() -> Dict[str, Any]:
    log.info("▸ 关联规则（Apriori）")
    items = base.read_df("""
        SELECT f.order_id, p.product_id, p.product_name, p.product_type, p.category
        FROM bloom_dw.fact_sales f
        JOIN bloom_dw.dim_product p ON p.product_sk = f.product_sk
        WHERE f.order_status <> '已取消'
    """)
    # 品类级标签：花束用花束品类，散卖用「加购-品类」，让规则更可读
    def _label(row) -> str:
        if row["product_type"] == "BUNDLE":
            return row["category"]
        return f"加购-{row['category']}"

    items["label"] = items.apply(_label, axis=1)

    results = {}
    all_rules = []
    for level in ("商品级", "品类级"):
        baskets = _baskets(items, level)
        freq = _frequent_itemsets(baskets, MIN_SUPPORT, max_len=2)
        rules = _rules(freq, len(baskets))
        results[level] = {"baskets": len(baskets), "itemsets": len(freq), "rules": rules}
        for r in rules[:30]:
            all_rules.append({
                "rule_level": level, "antecedent": r["antecedent"],
                "consequent": r["consequent"], "support": r["support"],
                "confidence": r["confidence"], "lift": r["lift"],
                "order_cnt": r["order_cnt"],
            })
        log.info("  %s：篮子 %d，频繁项集 %d，规则 %d",
                 level, len(baskets), len(freq), len(rules))

    # ---- 图：品类级 TOP 规则 ----
    top = results["品类级"]["rules"][:12]
    fig, ax = plt.subplots(figsize=(9.4, 4.0))
    if top:
        labels = [f"{r['antecedent']} → {r['consequent']}" for r in top][::-1]
        lifts = [r["lift"] for r in top][::-1]
        confs = [r["confidence"] for r in top][::-1]
        bars = ax.barh(labels, lifts, color=base.PALETTE["purple"], alpha=.85)
        for b, c in zip(bars, confs):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2, f"  {b.get_width():.2f}× "
                    f"(置信度 {c * 100:.0f}%)", va="center", fontsize=8,
                    color=base.PALETTE["navy"])
        ax.axvline(1, color=base.PALETTE["gray"], linestyle="--", lw=1)
        ax.set_xlabel("提升度 Lift（>1 表示正向关联）")
        ax.set_title("品类级关联规则 TOP12", pad=12)
        ax.set_xlim(0, max(lifts) * 1.35)
    chart = base.save(fig, "12_association_rules")

    # ---- 落库 ----
    if all_rules:
        df = pd.DataFrame(all_rules)
        df["calc_date"] = base.calc_date()
        base.write_frame("ads_assoc_rules", df)

    # ---- 报告 ----
    cat_rules = results["品类级"]["rules"]
    prod_rules = results["商品级"]["rules"]
    blocks = [
        "## 一、核心结论\n",
        f"1. 共分析 {results['商品级']['baskets']:,} 个「多件订单」篮子"
        f"（只统计 ≥2 件商品的订单，单件订单无法产生关联）。",
        f"2. 挖掘出品类级规则 {len(cat_rules)} 条、商品级规则 {len(prod_rules)} 条"
        f"（支持度 ≥{MIN_SUPPORT:.1%}、置信度 ≥{MIN_CONFIDENCE:.0%}、提升度 ≥{MIN_LIFT}）。",
    ]
    if cat_rules:
        best = cat_rules[0]
        blocks.append(
            f"3. 最强关联：**「{best['antecedent']}」→「{best['consequent']}」**，"
            f"买前者的人有 {best['confidence'] * 100:.1f}% 也买了后者，"
            f"提升度 {best['lift']:.2f}（是随机情况的 {best['lift']:.2f} 倍）。")
    if prod_rules:
        bp = prod_rules[0]
        blocks.append(
            f"4. 商品层面最强搭配：**「{bp['antecedent']}」→「{bp['consequent']}」**，"
            f"提升度 {bp['lift']:.2f}，可作为套餐组合直接上线。")
    blocks += ["", "## 二、算法是怎么跑出来的\n",
               "```",
               "Apriori：频繁项集的子集必然频繁 → 逐层剪枝",
               "  1. 扫描全部订单，统计单项出现次数 → 保留 ≥ min_support 的 1 项集",
               "  2. 由频繁 1 项集两两组合成候选 2 项集",
               "  3. 只统计「在订单里真实出现」的候选（而不是拿候选去扫全表）",
               "  4. 计算 support / confidence / lift，过滤掉 lift ≤ 1 的规则",
               "```",
               "",
               f"本次参数：min_support = {MIN_SUPPORT:.2%}，"
               f"min_confidence = {MIN_CONFIDENCE:.0%}，max_len = 2。",
               "",
               "## 三、品类级规则（可直接指导陈列/套餐）\n",
               ]
    blocks += base.md_table(
        ["前项（买这个）", "后项（也买）", "共现订单", "支持度", "置信度", "提升度"],
        [[r["antecedent"], r["consequent"], f"{r['order_cnt']:,}",
          f"{r['support'] * 100:.2f}%", f"{r['confidence'] * 100:.1f}%", f"{r['lift']:.2f}×"]
         for r in cat_rules[:15]],
        align="---:")
    blocks += ["", f"![关联规则]({chart})", "",
               "> 读图：条形越长表示关联越强（提升度越高）。",
               "> 提升度 2.0 意味着「买 A 的人买 B 的概率，是普通顾客的 2 倍」。",
               "",
               "## 四、商品级规则 TOP10\n", ]
    blocks += base.md_table(
        ["前项", "后项", "共现订单", "支持度", "置信度", "提升度"],
        [[r["antecedent"], r["consequent"], f"{r['order_cnt']:,}",
          f"{r['support'] * 100:.2f}%", f"{r['confidence'] * 100:.1f}%", f"{r['lift']:.2f}×"]
         for r in prod_rules[:10]],
        align="---:")
    blocks += ["",
               "## 五、怎么用这些规则\n",
               "1. **套餐化**：把高提升度的组合做成「固定套餐」，比单买便宜 5%，"
               "既提客单又降拣货复杂度；",
               "2. **加购推荐**：在花束详情页推荐关联商品（如巧克力/贺卡），"
               "这是最自然的加购位；",
               "3. **陈列联动**：线下把关联商品摆在一起（花束区旁边放礼盒），"
               "减少客户寻找成本；",
               "4. **注意边界**：关联规则只说明「相关」，不说明「因果」——"
               "客户买巧克力可能只是因为都要送人，不是因为花「带动」了巧克力。"
               "验证因果要靠 A/B 实验。", ""]

    base.write_md(base.ANALYSIS_DIR / "08_关联规则分析.md", "关联规则分析（Apriori）", blocks)
    return {
        "baskets": results["商品级"]["baskets"],
        "cat_rules": len(cat_rules), "prod_rules": len(prod_rules),
        "top_rule": (f"{cat_rules[0]['antecedent']} → {cat_rules[0]['consequent']}"
                     if cat_rules else ""),
        "top_lift": cat_rules[0]["lift"] if cat_rules else 0,
        "charts": [chart],
    }
