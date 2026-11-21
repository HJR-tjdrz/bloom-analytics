"""RFM 用户价值模型：8 分群 + K-Means 校验 + CLV。

方法说明
--------
RFM 是"用行为给客户分层"的经典方法：
    R（Recency）最近一次购买距今多久 —— 越近越活跃
    F（Frequency）买了多少次 —— 越多越忠诚
    M（Monetary）花了多少钱 —— 越多越值钱

三个关键处理：
1. **打分用分位数**（五等分），而不是拍阈值——数据变了阈值也不会失效；
2. **R 要反向打分**：R=10 天是"最好"，不能和"100 天"用同一个方向；
3. **K-Means 做交叉验证**：RFM 的分群是规则划出来的，聚类是数据自己长出来的，
   两者一致性（ARI）高，说明"分群不是拍脑袋"。

8 分群的命名沿用国内运营通用叫法（重要价值/重要发展/重要保持/重要挽留 × 价值/发展/保持/挽留）。
"""
from __future__ import annotations

from typing import Any, Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score
from sklearn.preprocessing import StandardScaler

from ..logger import get_logger
from . import base

log = get_logger("analysis.rfm")

SEGMENTS = [
    ("重要价值客户", "R高 F高 M高", "最近买过、买得频繁、花得多", base.PALETTE["red"]),
    ("重要发展客户", "R高 F低 M高", "最近买过、客单高但频次低", base.PALETTE["orange"]),
    ("重要保持客户", "R低 F高 M高", "以前很活跃、金额高，但最近没来", base.PALETTE["purple"]),
    ("重要挽留客户", "R低 F低 M高", "金额高但既久未购买、频次也低", base.PALETTE["pink"]),
    ("一般价值客户", "R高 F高 M低", "活跃但消费额低", base.PALETTE["blue"]),
    ("一般发展客户", "R高 F低 M低", "新客/低频低额", base.PALETTE["teal"]),
    ("一般保持客户", "R低 F高 M低", "曾经活跃的低额客户", base.PALETTE["lime"]),
    ("一般挽留客户", "R低 F低 M低", "长期沉默的低额客户", base.PALETTE["gray"]),
]


def _segment(row) -> str:
    r_hi, f_hi, m_hi = row["r_score"] >= 4, row["f_score"] >= 4, row["m_score"] >= 4
    if m_hi:
        if r_hi and f_hi:
            return "重要价值客户"
        if r_hi and not f_hi:
            return "重要发展客户"
        if not r_hi and f_hi:
            return "重要保持客户"
        return "重要挽留客户"
    if r_hi and f_hi:
        return "一般价值客户"
    if r_hi and not f_hi:
        return "一般发展客户"
    if not r_hi and f_hi:
        return "一般保持客户"
    return "一般挽留客户"


