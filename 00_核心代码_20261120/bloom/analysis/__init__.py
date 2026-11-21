"""分析建模总控：8 个分析模块 → 9 份报告 + 14 张图 + 5 张结果表。

模块顺序不是随便排的，而是"从整体到局部、从现象到预测"：
    经营总览（发生了什么）
      → 用户（谁在买）→ 商品（买什么）→ 促销（怎么卖）→ 渠道（在哪卖）
      → 库存（卖不动怎么办）→ 关联（还能搭着卖什么）→ 预测（明天卖多少）
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

from .. import db
from ..config import MYSQL_DW_DB
from ..logger import get_logger
from . import association, base, channel, forecast, inventory, overview, product, promo, rfm

log = get_logger("analysis")

DDL_FILE = base.ANALYSIS_DIR / "01_分析结果表_建表.sql"
SUMMARY_FILE = base.ANALYSIS_DIR / "10_分析结果汇总.json"

MODULES = [
    ("overview", "经营总览与趋势", overview.run),
    ("rfm", "RFM 用户价值", rfm.run),
    ("product", "商品结构与波士顿矩阵", product.run),
    ("promo", "促销效果", promo.run),
    ("channel", "渠道与门店效率", channel.run),
    ("inventory", "库存与损耗", inventory.run),
    ("association", "关联规则", association.run),
    ("forecast", "销售预测", forecast.run),
]


def run_all() -> Dict[str, Any]:
    log.info("=" * 72)
    log.info("第 3 轮 · 分析建模（8 个模块）")
    log.info("=" * 72)

    db.run_sql_file(DDL_FILE, database=MYSQL_DW_DB)

    results: Dict[str, Any] = {}
    for key, title, fn in MODULES:
        try:
            results[key] = fn()
            log.info("  ✓ %s 完成", title)
        except Exception as exc:  # noqa: BLE001
            log.error("  ✗ %s 失败：%s", title, exc)
            results[key] = {"error": str(exc)}

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "calc_date": base.calc_date(),
        "results": results,
        "charts": [c for r in results.values() if isinstance(r, dict) for c in r.get("charts", [])],
    }
    SUMMARY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
    log.info("分析结果已写入 %s", SUMMARY_FILE.name)
    return payload
