"""标签体系：建表、计算、覆盖率与分布统计。

标签不是"打上就完事"，还要回答三个治理问题：
1. **有没有值**：覆盖率（非空率）低于 90% 的标签，不能用于精准圈选；
2. **分布是否合理**：比如"节日型客户"如果占 99%，说明阈值定错了；
3. **口径是否唯一**：每个标签在 dim_tag 里只有一条口径说明，改规则必须登记。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .. import db
from ..config import GOVERNANCE_DIR, MYSQL_DW_DB
from ..logger import get_logger

log = get_logger("tags")

TAG_SQL = GOVERNANCE_DIR / "02_标签体系_建表与计算.sql"

# 标签编码 → 宽表列名（与 dim_tag 一一对应）
TAG_COLUMNS: Dict[str, Dict[str, str]] = {
    "客户": {
        "T01": "customer_type", "T02": "city_tier", "T03": "member_level",
        "T04": "tenure_year", "T05": "lifecycle_stage", "T06": "value_tier",
        "T07": "category_pref", "T08": "price_pref", "T09": "channel_pref",
        "T10": "festival_type", "T11": "refund_sensitive", "T12": "coupon_lover",
    },
    "商品": {
        "T13": "product_type", "T14": "price_band", "T15": "abc_class",
        "T16": "profit_tier", "T17": "seasonal_flag", "T18": "repurchase_flag",
    },
}
TABLE_OF = {"客户": "ads_customer_tags", "商品": "ads_product_tags"}


def build_tags() -> Dict[str, int]:
    """执行建表与标签计算 SQL，返回两张宽表的行数。"""
    db.run_sql_file(TAG_SQL, database=MYSQL_DW_DB)
    out = {}
    for tag_type, table in TABLE_OF.items():
        row = db.fetch_one(f"SELECT COUNT(*) AS c FROM {table}", database=MYSQL_DW_DB)
        out[table] = int(row["c"]) if row else 0
        log.info("  ✓ %-24s %6d 行（%s标签）", table, out[table], tag_type)
    return out


def coverage() -> List[Dict[str, Any]]:
    """统计每个标签的覆盖率与取值分布。"""
    catalog = db.fetch_all(
        "SELECT tag_code, tag_name, tag_type, tag_group, value_domain, rule_desc "
        "FROM dim_tag ORDER BY tag_code", database=MYSQL_DW_DB)
    results: List[Dict[str, Any]] = []

    for tag in catalog:
        tag_type = tag["tag_type"]
        column = TAG_COLUMNS.get(tag_type, {}).get(tag["tag_code"])
        table = TABLE_OF.get(tag_type)
        if not column or not table:
            continue
        rows = db.fetch_all(
            f"SELECT IFNULL(`{column}`, '(空)') AS val, COUNT(*) AS c "
            f"FROM `{table}` GROUP BY 1 ORDER BY c DESC", database=MYSQL_DW_DB)
        total = sum(int(r["c"]) for r in rows) or 1
        non_null = sum(int(r["c"]) for r in rows if r["val"] != "(空)")
        # 只展示前 6 个取值，避免长尾把表格撑爆
        top = rows[:6]
        results.append({
            "code": tag["tag_code"],
            "name": tag["tag_name"],
            "type": tag_type,
            "group": tag["tag_group"],
            "value_domain": tag["value_domain"] or "",
            "rule": tag["rule_desc"] or "",
            "total": total,
            "coverage": round(non_null / total, 4),
            "distinct_values": len(rows),
            "top_values": [{"value": r["val"], "count": int(r["c"]),
                            "ratio": round(int(r["c"]) / total, 4)} for r in top],
        })
    log.info("标签覆盖率统计完成：%d 个标签", len(results))
    return results