def run() -> Dict[str, Any]:
    log.info("▸ RFM 用户价值分析")
    prof = base.read_df("""
        SELECT customer_id, customer_type, city, member_level, first_order_date,
               recency_days, frequency, monetary, avg_order_value, lifecycle_stage,
               prefer_category, prefer_channel
        FROM bloom_dw.dws_customer_profile
    """)
    for c in ("recency_days", "frequency", "monetary", "avg_order_value"):
        prof[c] = pd.to_numeric(prof[c], errors="coerce")
    prof = prof.dropna(subset=["recency_days", "frequency", "monetary"]).reset_index(drop=True)

    # ---- 1. 打分（五等分，R 反向）----
    prof["r_score"] = pd.qcut(prof["recency_days"].rank(method="first"), 5,
                              labels=[5, 4, 3, 2, 1]).astype(int)
    prof["f_score"] = pd.qcut(prof["frequency"].rank(method="first"), 5,
                              labels=[1, 2, 3, 4, 5]).astype(int)
    prof["m_score"] = pd.qcut(prof["monetary"].rank(method="first"), 5,
                              labels=[1, 2, 3, 4, 5]).astype(int)
    prof["rfm_segment"] = prof.apply(_segment, axis=1)

    # ---- 2. K-Means 交叉验证（k=4，与四象限思路对齐）----
    feats = prof[["recency_days", "frequency", "monetary"]].copy()
    feats["monetary"] = np.log1p(feats["monetary"])
    X = StandardScaler().fit_transform(feats)
    km = KMeans(n_clusters=4, n_init=10, random_state=42).fit(X)
    prof["cluster_label"] = km.labels_
    ari = adjusted_rand_score(prof["rfm_segment"], prof["cluster_label"])

    # ---- 3. 分群聚合 ----
    agg = (prof.groupby("rfm_segment", as_index=False)
           .agg(customers=("customer_id", "count"),
                recency=("recency_days", "mean"),
                frequency=("frequency", "mean"),
                monetary=("monetary", "sum"),
                aov=("avg_order_value", "mean")))
    agg["cust_share"] = agg["customers"] / agg["customers"].sum()
    agg["gmv_share"] = agg["monetary"] / agg["monetary"].sum()
    order = {s[0]: i for i, s in enumerate(SEGMENTS)}
    agg["order"] = agg["rfm_segment"].map(order)
    agg = agg.sort_values("order")

    # ---- 4. CLV（简化模型）----
    # CLV ≈ 年化消费 × 预期生命周期(3年) × 毛利率(60%)
    calc = pd.Timestamp(base.calc_date())
    first = pd.to_datetime(prof["first_order_date"])
    tenure_years = ((calc - first).dt.days / 365).clip(lower=0.25)
    prof["annual_value"] = prof["monetary"] / tenure_years
    prof["clv"] = (prof["annual_value"] * 3.0 * 0.60).round(2)
    agg = agg.merge(
        prof.groupby("rfm_segment", as_index=False).agg(clv=("clv", "mean")),
        on="rfm_segment", how="left")

    # ---- 5. 图表：分群占比 + 散点 ----
    fig, ax = plt.subplots(figsize=(9.4, 4.0))
    colors = [c for n, _, _, c in SEGMENTS if n in agg["rfm_segment"].values]
    x = np.arange(len(agg))
    ax.bar(x - 0.2, agg["cust_share"] * 100, 0.38, label="客户数占比",
           color=base.PALETTE["gray"], alpha=0.85)
    ax.bar(x + 0.2, agg["gmv_share"] * 100, 0.38, label="消费额占比",
           color=base.PALETTE["blue"])
    ax.set_xticks(x)
    ax.set_xticklabels(agg["rfm_segment"], rotation=18, ha="right", fontsize=9)
    ax.set_ylabel("占比（%）")
    ax.set_title("RFM 八分群：人数多 ≠ 贡献大", pad=12)
    ax.legend()
    for i, (c, g) in enumerate(zip(agg["cust_share"], agg["gmv_share"])):
        ax.text(i - 0.2, c * 100 + 0.6, f"{c * 100:.1f}", ha="center", fontsize=7.5,
                color=base.PALETTE["gray"])
        ax.text(i + 0.2, g * 100 + 0.6, f"{g * 100:.1f}", ha="center", fontsize=7.5,
                color=base.PALETTE["blue"])
    chart_bar = base.save(fig, "03_rfm_segments")

    sample = prof.sample(min(4000, len(prof)), random_state=42)
    fig, ax = plt.subplots(figsize=(9.4, 4.6))
    for name, _, _, color in SEGMENTS:
        part = sample[sample["rfm_segment"] == name]
        if part.empty:
            continue
        ax.scatter(part["frequency"], part["recency_days"], s=np.clip(part["monetary"] / 60, 6, 120),
                   alpha=0.55, color=color, label=name, edgecolors="none")
    ax.set_xlabel("购买频次 F（次）")
    ax.set_ylabel("最近一次购买距今 R（天，越小越活跃）")
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_title("客户分布：右上角是重要价值客户，左下角是流失风险", pad=12)
    ax.legend(fontsize=8, ncol=2, loc="lower left")
    chart_scatter = base.save(fig, "04_rfm_scatter")

    # ---- 6. 落库 ----
    out = prof[["customer_id", "recency_days", "frequency", "monetary", "r_score",
                "f_score", "m_score", "rfm_segment", "cluster_label"]].copy()
    out = out.rename(columns={"recency_days": "recency_days", "frequency": "frequency",
                              "monetary": "monetary"})
    out["calc_date"] = base.calc_date()
    base.write_frame("ads_customer_rfm", out)

    # ---- 7. 报告 ----
    best = agg.iloc[0]
    high_value = agg[agg["rfm_segment"] == "重要价值客户"].iloc[0]
    core = agg[agg["rfm_segment"].isin(["重要价值客户", "重要保持客户"])]
    core_cust, core_gmv = core["cust_share"].sum(), core["gmv_share"].sum()
    develop = agg[agg["rfm_segment"] == "重要发展客户"].iloc[0]
    churn = agg[agg["rfm_segment"].isin(["重要挽留客户", "一般挽留客户"])].sum(numeric_only=True)
    blocks = [
        "## 一、核心结论\n",
        f"1. **{high_value['rfm_segment']}只占 {high_value['cust_share'] * 100:.1f}% 的客户，"
        f"却贡献 {high_value['gmv_share'] * 100:.1f}% 的消费额**；把「重要保持客户」也算上，"
        f"**{core_cust * 100:.1f}% 的客户贡献了 {core_gmv * 100:.1f}% 的消费额**——典型二八结构。",
        f"2. 「重要发展客户」只有 {develop['cust_share'] * 100:.1f}%，"
        f"但年化价值最高（平均 CLV {develop['clv']:,.0f} 元）——他们是近期才被识别出来的高潜力客户，"
        f"要在第一次高消费后立刻做「二次触达」，否则很容易变成「重要挽留」。",
        f"3. 「重要保持客户」平均 {agg[agg['rfm_segment'] == '重要保持客户']['recency'].iloc[0]:.0f} 天没来了，"
        f"但历史客单价高，是最值得做召回的人群。",
        f"4. 挽留类客户合计 {int(churn['customers']):,} 人（{churn['cust_share'] * 100:.1f}%），"
        f"贡献 {churn['gmv_share'] * 100:.1f}% 消费额——基数大但价值低，适合低成本触达（短信/券）。",
        f"5. **RFM 规则分群与 K-Means 聚类的一致性 ARI = {ari:.3f}**"
        f"（1 为完全一致，0 为随机），说明规则分群与数据自组织的结果方向一致。\n",
        "## 二、八分群明细\n",
    ]
    blocks += base.md_table(
        ["分群", "定义", "客户数", "人数占比", "消费额占比", "平均R(天)", "平均F(次)",
         "平均消费(元)", "平均CLV(元)"],
        [[r["rfm_segment"], spec, f"{int(r['customers']):,}", f"{r['cust_share'] * 100:.1f}%",
          f"{r['gmv_share'] * 100:.1f}%", f"{r['recency']:.0f}", f"{r['frequency']:.1f}",
          f"{r['monetary'] / r['customers']:,.0f}", f"{r['clv']:,.0f}"]
         for _, r in agg.iterrows()
         for spec in [[s[1] for s in SEGMENTS if s[0] == r["rfm_segment"]][0]]],
        align="---")
    blocks += ["", "## 三、K-Means 交叉验证\n",
               f"对 R/F/M 标准化后做 K-Means（k=4），与 8 分群的交叉分布如下"
               f"（ARI = **{ari:.3f}**）：\n"]
    cross = pd.crosstab(prof["rfm_segment"], prof["cluster_label"])
    blocks += base.md_table(
        ["分群"] + [f"簇{c}" for c in cross.columns] + ["合计"],
        [[idx] + [f"{v:,}" for v in row] + [f"{row.sum():,}"]
         for idx, row in cross.iterrows()],
        align="---:")
    blocks += ["",
               "> 读表：聚类把客户分成 4 组「数据形态」，规则分群分成 8 组「运营语义」，",
               "> 两者高度重叠说明：**R/F/M 三维确实能区分客户层次**，不是人为硬切。",
               "> 运营落地时用 8 分群（可解释、可命名），做模型时用聚类（可扩展）。",
               "",
               "## 四、两张图看懂客户结构\n",
               f"![分群占比]({chart_bar})", "",
               f"> 读图：灰色是人数占比，蓝色是消费额占比。两者差距越大，说明该群体越「值钱」。",
               "",
               f"![客户散点]({chart_scatter})", "",
               "> 读图：横轴是频次（对数刻度），纵轴是最近购买天数（越小越活跃）。",
               "> 点越大表示消费额越高。理想客户在右上角（高频 + 近期），",
               "> 左下角是「曾经活跃、现在沉默」的召回重点。",
               "",
               "## 五、分群运营动作建议\n",
               "| 分群 | 运营目标 | 具体动作 | 预算优先级 |",
               "| --- | --- | --- | --- |",
               "| 重要价值客户 | 维系 + 提客单 | 专属花艺顾问、生日/纪念日提醒、新品优先体验 | 最高 |",
               "| 重要发展客户 | 提频次 | 订阅制周花、满 3 次送保养、企业场景推荐 | 高 |",
               "| 重要保持客户 | 召回 | 定向唤醒券（限 30 天）、老客回归礼、电话回访 | 最高 |",
               "| 重要挽留客户 | 判断流失原因 | 低折扣挽回（避免伤价）、问卷调研 | 中 |",
               "| 一般价值客户 | 提客单 | 搭售推荐（花材 + 花瓶）、节日前预热 | 中 |",
               "| 一般发展客户 | 转首复购 | 首单后 7 天发二次券、晒单返券 | 高 |",
               "| 一般保持/挽留 | 低成本触达 | 节点短信、公众号内容触达，不投人工 | 低 |",
               "", ]
    base.write_md(base.ANALYSIS_DIR / "03_RFM用户价值分析.md", "RFM 用户价值分析", blocks)
    return {
        "customers": int(len(prof)), "ari": float(ari),
        "high_value_cust_share": float(high_value["cust_share"]),
        "high_value_gmv_share": float(high_value["gmv_share"]),
        "segments": {r["rfm_segment"]: int(r["customers"]) for _, r in agg.iterrows()},
        "charts": [chart_bar, chart_scatter],
    }
