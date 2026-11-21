"""治理报告渲染：Markdown / HTML / JSON / Excel。

为什么同一份数据要出四种格式？
------------------------------
- **JSON**：给程序和看板用（第 4 轮 Web 接口直接读）；
- **Markdown**：给 Git 看，diff 友好，评审时能直接看到数字变化；
- **HTML**：给老板看，带图表、能直接发邮件/挂网页；
- **Excel**：给业务方改，数据字典与指标字典都支持筛选。

四种格式共用同一份"结构化结果"，避免"PPT 上的数和报表对不上"。
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from ..logger import get_logger

log = get_logger("report")

# 中文字体（Windows/macOS 常见字体按顺序回退）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

PALETTE = {
    "blue": "#4C6FFF", "green": "#2BC48A", "orange": "#F5A524",
    "red": "#F04E4E", "gray": "#8A94A6", "purple": "#8B5CF6",
}


# --------------------------------------------------------------------------
# 图表（返回 base64 PNG）
# --------------------------------------------------------------------------
def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def chart_dimension_scores(ods: Dict[str, float], dwd: Dict[str, float]) -> str:
    dims = list(ods.keys())
    x = range(len(dims))
    fig, ax = plt.subplots(figsize=(7.2, 3.2), facecolor="white")
    w = 0.36
    before = [(1 - ods[d]) * 100 for d in dims]
    after = [(1 - dwd[d]) * 100 for d in dims]
    ax.bar([i - w / 2 for i in x], before, w, label="清洗前（ODS）",
           color=PALETTE["red"], alpha=0.85)
    ax.bar([i + w / 2 for i in x], after, w, label="清洗后（DWD）",
           color=PALETTE["green"], alpha=0.9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(dims, fontsize=10)
    ax.set_ylabel("问题率（%）", fontsize=10)
    ax.set_title("五维质量问题率：清洗前后对比（越低越好）", fontsize=12, pad=10)
    ax.legend(frameon=False, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    for i, v in enumerate(before):
        ax.text(i - w / 2, v + max(before) * 0.02, f"{v:.2f}", ha="center", fontsize=7.5,
                color=PALETTE["red"])
    for i, v in enumerate(after):
        ax.text(i + w / 2, v + max(before) * 0.02, f"{v:.2f}", ha="center", fontsize=7.5,
                color=PALETTE["green"])
    return _fig_to_base64(fig)


def chart_rule_violations(rules: List[Dict[str, Any]], top: int = 12) -> str:
    items = sorted(rules, key=lambda r: r["detected"], reverse=True)[:top]
    items = [i for i in items if i["detected"] > 0][::-1]
    fig, ax = plt.subplots(figsize=(7.2, max(2.8, 0.32 * len(items) + 1)), facecolor="white")
    labels = [f"{r['code']} {r['name']}" for r in items]
    vals = [r["detected"] for r in items]
    colors = [PALETTE["blue"] if r["dimension"] != "唯一性" else PALETTE["purple"] for r in items]
    ax.barh(labels, vals, color=colors, alpha=0.9)
    ax.set_xlabel("检出问题行数（ODS 清洗前）", fontsize=10)
    ax.set_title("各规则检出量 TOP12", fontsize=12, pad=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    for y, v in enumerate(vals):
        ax.text(v, y, f" {v:,}", va="center", fontsize=8.5)
    return _fig_to_base64(fig)


def chart_clean_funnel(stats: Dict[str, Any]) -> str:
    """清洗漏斗：输入 → 去重 → 隔离 → 入仓。"""
    rows_in = stats["rows_in"]["orders"]
    dedupe = stats["dedupe"].get("E01", 0)
    rejects = stats["rejects"].get("E03", 0) + stats["rejects"].get("E04", 0) + stats["rejects"].get("E02", 0)
    out = stats["rows_out"]["orders"]

    stages = ["ODS 原始行", "去重后", "剔除坏值后", "DWD 入仓"]
    values = [rows_in, rows_in - dedupe, rows_in - dedupe - rejects, out]
    colors = [PALETTE["gray"], PALETTE["blue"], PALETTE["orange"], PALETTE["green"]]

    fig, ax = plt.subplots(figsize=(7.2, 2.6), facecolor="white")
    bars = ax.barh(stages[::-1], values[::-1], color=colors[::-1], alpha=0.92)
    ax.set_xlim(0, max(values) * 1.18)
    ax.set_title("订单清洗漏斗", fontsize=12, pad=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", linestyle="--", alpha=0.25)
    for b, v in zip(bars, values[::-1]):
        ax.text(v, b.get_y() + b.get_height() / 2, f"  {v:,}", va="center", fontsize=9)
    return _fig_to_base64(fig)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
_CSS = """
:root{--bg:#0f1420;--panel:#161d2d;--card:#1c2437;--line:#2a3447;--text:#e8edf7;
--muted:#93a0b8;--blue:#4c6fff;--green:#2bc48a;--orange:#f5a524;--red:#f04e4e;--purple:#8b5cf6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:"Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;line-height:1.7}
.wrap{max-width:1120px;margin:0 auto;padding:36px 28px 64px}
.hero{background:linear-gradient(135deg,#1b2540 0%,#141b2b 60%,#101725 100%);
border:1px solid var(--line);border-radius:18px;padding:30px 32px;margin-bottom:22px}
.hero h1{margin:0 0 6px;font-size:26px;letter-spacing:.5px}
.hero p{margin:0;color:var(--muted);font-size:14px}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
background:rgba(76,111,255,.16);color:#9db3ff;border:1px solid rgba(76,111,255,.35);margin-right:8px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0 26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.card .k{color:var(--muted);font-size:12px;letter-spacing:.5px}
.card .v{font-size:24px;font-weight:700;margin-top:6px}
.card .d{font-size:12px;color:var(--muted);margin-top:4px}
.up{color:var(--green)} .down{color:var(--red)}
h2{font-size:18px;margin:30px 0 10px;padding-left:10px;border-left:3px solid var(--blue)}
h3{font-size:15px;margin:18px 0 8px;color:#c9d4ea}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--panel);
border-radius:10px;overflow:hidden;margin:8px 0 16px}
th{background:#202a3f;color:#b9c6e0;text-align:left;padding:9px 10px;font-weight:600;font-size:12.5px}
td{padding:8px 10px;border-top:1px solid var(--line);color:#d7e0f2}
tr:hover td{background:#1b2436}
img{width:100%;border-radius:12px;background:#fff;padding:10px;margin:6px 0 4px}
.note{background:rgba(245,165,36,.08);border:1px solid rgba(245,165,36,.3);
border-radius:10px;padding:12px 14px;font-size:13px;color:#f0d6a8;margin:12px 0}
.ok{color:var(--green)} .bad{color:var(--red)} .muted{color:var(--muted)}
.footer{margin-top:36px;color:var(--muted);font-size:12px;text-align:center}
"""


def _table(headers: List[str], rows: List[List[Any]], align: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = ""
    for r in rows:
        cells = "".join(f"<td>{c}</td>" for c in r)
        body += f"<tr>{cells}</tr>"
    return f'<table align="{align}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render_quality_html(payload: Dict[str, Any]) -> str:
    ods, dwd = payload["ods"], payload["dwd"]
    stats = payload["clean_stats"]
    charts = payload["charts"]

    rule_rows = []
    dwd_map = {r["code"]: r["detected"] for r in dwd["rules"]}
    for r in ods["rules"]:
        after = dwd_map.get(r["code"], 0)
        status = "✅ 已归零" if after == 0 else f"⚠️ 保留 {after:,}"
        expected = r.get("expected")
        rule_rows.append([
            r["code"], r["name"], r["dimension"], f"{r['detected']:,}",
            f"{r['detected'] / r['total'] * 100:.3f}%",
            f"{expected:,}" if expected is not None else "—",
            f"{after:,}", status,
        ])

    dim_rows = []
    for d in ods["dimensions"]:
        b, a = ods["dimensions"][d], dwd["dimensions"][d]
        delta = (a - b) * 100
        cls = "up" if delta >= 0 else "down"
        dim_rows.append([d, f"{b * 100:.2f}%", f"{a * 100:.2f}%",
                         f'<span class="{cls}">{delta:+.2f}pp</span>'])

    reject_rows = []
    for code, cnt in sorted(stats["rejects"].items(), key=lambda x: -x[1]):
        if cnt:
            reject_rows.append([code, payload["clean_rules"].get(code, ""), f"{cnt:,}"])

    repair_rows = []
    for code, cnt in sorted(stats["repairs"].items(), key=lambda x: -x[1]):
        if cnt:
            repair_rows.append([code, payload["clean_rules"].get(code, ""), f"{cnt:,}"])

    card = lambda k, v, d="", cls="": (
        f'<div class="card"><div class="k">{k}</div>'
        f'<div class="v {cls}">{v}</div><div class="d">{d}</div></div>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>数据质量报告 · BloomAnalytics</title><style>{_CSS}</style></head>
<body><div class="wrap">
  <div class="hero">
    <h1>🌸 数据质量报告</h1>
    <p>花店全渠道经营数据分析平台｜生成时间 {payload['generated_at']}｜
       数据范围 2024-01-01 ~ 2026-11-18｜规则版本 v1.0</p>
    <div style="margin-top:12px">
      <span class="badge">ODS 贴源层</span>
      <span class="badge">DWD 清洗层</span>
      <span class="badge">18 条质量规则</span>
      <span class="badge">5 个维度</span>
    </div>
  </div>

  <div class="cards">
    {card("清洗前质量得分", f"{ods['overall'] * 100:.2f}", "等级 " + ods["grade"], "down")}
    {card("清洗后质量得分", f"{dwd['overall'] * 100:.2f}", "等级 " + dwd["grade"], "up")}
    {card("提分", f"+{(dwd['overall'] - ods['overall']) * 100:.2f}", "满分 100 分")}
    {card("隔离异常行", f"{sum(stats['rejects'].values()):,}", "全部留痕可追溯", "down")}
  </div>

  <h2>一、五维质量评分</h2>
  <img src="data:image/png;base64,{charts['dimensions']}" alt="五维得分">
  {_table(["维度", "清洗前", "清洗后", "变化"], dim_rows)}

  <h2>二、逐规则检出情况</h2>
  <img src="data:image/png;base64,{charts['rules']}" alt="规则检出">
  <p class="muted" style="font-size:12.5px">
    注入数 = 数据工厂记录的"标准答案"；检出数略高是因为一行可能同时命中多条规则
    （例如重复行同时也带来客户ID缺失），属于正常现象。</p>
  {_table(["规则", "问题", "维度", "检出(ODS)", "问题率", "注入数", "清洗后", "状态"], rule_rows)}

  <h2>三、清洗动作</h2>
  <div class="note">清洗遵循「能修则修、不能修则隔离、事实缺失只标记不编造」三原则：
  修复与隔离都会写入 <code>repair_flags</code> 和 <code>dwd_rejects</code>，可逐行追溯。</div>
  <h3>3.1 清洗漏斗</h3>
  <img src="data:image/png;base64,{charts['funnel']}" alt="清洗漏斗">
  <h3>3.2 已修复（{sum(stats['repairs'].values()):,} 行次）</h3>
  {_table(["规则", "动作", "影响行数"], repair_rows)}
  <h3>3.3 已隔离（{sum(stats['rejects'].values()):,} 行）</h3>
  {_table(["规则", "原因", "行数"], reject_rows)}

  <h2>四、结论与建议</h2>
  <ul style="color:#d7e0f2;font-size:14px">
    <li>唯一性、有效性、及时性三维在清洗后<b class="ok">全部归零</b>，
        说明格式类、重复类、取值类问题已被规则完整覆盖。</li>
    <li>完整性与一致性仍有残留，但都是<b>「可识别不可修复」</b>：
        客户ID缺失只能置 UNKNOWN 占位、已完成的配送时间缺失不能编造。
        这两类已在 <code>repair_flags</code> 打标，分析时按标记排除。</li>
    <li>建议把「下游报表排除 E06 / E19 标记行」写进指标口径，
        而不是在 ETL 里把数据「洗成 100 分」。</li>
    <li>建议将 18 条规则接入调度：每日跑批后自动出分，低于阈值告警。</li>
  </ul>

  <div class="footer">BloomAnalytics · 数据治理模块 · 报告由程序自动生成，请勿手工修改</div>
</div></body></html>"""


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def render_quality_markdown(payload: Dict[str, Any]) -> str:
    ods, dwd, stats = payload["ods"], payload["dwd"], payload["clean_stats"]
    lines = [
        "# 数据质量报告\n",
        f"> 生成时间：{payload['generated_at']}｜数据范围：2024-01-01 ~ 2026-11-18｜"
        "规则数：18｜维度：5\n",
        "## 一、总体评分\n",
        "| 指标 | 清洗前（ODS） | 清洗后（DWD） |",
        "| --- | ---: | ---: |",
        f"| 质量得分 | {ods['overall'] * 100:.2f} | **{dwd['overall'] * 100:.2f}** |",
        f"| 等级 | {ods['grade']} | **{dwd['grade']}** |",
        f"| 总行数 | {ods['total_rows']:,} | {dwd['total_rows']:,} |",
        "",
        "## 二、五维评分\n",
        "| 维度 | 清洗前 | 清洗后 | 变化 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for d in ods["dimensions"]:
        b, a = ods["dimensions"][d], dwd["dimensions"][d]
        lines.append(f"| {d} | {b * 100:.2f}% | {a * 100:.2f}% | {(a - b) * 100:+.2f}pp |")

    lines += ["", "## 三、逐规则明细\n",
              "| 规则 | 问题 | 维度 | 检出 | 问题率 | 注入数 | 清洗后 |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    dwd_map = {r["code"]: r["detected"] for r in dwd["rules"]}
    for r in ods["rules"]:
        exp = r.get("expected")
        lines.append(
            f"| {r['code']} | {r['name']} | {r['dimension']} | {r['detected']:,} | "
            f"{r['detected'] / r['total'] * 100:.3f}% | {exp if exp is not None else '—'} | "
            f"{dwd_map.get(r['code'], 0):,} |")

    lines += ["", "## 四、清洗动作统计\n", "### 4.1 已修复\n",
              "| 规则 | 动作 | 行数 |", "| --- | --- | ---: |"]
    for code, cnt in sorted(stats["repairs"].items(), key=lambda x: -x[1]):
        if cnt:
            lines.append(f"| {code} | {payload['clean_rules'].get(code, '')} | {cnt:,} |")

    lines += ["", "### 4.2 已隔离\n", "| 规则 | 原因 | 行数 |", "| --- | --- | ---: |"]
    for code, cnt in sorted(stats["rejects"].items(), key=lambda x: -x[1]):
        if cnt:
            lines.append(f"| {code} | {payload['clean_rules'].get(code, '')} | {cnt:,} |")

    lines += ["", "## 五、结论\n",
              "- 唯一性 / 有效性 / 及时性：清洗后**全部归零**；",
              "- 完整性 / 一致性：残留问题均为**可识别不可修复**（客户ID缺失置 UNKNOWN、"
              "已完成单缺配送时间），已打标并在分析口径中排除；",
              "- 全部清洗动作可追溯（`repair_flags`），全部隔离数据可复核（`dwd_rejects`）。",
              ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 指标 / 标签 / 字典的 Markdown
# --------------------------------------------------------------------------
def render_metrics_markdown(rows: List[Dict[str, Any]], fmt) -> str:
    lines = [
        "# 指标体系（48 个指标）\n",
        "> 全部指标由 `bloom.governance.metrics` 定义并实时计算，口径唯一、可复算。\n",
        "## 一、按层级\n",
        "| 层级 | 定义 | 指标数 |",
        "| --- | --- | ---: |",
    ]
    for layer in ("原子", "派生", "复合"):
        cnt = sum(1 for r in rows if r["layer"] == layer)
        desc = {
            "原子": "不可再拆的度量，直接来自事实表",
            "派生": "原子指标 + 业务限定 + 时间周期",
            "复合": "多个指标运算得来",
        }[layer]
        lines.append(f"| {layer} | {desc} | {cnt} |")

    lines += ["", "## 二、指标明细\n",
              "| 编码 | 指标 | 层级 | 域 | 口径 | 周期 | 当前值 | 单位 |",
              "| --- | --- | --- | --- | --- | --- | ---: | --- |"]
    for r in rows:
        lines.append(
            f"| {r['code']} | {r['name']} | {r['layer']} | {r['domain']} | "
            f"{r['formula']} | {r['cycle']} | {fmt(r)} | {r['unit']} |")
    lines += ["", "## 三、使用建议\n",
              "- **看板**：优先展示复合指标（客单价、毛利率、复购率），它们才是决策依据；",
              "- **诊断**：先用原子指标定位（订单少了还是客单价降了），再看派生指标找原因；",
              "- **口径变更**：任何公式修改必须走 Git 评审，禁止在看板 SQL 里私自改口径。", ""]
    return "\n".join(lines)


def write_excel(path: Path, sheet: str, headers: List[str], rows: List[List[Any]],
                widths: List[int] | None = None) -> None:
    """通用 Excel 写出（带表头样式、斑马纹、冻结、筛选）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(headers)

    header_fill = PatternFill("solid", fgColor="2F5597")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    zebra = PatternFill("solid", fgColor="F2F5FA")
    for r in rows:
        ws.append(r)
    for i, row in enumerate(ws.iter_rows(min_row=2, max_row=ws.max_row), start=1):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if i % 2 == 0:
                cell.fill = zebra

    for idx, w in enumerate(widths or [18] * len(headers), start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def render_tags_markdown(coverage: List[Dict[str, Any]]) -> str:
    lines = [
        "# 标签体系（18 个标签）\n",
        "> 标签目录存于 `dim_tag`，客户标签落在 `ads_customer_tags`（12 个），"
        "商品标签落在 `ads_product_tags`（6 个）。\n",
        "## 一、标签清单与覆盖率\n",
        "| 编码 | 标签 | 类型 | 分组 | 值域 | 覆盖率 | 取值数 | 口径 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for t in coverage:
        lines.append(
            f"| {t['code']} | {t['name']} | {t['type']} | {t['group']} | {t['value_domain']} | "
            f"{t['coverage'] * 100:.1f}% | {t['distinct_values']} | {t['rule']} |")

    lines += ["", "## 二、主要标签分布\n"]
    for t in coverage:
        if t["type"] != "客户" or t["code"] not in ("T02", "T05", "T06", "T07", "T10"):
            continue
        lines.append(f"### {t['code']} {t['name']}\n")
        lines.append("| 取值 | 客户数 | 占比 |")
        lines.append("| --- | ---: | ---: |")
        for v in t["top_values"]:
            lines.append(f"| {v['value']} | {v['count']:,} | {v['ratio'] * 100:.2f}% |")
        lines.append("")

    lines += ["## 三、标签治理规则\n",
              "1. **必须有目录**：新增标签先在 `dim_tag` 登记口径、值域、责任人；",
              "2. **值域必须封闭**：禁止出现自由文本取值（如城市写「上海/上海市」两种）；",
              "3. **覆盖率监控**：覆盖率 <90% 的标签不得用于精准营销圈选；",
              "4. **可回溯**：标签由 `dws_*_profile` 推导，特征表重算即标签重算。", ""]
    return "\n".join(lines)
