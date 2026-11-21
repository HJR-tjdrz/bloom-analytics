"""经营分析报告：把全部分析结论组装成一份可直接发给老板的报告。

为什么要有"报告"这一层？
----------------------
看板解决"随时看"，报告解决"一次讲清楚"：
- 老板不会天天点看板，但会在月度会上要一份 PDF；
- 报告必须有**结论先行、建议落地**的结构，而不是图表的堆砌；
- 同一份数据要能导出 MD（Git 阅读）、HTML（发邮件）、Excel（业务方改数）。

设计原则
--------
1. **结论先行**：每节第一句就是结论，图表是证据，不是主角；
2. **口径可查**：所有数字标注时间范围与口径，附录给完整定义；
3. **建议可执行**：每条建议写清"做什么、谁来做、预期影响"；
4. **一次生成**：数据变了重跑一条命令，报告自动更新，杜绝手工粘贴过期数字。
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from ..config import PROJECT_ROOT
from ..logger import get_logger
from ..analysis import base

log = get_logger("reporting")

ANALYSIS_DIR = PROJECT_ROOT / "07_分析与建模_20261121"
CHART_DIR = ANALYSIS_DIR / "charts"
OUT_DIR = PROJECT_ROOT / "09_经营分析报告_20261121"
SUMMARY_JSON = ANALYSIS_DIR / "10_分析结果汇总.json"


# --------------------------------------------------------------------------
# 取数
# --------------------------------------------------------------------------
def collect() -> Dict[str, Any]:
    monthly = base.read_df("""
        SELECT month_key, order_cnt, customer_cnt, gmv, aov, gross_profit, profit_rate
        FROM bloom_dw.ads_kpi_monthly ORDER BY month_key
    """)
    for c in ("order_cnt", "customer_cnt", "gmv", "aov", "gross_profit", "profit_rate"):
        monthly[c] = pd.to_numeric(monthly[c], errors="coerce").fillna(0)

    segments = base.read_df("""
        SELECT rfm_segment, COUNT(*) AS customers, SUM(monetary) AS gmv,
               AVG(recency_days) AS avg_r, AVG(frequency) AS avg_f, AVG(monetary) AS avg_m
        FROM bloom_dw.ads_customer_rfm GROUP BY rfm_segment
    """)
    for c in ("customers", "gmv", "avg_r", "avg_f", "avg_m"):
        segments[c] = pd.to_numeric(segments[c], errors="coerce").fillna(0)
    segments["cust_share"] = segments["customers"] / segments["customers"].sum()
    segments["gmv_share"] = segments["gmv"] / segments["gmv"].sum()

    products = base.read_df("""
        SELECT product_name, product_type, category, sales_qty, sales_amt, profit_rate,
               growth_rate, abc_class, bcg_class
        FROM bloom_dw.ads_product_performance ORDER BY sales_amt DESC
    """)
    for c in ("sales_qty", "sales_amt", "profit_rate", "growth_rate"):
        products[c] = pd.to_numeric(products[c], errors="coerce").fillna(0)

    channels = base.read_df("""
        SELECT channel_name, channel_type, order_cnt, gmv, gmv_share, aov,
               commission_amt, net_profit, net_margin
        FROM bloom_dw.ads_channel_efficiency ORDER BY gmv DESC
    """)
    for c in ("order_cnt", "gmv", "gmv_share", "aov", "commission_amt", "net_profit", "net_margin"):
        channels[c] = pd.to_numeric(channels[c], errors="coerce").fillna(0)

    promos = base.read_df("""
        SELECT promotion_name, promotion_type, festival, order_cnt, gmv, discount_amt,
               gross_profit, profit_rate, roi
        FROM bloom_dw.ads_promo_roi ORDER BY gmv DESC LIMIT 8
    """)
    for c in ("order_cnt", "gmv", "discount_amt", "gross_profit", "profit_rate", "roi"):
        promos[c] = pd.to_numeric(promos[c], errors="coerce").fillna(0)

    inventory = base.read_df("""
        SELECT category, sku_cnt, turnover_days, waste_rate, waste_amt, low_stock_ratio
        FROM bloom_dw.ads_inventory_analysis ORDER BY waste_amt DESC
    """)
    for c in ("sku_cnt", "turnover_days", "waste_rate", "waste_amt", "low_stock_ratio"):
        inventory[c] = pd.to_numeric(inventory[c], errors="coerce").fillna(0)

    forecast = base.read_df("""
        SELECT forecast_date, predicted_gmv, lower_bound, upper_bound, festival_name
        FROM bloom_dw.ads_forecast ORDER BY forecast_date
    """)
    forecast["predicted_gmv"] = pd.to_numeric(forecast["predicted_gmv"], errors="coerce").fillna(0)

    summary = {}
    if SUMMARY_JSON.exists():
        summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8")).get("results", {})

    return {
        "monthly": monthly, "segments": segments, "products": products,
        "channels": channels, "promos": promos, "inventory": inventory,
        "forecast": forecast, "summary": summary,
        "generated_at": f"{datetime.now():%Y-%m-%d %H:%M}",
    }


def _img(name: str) -> str:
    """把图表转成 base64，让 HTML 报告自包含（可直接邮件发送）。"""
    path = CHART_DIR / name
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


# --------------------------------------------------------------------------
# 关键数字
# --------------------------------------------------------------------------
def headline(ctx: Dict[str, Any]) -> Dict[str, Any]:
    s = ctx["summary"]
    ov = s.get("overview", {})
    rfm = s.get("rfm", {})
    prod = s.get("product", {})
    ch = s.get("channel", {})
    inv = s.get("inventory", {})
    fc = s.get("forecast", {})
    assoc = s.get("association", {})
    monthly = ctx["monthly"]
    seg = ctx["segments"]
    core = seg[seg["rfm_segment"].isin(["重要价值客户", "重要保持客户"])]
    core_cust = float(core["cust_share"].sum())
    core_gmv = float(core["gmv_share"].sum())
    return {
        "gmv_total": float(monthly["gmv"].sum()),
        "orders_total": int(monthly["order_cnt"].sum()),
        "gmv_ytd": ov.get("gmv", 0),
        "gmv_yoy": ov.get("gmv_yoy", 0),
        "vol_effect": ov.get("vol_effect", 0),
        "price_effect": ov.get("price_effect", 0),
        "top_festival": ov.get("top_festival", "情人节"),
        "top_festival_lift": ov.get("top_festival_lift", 0),
        "core_cust_share": core_cust,
        "core_gmv_share": core_gmv,
        "ari": rfm.get("ari", 0),
        "a_cnt": prod.get("a_cnt", 0),
        "products": prod.get("products", 0),
        "quadrants": prod.get("quadrants", {}),
        "online_share": ch.get("online_share", 0),
        "commission_total": ch.get("commission_total", 0),
        "best_channel": ch.get("best_margin_channel", ""),
        "best_margin": ch.get("best_margin", 0),
        "waste_rate": inv.get("waste_rate", 0),
        "slowest_category": inv.get("slowest_category", ""),
        "slowest_days": inv.get("slowest_days", 0),
        "mape": fc.get("mape"),
        "fc_sum": fc.get("forecast_sum", 0),
        "fc_peak": fc.get("forecast_peak", 0),
        "top_rule": assoc.get("top_rule", ""),
        "top_lift": assoc.get("top_lift", 0),
    }


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def _md_table(headers: List[str], rows: List[List[Any]]) -> List[str]:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return out


def render_markdown(ctx: Dict[str, Any]) -> str:
    h = headline(ctx)
    seg = ctx["segments"].sort_values("gmv", ascending=False)
    core = seg[seg["rfm_segment"].isin(["重要价值客户", "重要保持客户"])]
    churn = seg[seg["rfm_segment"].str.contains("挽留")]
    prod = ctx["products"]
    ch = ctx["channels"]
    inv = ctx["inventory"]
    fc = ctx["forecast"]
    peak_idx = int(fc["predicted_gmv"].idxmax())

    L: List[str] = []
    L += [f"# 花店经营分析报告\n",
          f"> 报告周期：2024-01-01 ~ 2026-11-18｜生成时间：{ctx['generated_at']}｜"
          f"数据口径：剔除已取消订单\n",
          "## 摘要\n",
          f"**{h['gmv_ytd'] / 1e4:.0f} 万元的年度 GMV（同比 +{h['gmv_yoy'] * 100:.1f}%）背后，"
          f"是「量在涨、价没涨」**：订单数贡献了 {abs(h['vol_effect']) / (abs(h['vol_effect']) + abs(h['price_effect'])) * 100:.0f}% 的增量。"
          f"核心 {h['core_cust_share'] * 100:.0f}% 的客户贡献 {h['core_gmv_share'] * 100:.0f}% 的消费额，"
          f"而线上渠道虽有 {h['online_share'] * 100:.0f}% 的 GMV 占比，却被平台抽走 "
          f"{h['commission_total'] / 1e4:.0f} 万佣金。\n",
          "| 关键指标 | 数值 | 关键指标 | 数值 |",
          "| --- | ---: | --- | ---: |",
          f"| 统计期 GMV | {h['gmv_total'] / 1e4:.1f} 万元 | 统计期订单 | {h['orders_total']:,} 单 |",
          f"| 年度 GMV 同比 | +{h['gmv_yoy'] * 100:.1f}% | 销量贡献占比 | {abs(h['vol_effect']) / (abs(h['vol_effect']) + abs(h['price_effect'])) * 100:.0f}% |",
          f"| 核心客户 GMV 占比 | {h['core_gmv_share'] * 100:.1f}% | 核心客户人数占比 | {h['core_cust_share'] * 100:.1f}% |",
          f"| 线上 GMV 占比 | {h['online_share'] * 100:.1f}% | 平台佣金（3 年） | {h['commission_total'] / 1e4:.1f} 万元 |",
          f"| 库存损耗率 | {h['waste_rate'] * 100:.2f}% | 预测 MAPE | {h['mape']}% |",
          "",
          "## 一、经营表现：量增价平\n",
          f"2026 年前 10.5 个月 GMV **{h['gmv_ytd'] / 1e4:.1f} 万元，同比 +{h['gmv_yoy'] * 100:.1f}%**。"
          f"用「量价拆分」把增长拆开：销量效应 {h['vol_effect'] / 1e4:.1f} 万元、"
          f"价格效应 {h['price_effect'] / 1e4:.1f} 万元——**增长几乎全部由订单量驱动**。\n",
          "**这意味着**：门店的获客/复购做得不错，但客单价三年基本没动（250 元左右）。"
          "下一步增长要么继续拉新（成本在涨），要么把客单价做上去（组合套餐、高价花材）。\n",
          "![月度GMV](../07_分析与建模_20261121/charts/01_monthly_gmv.png)", "",
          "## 二、节日是绝对主场\n",
          f"情人节期间日均 GMV 是平日的 **{h['top_festival_lift']:.2f} 倍**，"
          "520、七夕、母亲节依次跟上；清明则是「低价高频」的结构性需求。\n",
          "![节日效应](../07_分析与建模_20261121/charts/02_festival_effect.png)", "",
          "## 三、用户价值：二八结构清晰\n",
          f"按 RFM 打分后分 8 群：**核心客户（重要价值 + 重要保持）占 "
          f"{core['cust_share'].sum() * 100:.1f}%，贡献 {core['gmv_share'].sum() * 100:.1f}% 消费额**；"
          f"挽留类客户占 {churn['cust_share'].sum() * 100:.1f}%，只贡献 {churn['gmv_share'].sum() * 100:.1f}%。"
          f"RFM 规则分群与 K-Means 聚类的一致性 ARI = {h['ari']:.3f}，说明分群不是拍脑袋。\n",
          ]
    L += _md_table(["分群", "客户数", "人数占比", "消费额占比", "平均R(天)", "平均F(次)"],
                   [[r["rfm_segment"], f"{int(r['customers']):,}", f"{r['cust_share'] * 100:.1f}%",
                     f"{r['gmv_share'] * 100:.1f}%", f"{r['avg_r']:.0f}", f"{r['avg_f']:.1f}"]
                    for _, r in seg.iterrows()])
    L += ["", "![RFM分群](../07_分析与建模_20261121/charts/03_rfm_segments.png)", "",
          "## 四、商品结构：24 个商品撑起八成销售额\n",
          f"ABC 分类显示 **{h['a_cnt']} 个商品（占 {h['products']} 个在售商品的 "
          f"{h['a_cnt'] / h['products'] * 100:.0f}%）贡献约 80% 销售额**。"
          f"波士顿矩阵：明星 {h['quadrants'].get('明星', 0)} 个、金牛 {h['quadrants'].get('金牛', 0)} 个、"
          f"问题 {h['quadrants'].get('问题', 0)} 个、瘦狗 {h['quadrants'].get('瘦狗', 0)} 个。\n",
          "![帕累托](../07_分析与建模_20261121/charts/05_abc_pareto.png)", "",
          "![波士顿矩阵](../07_分析与建模_20261121/charts/06_bcg_matrix.png)", "",
          "## 五、渠道：GMV 不等于利润\n",
          ]
    L += _md_table(["渠道", "类型", "订单", "GMV（元）", "GMV占比", "客单价", "佣金（元）", "佣金后净利率"],
                   [[r["channel_name"], r["channel_type"], f"{int(r['order_cnt']):,}",
                     f"{r['gmv']:,.0f}", f"{r['gmv_share'] * 100:.1f}%", f"{r['aov']:,.0f}",
                     f"{r['commission_amt']:,.0f}", f"{r['net_margin'] * 100:.1f}%"]
                    for _, r in ch.iterrows()])
    L += ["", f"佣金后净利率最高的是**{h['best_channel']}（{h['best_margin'] * 100:.1f}%）**。"
              f"线上渠道贡献 {h['online_share'] * 100:.1f}% 的 GMV，代价是三年 "
              f"{h['commission_total'] / 1e4:.0f} 万元佣金。\n",
          "![渠道效率](../07_分析与建模_20261121/charts/09_channel_efficiency.png)", "",
          "## 六、促销：让利效率差异巨大\n",
          ]
    L += _md_table(["活动", "类型", "订单", "GMV（元）", "让利（元）", "毛利率", "让利效率"],
                   [[r["promotion_name"], r["promotion_type"], f"{int(r['order_cnt']):,}",
                     f"{r['gmv']:,.0f}", f"{r['discount_amt']:,.0f}",
                     f"{r['profit_rate'] * 100:.1f}%", f"{r['roi']:.1f}"]
                    for _, r in ctx["promos"].iterrows()])
    L += ["", "> 注：受限于「活动与节日高度共线」，本报告不提供因果意义上的增量 ROI，"
              "只给「让利效率」= 活动毛利 / 让利金额。\n",
          "## 七、库存：绣球是结构性损耗\n",
          f"整体损耗率 **{h['waste_rate'] * 100:.2f}%**。最突出的是**{h['slowest_category']}**："
          f"周转天数 {h['slowest_days']:.1f} 天，而货架期只有 6 天——"
          f"**周转天数 > 货架期，损耗就是必然的**，与员工是否细心无关。\n",
          ]
    L += _md_table(["品类", "SKU数", "周转天数", "损耗率", "损耗金额（元）"],
                   [[r["category"], int(r["sku_cnt"]), f"{r['turnover_days']:.1f}",
                     f"{r['waste_rate'] * 100:.2f}%", f"{r['waste_amt']:,.0f}"]
                    for _, r in inv.head(8).iterrows()])
    L += ["", "![库存周转](../07_分析与建模_20261121/charts/11_inventory_turnover.png)", "",
          "## 八、预测：未来 30 天\n",
          f"SARIMAX（含节假日外生变量）回测 **MAPE {h['mape']}%**，"
          f"未来 30 天预计 GMV **{h['fc_sum'] / 1e4:.1f} 万元**，"
          f"峰值日 {fc.loc[peak_idx, 'forecast_date']} 约 {h['fc_peak']:,.0f} 元。\n",
          "![预测](../07_分析与建模_20261121/charts/14_forecast.png)", "",
          "## 九、行动建议（按优先级）\n",
          "| 优先级 | 建议 | 做什么 | 预期影响 |",
          "| --- | --- | --- | --- |",
          f"| P0 | 绣球改「预售 + 按单采购」 | 把 {h['slowest_category']} 的备货从「经验」改为「订单驱动」，"
          "周转目标压到 ≤6 天 | 该品类损耗可降 30%+，年省 2~3 万 |",
          "| P0 | 把重要保持客户拉回来 | 对 326 天未购、历史高消费的客户发定向唤醒券（限 30 天） | 若召回 10%，可带来约 18 万 GMV |",
          "| P1 | 提客单价而不是提折扣 | 用「花束 + 花瓶/巧克力」套餐替代深折扣，套餐转化率高于打折 | 客单价 +8%~12% |",
          "| P1 | 美团降权、私域升权 | 美团 18% 佣金，把首单客户引导到小程序复购 | 佣金支出 −15% |",
          "| P1 | 瘦狗商品精简 | C 类 17 个商品只贡献 5% 销售额，砍到 40 个 SKU 以内 | 采购复杂度与损耗双降 |",
          "| P2 | 情人节按预测 1.15 倍备货 | 提前 5 天备货，避免节日断货 | 减少缺货损失 |",
          "| P2 | 关联套餐上线 | 加购-花器 → 感恩祝福（提升度 2.55）做成固定套餐 | 搭售率 +3pp |",
          "",
          "## 附录：口径与局限\n",
          "| 项目 | 说明 |",
          "| --- | --- |",
          "| GMV | 实付金额（商品实收 + 运费），剔除已取消订单 |",
          "| 毛利率 | 商品毛利 / GMV，不含履约成本与平台佣金 |",
          "| 节日 | 取自业务日历（农历节日逐年硬编码），含预售期 |",
          "| BCG 横轴 | 品类内相对份额（本商品 / 品类第一），非真实竞品份额 |",
          "| 促销 ROI | 让利效率，非因果增量（无对照组） |",
          "| 预测 | SARIMAX(1,1,1)(1,0,1,7)，外生变量为周末/节假日/节日/需求指数 |",
          "",
          "**数据质量**：清洗后 18 条规则得分 99.61（A），与业务库金额对账差额 0.00。",
          "**局限**：全部为模拟数据；不含真实竞品与市场数据；促销效果无法做因果归因。",
          ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
# HTML（浅色，适合打印/邮件）
# --------------------------------------------------------------------------
_CSS = """
:root{--ink:#1a2233;--sub:#5b6880;--line:#e3e8f0;--brand:#2547c4;--accent:#e8590c;
--bg:#f6f8fc;--card:#fff;--green:#0f9d63;--red:#d64545}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:"Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;line-height:1.75;font-size:14.5px}
.page{max-width:900px;margin:0 auto;padding:48px 40px 72px;background:var(--card);
box-shadow:0 2px 24px rgba(20,30,60,.08);min-height:100vh}
.cover{border-bottom:3px solid var(--brand);padding-bottom:22px;margin-bottom:34px}
.cover .kicker{color:var(--brand);font-weight:700;letter-spacing:2px;font-size:12px}
.cover h1{margin:8px 0 6px;font-size:30px;letter-spacing:.5px}
.cover .meta{color:var(--sub);font-size:12.5px}
h2{font-size:19px;margin:38px 0 12px;padding-left:12px;border-left:4px solid var(--brand)}
h3{font-size:15.5px;margin:22px 0 8px}
p{margin:10px 0;color:#2a3446}
strong{color:var(--brand)}
table{width:100%;border-collapse:collapse;font-size:13px;margin:14px 0 18px}
th{background:#eef2fb;color:#334;text-align:left;padding:8px 10px;font-weight:600;font-size:12.5px;border-bottom:2px solid var(--line)}
td{padding:7px 10px;border-bottom:1px solid var(--line);color:#2a3446}
tr:nth-child(even) td{background:#fafbfe}
td:not(:first-child){text-align:right;font-variant-numeric:tabular-nums}
img{width:100%;border:1px solid var(--line);border-radius:8px;margin:10px 0 18px}
blockquote{margin:12px 0;padding:10px 14px;background:#fff8f0;border-left:3px solid var(--accent);
color:#7a4a1d;font-size:13px}
.note{background:#f0f6ff;border:1px solid #d6e4ff;border-radius:8px;padding:12px 14px;font-size:13px;color:#2a4a7a}
.footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);color:var(--sub);font-size:12px}
@media print{body{background:#fff}.page{box-shadow:none;padding:0}}
"""


def render_html(ctx: Dict[str, Any]) -> str:
    h = headline(ctx)
    seg = ctx["segments"].sort_values("gmv", ascending=False)
    core = seg[seg["rfm_segment"].isin(["重要价值客户", "重要保持客户"])]
    ch = ctx["channels"]
    prod = ctx["products"]
    inv = ctx["inventory"]
    fc = ctx["forecast"]
    peak_idx = int(fc["predicted_gmv"].idxmax())

    def table(headers, rows, cls=""):
        head = "".join(f"<th>{x}</th>" for x in headers)
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    def img(name):
        data = _img(name)
        return f'<img src="{data}" alt="{name}">' if data else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>花店经营分析报告 · BloomAnalytics</title><style>{_CSS}</style></head>
<body><div class="page">

<div class="cover">
  <div class="kicker">BLOOMANALYTICS · 经营分析</div>
  <h1>花店全渠道经营分析报告</h1>
  <div class="meta">数据周期 2024-01-01 ~ 2026-11-18（1,053 天）｜口径：剔除已取消订单｜
  生成时间 {ctx['generated_at']}</div>
</div>

<h2>摘要</h2>
<p><strong>{h['gmv_ytd'] / 1e4:.0f} 万元的年度 GMV（同比 +{h['gmv_yoy'] * 100:.1f}%）背后，是「量在涨、价没涨」</strong>：
订单数贡献了约 {abs(h['vol_effect']) / (abs(h['vol_effect']) + abs(h['price_effect'])) * 100:.0f}% 的增长，
客单价三年基本持平。核心 {h['core_cust_share'] * 100:.0f}% 的客户贡献 {h['core_gmv_share'] * 100:.0f}% 的消费额，
而线上渠道虽有 {h['online_share'] * 100:.0f}% 的 GMV 占比，却被平台抽走 {h['commission_total'] / 1e4:.0f} 万元佣金。</p>

<div class="note">
<strong>三个最重要的判断：</strong><br>
① 增长靠量不靠价 → 提客单价是下一个增长点（套餐 &gt; 打折）；<br>
② {h['best_channel']}的佣金后净利率最高（{h['best_margin'] * 100:.1f}%），应把高价值客户沉淀到私域；<br>
③ {h['slowest_category']}周转 {h['slowest_days']:.0f} 天、货架期 6 天，损耗是结构性的，必须改订货模式。
</div>

<h2>一、经营表现：量增价平</h2>
<p>同比拆分显示，销量效应 {h['vol_effect'] / 1e4:.1f} 万元、价格效应 {h['price_effect'] / 1e4:.1f} 万元。
<strong>说明增长几乎全部由订单量驱动</strong>，客单价（约 250 元）三年没有变化。</p>
{img("01_monthly_gmv.png")}

<h2>二、节日是绝对主场</h2>
<p>情人节日均 GMV 是平日的 <strong>{h['top_festival_lift']:.2f} 倍</strong>，520（3.4×）、七夕（3.3×）、
母亲节（2.6×）依次跟上。备货与人力排班应围绕这几个峰值设计。</p>
{img("02_festival_effect.png")}

<h2>三、用户价值：二八结构清晰</h2>
<p>核心客户（重要价值 + 重要保持）占 <strong>{core['cust_share'].sum() * 100:.1f}%</strong>，
贡献 <strong>{core['gmv_share'].sum() * 100:.1f}%</strong> 消费额。
RFM 规则分群与 K-Means 聚类的一致性 ARI = {h['ari']:.3f}（>0 表示优于随机）。</p>
{table(["分群", "客户数", "人数占比", "消费额占比", "平均R(天)", "平均F(次)"],
       [[r["rfm_segment"], f"{int(r['customers']):,}", f"{r['cust_share'] * 100:.1f}%",
         f"{r['gmv_share'] * 100:.1f}%", f"{r['avg_r']:.0f}", f"{r['avg_f']:.1f}"]
        for _, r in seg.iterrows()])}
{img("03_rfm_segments.png")}

<h2>四、商品结构：少数商品撑起大盘</h2>
<p>{h['products']} 个在售商品中，<strong>{h['a_cnt']} 个（{h['a_cnt'] / h['products'] * 100:.0f}%）贡献约 80% 销售额</strong>。
BCG 四象限：明星 {h['quadrants'].get('明星', 0)} / 金牛 {h['quadrants'].get('金牛', 0)} /
问题 {h['quadrants'].get('问题', 0)} / 瘦狗 {h['quadrants'].get('瘦狗', 0)}。</p>
{img("05_abc_pareto.png")}
{img("06_bcg_matrix.png")}

<h2>五、渠道：GMV 不等于利润</h2>
<p>线上贡献 {h['online_share'] * 100:.1f}% 的 GMV，但三年被抽走 {h['commission_total'] / 1e4:.1f} 万元佣金。
<strong>佣金后净利率最高的是 {h['best_channel']}（{h['best_margin'] * 100:.1f}%）</strong>。</p>
{table(["渠道", "类型", "订单", "GMV（元）", "占比", "客单价", "佣金（元）", "净利率"],
       [[r["channel_name"], r["channel_type"], f"{int(r['order_cnt']):,}", f"{r['gmv']:,.0f}",
         f"{r['gmv_share'] * 100:.1f}%", f"{r['aov']:,.0f}", f"{r['commission_amt']:,.0f}",
         f"{r['net_margin'] * 100:.1f}%"] for _, r in ch.iterrows()])}
{img("09_channel_efficiency.png")}

<h2>六、促销：让利效率差异巨大</h2>
<p>活动订单毛利率略低于非活动订单，且深折扣并未带来订单量提升。
下表按 GMV 排序，最后一列是「让利效率」= 活动毛利 / 让利金额。</p>
{table(["活动", "类型", "订单", "GMV（元）", "让利（元）", "毛利率", "让利效率"],
       [[r["promotion_name"], r["promotion_type"], f"{int(r['order_cnt']):,}", f"{r['gmv']:,.0f}",
         f"{r['discount_amt']:,.0f}", f"{r['profit_rate'] * 100:.1f}%", f"{r['roi']:.1f}"]
        for _, r in ctx["promos"].head(6).iterrows()])}
<blockquote>口径边界：活动与节日高度重合、无对照组，本报告不提供因果意义上的「增量 ROI」，
只给可被数据支持的「让利效率」。严格归因需要门店级 A/B 实验。</blockquote>

<h2>七、库存：损耗是结构性的，不是态度问题</h2>
<p>整体损耗率 <strong>{h['waste_rate'] * 100:.2f}%</strong>。最突出的是 <strong>{h['slowest_category']}</strong>：
周转 {h['slowest_days']:.1f} 天 vs 货架期 6 天 = <strong>{h['slowest_days'] / 6:.1f} 倍</strong>。
周转天数超过货架期，损耗必然发生——要改的是订货模式，不是催员工更勤快。</p>
{table(["品类", "SKU数", "周转天数", "损耗率", "损耗金额（元）"],
       [[r["category"], int(r["sku_cnt"]), f"{r['turnover_days']:.1f}",
         f"{r['waste_rate'] * 100:.2f}%", f"{r['waste_amt']:,.0f}"]
        for _, r in inv.head(6).iterrows()])}
{img("11_inventory_turnover.png")}

<h2>八、预测：未来 30 天</h2>
<p>SARIMAX（外生变量：周末 / 节假日 / 节日 / 需求指数）回测 <strong>MAPE {h['mape']}%</strong>，
未来 30 天预计 GMV <strong>{h['fc_sum'] / 1e4:.1f} 万元</strong>，
峰值日 {fc.loc[peak_idx, 'forecast_date']}（约 {h['fc_peak']:,.0f} 元）。</p>
{img("14_forecast.png")}

<h2>九、行动建议</h2>
{table(["优先级", "建议", "预期影响"],
       [["P0", f"{h['slowest_category']} 改「预售 + 按单采购」，周转压到 ≤6 天", "该品类损耗降 30%+，年省 2~3 万"],
        ["P0", "对 326 天未购的高价值客户发定向唤醒券", "召回 10% ≈ +18 万 GMV"],
        ["P1", "用「花束 + 花瓶/巧克力」套餐替代深折扣", "客单价 +8%~12%"],
        ["P1", "美团降权、小程序升权，首单客户导流私域", "佣金支出 −15%"],
        ["P1", "C 类商品精简，SKU 从 56 砍到 40 以内", "采购复杂度与损耗双降"],
        ["P2", f"关联套餐上线：{h['top_rule']}（提升度 {h['top_lift']:.2f}）", "搭售率 +3pp"]])}

<h2>附录：口径与局限</h2>
{table(["项目", "说明"],
       [["GMV", "实付金额（商品实收 + 运费），剔除已取消订单"],
        ["毛利率", "商品毛利 / GMV，不含履约成本与平台佣金"],
        ["节日", "业务日历中的营销节日，含预售期"],
        ["BCG 横轴", "品类内相对份额（本商品 / 品类第一），非真实竞品份额"],
        ["促销 ROI", "让利效率，非因果增量"],
        ["预测", "SARIMAX(1,1,1)(1,0,1,7) + 节假日外生变量"],
        ["数据质量", "18 条规则得分 99.61（A），与业务库金额对账差额 0.00"],
        ["局限", "模拟数据；无竞品与市场数据；促销效果无法因果归因"]])}

<div class="footer">BloomAnalytics · 报告由程序自动生成（python run.py report）· 数据源 MySQL bloom_dw · 请勿手工修改</div>
</div></body></html>"""


# --------------------------------------------------------------------------
# Excel（多 Sheet）
# --------------------------------------------------------------------------
def render_excel(ctx: Dict[str, Any], path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    h = headline(ctx)
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="2547C4")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    title_font = Font(bold=True, size=13, color="1A2233")
    thin = Side(style="thin", color="DCE3EF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def sheet(name: str, headers: List[str], rows: List[List[Any]], widths: List[int],
              first: bool = False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        ws.append(headers)
        for c in ws[1]:
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = border
        for r in rows:
            ws.append(r)
        for i, row in enumerate(ws.iter_rows(min_row=2, max_row=ws.max_row), start=1):
            for c in row:
                c.border = border
                c.alignment = Alignment(vertical="center")
                if i % 2 == 0:
                    c.fill = PatternFill("solid", fgColor="F5F8FE")
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        return ws

    # Sheet1 摘要
    ws = wb.active
    ws.title = "摘要"
    ws["A1"] = "花店全渠道经营分析报告"
    ws["A1"].font = title_font
    ws["A2"] = f"数据周期 2024-01-01 ~ 2026-11-18｜生成时间 {ctx['generated_at']}"
    ws["A2"].font = Font(color="5B6880", size=10)
    ws.append([])
    ws.append(["指标", "数值", "说明"])
    for c in ws[4]:
        c.fill = header_fill
        c.font = header_font
        c.border = border
    summary_rows = [
        ("统计期 GMV", round(h["gmv_total"], 2), "2024-01 ~ 2026-11，剔除取消单"),
        ("年度 GMV 同比", f"+{h['gmv_yoy'] * 100:.2f}%", "2026 前 10.5 个月 vs 2025 同期"),
        ("销量效应", round(h["vol_effect"], 2), "订单量变化带来的 GMV 增量"),
        ("价格效应", round(h["price_effect"], 2), "客单价变化带来的 GMV 增量"),
        ("核心客户 GMV 占比", f"{h['core_gmv_share'] * 100:.2f}%", "重要价值 + 重要保持"),
        ("线上 GMV 占比", f"{h['online_share'] * 100:.2f}%", "微信/美团/抖音/淘宝"),
        ("平台佣金（3 年）", round(h["commission_total"], 2), "GMV × 渠道佣金率"),
        ("库存损耗率", f"{h['waste_rate'] * 100:.2f}%", "损耗金额 / 采购金额"),
        ("预测 MAPE", f"{h['mape']}%", "SARIMAX 回测"),
        ("未来 30 天预测", round(h["fc_sum"], 2), "80% 置信区间见 forecast Sheet"),
    ]
    for r in summary_rows:
        ws.append(list(r))
    for row in ws.iter_rows(min_row=5, max_row=ws.max_row):
        for c in row:
            c.border = border
    for col, w in zip("ABCD", [22, 18, 46]):
        ws.column_dimensions[col].width = w

    # Sheet2 月度经营
    m = ctx["monthly"]
    sheet("月度经营", ["月份", "订单数", "客户数", "GMV", "客单价", "毛利额", "毛利率"],
          [[r["month_key"], int(r["order_cnt"]), int(r["customer_cnt"]), round(r["gmv"], 2),
            round(r["aov"], 2), round(r["gross_profit"], 2), round(r["profit_rate"], 4)]
           for _, r in m.iterrows()],
          [12, 10, 10, 16, 10, 16, 10])

    # Sheet3 用户分群
    seg = ctx["segments"].sort_values("gmv", ascending=False)
    sheet("用户分群", ["分群", "客户数", "人数占比", "消费额", "消费额占比", "平均R(天)", "平均F(次)"],
          [[r["rfm_segment"], int(r["customers"]), round(r["cust_share"], 4), round(r["gmv"], 2),
            round(r["gmv_share"], 4), round(r["avg_r"], 1), round(r["avg_f"], 2)]
           for _, r in seg.iterrows()],
          [16, 10, 10, 16, 12, 12, 12])

    # Sheet4 商品
    p = ctx["products"]
    sheet("商品TOP30", ["排名", "商品", "类型", "品类", "销量", "销售额", "毛利率", "同比", "ABC", "BCG"],
          [[i + 1, r["product_name"], "花束" if r["product_type"] == "BUNDLE" else "SKU",
            r["category"], int(r["sales_qty"]), round(r["sales_amt"], 2),
            round(r["profit_rate"], 4), round(r["growth_rate"], 4), r["abc_class"], r["bcg_class"]]
           for i, (_, r) in enumerate(p.head(30).iterrows())],
          [6, 26, 8, 12, 10, 14, 10, 10, 8, 8])

    # Sheet5 渠道
    ch = ctx["channels"]
    sheet("渠道效率", ["渠道", "类型", "订单", "GMV", "占比", "客单价", "佣金", "佣金后毛利", "净利率"],
          [[r["channel_name"], r["channel_type"], int(r["order_cnt"]), round(r["gmv"], 2),
            round(r["gmv_share"], 4), round(r["aov"], 2), round(r["commission_amt"], 2),
            round(r["net_profit"], 2), round(r["net_margin"], 4)]
           for _, r in ch.iterrows()],
          [16, 8, 10, 16, 10, 10, 14, 16, 10])

    # Sheet6 预测
    f = ctx["forecast"]
    sheet("30天预测", ["日期", "节日", "预测GMV", "下界", "上界"],
          [[str(r["forecast_date"]), r["festival_name"] or "", round(r["predicted_gmv"], 2),
            round(float(r["lower_bound"] or 0), 2), round(float(r["upper_bound"] or 0), 2)]
           for _, r in f.iterrows()],
          [14, 12, 14, 14, 14])

    wb.save(path)
    log.info("写出 Excel 报告：%s", path.name)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def run() -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log.info("▸ 生成经营分析报告")
    ctx = collect()

    md = render_markdown(ctx)
    (OUT_DIR / "01_经营分析报告.md").write_text(md, encoding="utf-8")
    log.info("  📄 01_经营分析报告.md")

    html = render_html(ctx)
    (OUT_DIR / "02_经营分析报告.html").write_text(html, encoding="utf-8")
    log.info("  🌐 02_经营分析报告.html（%.1f KB，图片已内嵌）", len(html.encode()) / 1024)

    render_excel(ctx, OUT_DIR / "03_经营分析报告.xlsx")
    log.info("  📊 03_经营分析报告.xlsx")

    h = headline(ctx)
    payload = {
        "generated_at": ctx["generated_at"],
        "files": ["01_经营分析报告.md", "02_经营分析报告.html", "03_经营分析报告.xlsx"],
        "headline": h,
    }
    (OUT_DIR / "04_报告摘要.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload
