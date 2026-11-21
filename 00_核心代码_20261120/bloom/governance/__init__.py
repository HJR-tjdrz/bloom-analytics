"""数据治理：质量规则引擎、数据字典、指标体系、标签体系。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from ..config import GOVERNANCE_DIR, MYSQL_DW_DB
from ..etl import load_run
from ..etl.clean import CLEAN_RULES
from ..logger import get_logger
from . import dictionary, metrics, report, tags
from .quality import DIMENSIONS, RULES, evaluate

log = get_logger("governance")


def _write_rules_catalog() -> None:
    lines = [
        "# 数据质量规则清单（DQ01–DQ18）\n",
        "> 规则即配置：每条规则用一段条件表达式描述「什么算坏数据」，\n"
        "> 同一套规则在 ODS（清洗前）与 DWD（清洗后）各跑一遍，才能证明清洗有效。\n",
        "## 一、维度定义与权重\n",
        "| 维度 | 权重 | 含义 | 覆盖规则 |",
        "| --- | ---: | --- | --- |",
    ]
    dim_desc = {
        "完整性": "关键字段是否缺失",
        "唯一性": "是否存在重复记录",
        "有效性": "取值/格式是否合法",
        "一致性": "跨字段、跨表是否自相矛盾",
        "及时性": "时间是否在业务合理区间",
    }
    for dim, weight in DIMENSIONS.items():
        codes = [r.code for r in RULES if r.dimension == dim]
        lines.append(f"| {dim} | {weight:.0%} | {dim_desc[dim]} | {', '.join(codes)} |")

    lines += ["", "## 二、规则明细\n",
              "| 编码 | 规则 | 维度 | 作用表 | 判定条件（SQL 片段） | 清洗策略 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for r in RULES:
        cond = r.cond or f"按 `{r.unique_key}` 统计重复"
        cond = cond.replace("|", "\\|")
        lines.append(f"| {r.code} | {r.name} | {r.dimension} | `{r.layer_table}` | "
                     f"`{cond}` | {r.fix_policy} |")

    lines += ["", "## 三、评分算法\n",
              "```",
              "维度得分 = 1 - Σ(该维度违规行数) / Σ(表总行数)",
              "总分     = Σ(维度得分 × 维度权重)",
              "```",
              "- 违规行数按表分别统计后求和，并做「不超过该表总行数」的封顶，避免重复计数放大；",
              "- 唯一性维度用 `COUNT(*) - COUNT(DISTINCT 主键)` 精确计算，只算「多出来的那一条」；",
              "- 评分不是目的：**能定位到具体规则、具体行、具体修复动作**才有意义。", ""]
    (GOVERNANCE_DIR / "01_数据质量规则清单.md").write_text("\n".join(lines), encoding="utf-8")


def run_all() -> Dict[str, Any]:
    log.info("=" * 70)
    log.info("第 2 轮 · 数据治理（质量 / 字典 / 指标 / 标签）")
    log.info("=" * 70)

    etl_run = load_run()
    clean_stats = etl_run.get("clean_stats")
    if not clean_stats:
        log.warning("未找到 ETL 运行结果，请先执行 python run.py etl；质量报告将缺少清洗动作数据")
        clean_stats = {"rows_in": {}, "rows_out": {}, "dedupe": {}, "rejects": {}, "repairs": {}}

    # ---- 1. 质量评估（ODS vs DWD）----
    log.info("[1/5] 执行质量规则（18 条 × 2 层）…")
    ods = evaluate("ods")
    dwd = evaluate("dwd")
    log.info("  ODS 得分 %.4f（%s） → DWD 得分 %.4f（%s）",
             ods["overall"], ods["grade"], dwd["overall"], dwd["grade"])

    # ---- 2. 报告 ----
    log.info("[2/5] 生成质量报告（MD / HTML / JSON）…")
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ods": ods,
        "dwd": dwd,
        "clean_stats": clean_stats,
        "clean_rules": CLEAN_RULES,
        "charts": {
            "dimensions": report.chart_dimension_scores(ods["dimensions"], dwd["dimensions"]),
            "rules": report.chart_rule_violations(ods["rules"]),
            "funnel": report.chart_clean_funnel(clean_stats),
        },
    }
    (GOVERNANCE_DIR / "02_数据质量报告.md").write_text(
        report.render_quality_markdown(payload), encoding="utf-8")
    (GOVERNANCE_DIR / "03_数据质量报告.html").write_text(
        report.render_quality_html(payload), encoding="utf-8")
    (GOVERNANCE_DIR / "04_数据质量报告.json").write_text(
        json.dumps({k: v for k, v in payload.items() if k != "charts"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    _write_rules_catalog()

    # ---- 3. 数据字典 ----
    log.info("[3/5] 生成数据字典（MD / Excel）…")
    dict_rows = dictionary.collect()
    (GOVERNANCE_DIR / "05_数据字典.md").write_text(
        dictionary.render_markdown(dict_rows), encoding="utf-8")
    dictionary.render_excel(dict_rows, GOVERNANCE_DIR / "06_数据字典.xlsx")

    # ---- 4. 指标体系 ----
    log.info("[4/5] 计算指标体系（48 个指标）…")
    metric_rows = metrics.compute_all()
    (GOVERNANCE_DIR / "07_指标体系.md").write_text(
        report.render_metrics_markdown(metric_rows, metrics.format_value), encoding="utf-8")
    report.write_excel(
        GOVERNANCE_DIR / "08_指标体系.xlsx", "指标体系",
        ["编码", "指标", "层级", "业务域", "口径", "统计周期", "当前值", "单位"],
        [[r["code"], r["name"], r["layer"], r["domain"], r["formula"], r["cycle"],
          metrics.format_value(r), r["unit"]] for r in metric_rows],
        widths=[8, 20, 8, 10, 46, 10, 16, 8],
    )

    # ---- 5. 标签体系 ----
    log.info("[5/5] 构建标签体系（18 个标签）…")
    tag_counts = tags.build_tags()
    coverage = tags.coverage()
    (GOVERNANCE_DIR / "09_标签体系.md").write_text(
        report.render_tags_markdown(coverage), encoding="utf-8")
    import csv

    with open(GOVERNANCE_DIR / "10_标签体系.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["标签编码", "标签名称", "标签类型", "分组", "值域", "计算口径",
                         "覆盖率", "取值数", "TOP1取值", "TOP1占比"])
        for t in coverage:
            top = t["top_values"][0] if t["top_values"] else {"value": "", "ratio": 0}
            writer.writerow([t["code"], t["name"], t["type"], t["group"], t["value_domain"],
                             t["rule"], f"{t['coverage'] * 100:.1f}%", t["distinct_values"],
                             top["value"], f"{top['ratio'] * 100:.1f}%"])

    summary = {
        "generated_at": payload["generated_at"],
        "ods_score": ods["overall"], "dwd_score": dwd["overall"],
        "ods_grade": ods["grade"], "dwd_grade": dwd["grade"],
        "rules": len(ods["rules"]),
        "dictionary_fields": len(dict_rows),
        "metrics": len(metric_rows),
        "tags": len(coverage),
        "tag_tables": tag_counts,
    }
    log.info("治理完成：ODS %.2f → DWD %.2f｜字段 %d｜指标 %d｜标签 %d",
             ods["overall"] * 100, dwd["overall"] * 100,
             len(dict_rows), len(metric_rows), len(coverage))
    return summary
