"""数据质量规则引擎（DQ01–DQ18）。

设计要点
--------
1. **规则即配置**：每条规则用 `条件表达式` 描述"什么算一条坏数据"，
   引擎负责在 ODS（清洗前）与 DWD（清洗后）两个层各跑一遍，
   同一套规则前后对比，才能证明清洗有效。
2. **五维评分**：完整性 / 唯一性 / 有效性 / 一致性 / 及时性。
   评分 = 1 - 该维度违规行数 / 总行数，按表行数加权，避免小表拉高整体分数。
3. **区分"规则检出"与"注入标准答案"**：生成数据时记录了每类问题注入了多少行，
   引擎检出数应当 ≥ 注入数（一行可能同时命中多条规则），这才能说明规则没漏。
4. **唯一性单独处理**：重复行不能用 WHERE 表达，改用 `总行数 - 去重行数` 计算，
   并天然做到"只算多余的那一条"，与注入数口径一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .. import db
from ..config import DATA_MANIFEST, MYSQL_DW_DB
from ..logger import get_logger

log = get_logger("quality")

# 五个质量维度与权重（权重体现业务重要性：完整与有效优先）
DIMENSIONS: Dict[str, float] = {
    "完整性": 0.25,
    "唯一性": 0.20,
    "有效性": 0.25,
    "一致性": 0.15,
    "及时性": 0.15,
}

# 各层表 → 唯一性判定主键
LAYER_TABLES: Dict[str, Dict[str, str]] = {
    "ods": {
        "ods_orders_raw": "order_id",
        "ods_order_items_raw": "item_id",
        "ods_customers_raw": "customer_id",
        "ods_products_raw": "sku_id",
    },
    "dwd": {
        "dwd_orders_clean": "order_id",
        "dwd_order_items_clean": "item_id",
        "dwd_customers_clean": "customer_id",
        "dwd_products_clean": "sku_id",
    },
}

STANDARD_STATUS = ("已付款", "制作中", "待配送", "已完成", "已退款", "已取消")
STANDARD_LEVEL = ("普通会员", "银卡会员", "金卡会员", "钻石会员", "企业客户")

# 数据时间边界（与 config.DATA_START/END 保持一致，这里写死便于 SQL 内联）
DATA_MIN = "2024-01-01 00:00:00"
DATA_MAX = "2026-11-19 23:59:59"


@dataclass(frozen=True)
class Rule:
    """一条质量规则。"""

    code: str
    name: str
    dimension: str
    layer_table: str          # 该规则作用的表（ODS 侧表名）
    desc: str
    fix_policy: str           # 清洗策略（报告里展示，说明"发现之后怎么办"）
    cond: Optional[str] = None          # WHERE 条件（不含 WHERE 关键字）
    unique_key: Optional[str] = None    # 唯一性规则：按此列判定重复
    dwd_cond: Optional[str] = None      # 清洗后表上的等价条件
    dwd_table: Optional[str] = None     # 清洗后表名（默认按层映射）


def _q(vals: Iterable[str]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in vals) + ")"


STATUS_IN = _q(STANDARD_STATUS)
LEVEL_IN = _q(STANDARD_LEVEL)

# --------------------------------------------------------------------------
# 18 条规则
# --------------------------------------------------------------------------
RULES: List[Rule] = [
    Rule("DQ01", "订单号重复", "唯一性", "ods_orders_raw",
         "同一订单号出现多行（重复导出/重复提交）",
         "按订单号去重，保留首条，其余计入「去重行数」",
         unique_key="order_id", dwd_table="dwd_orders_clean"),
    Rule("DQ02", "客户ID缺失", "完整性", "ods_orders_raw",
         "订单没有关联客户（游客下单/系统未落号）",
         "不丢单：客户置为 UNKNOWN 并打标，报表层单列「游客订单」",
         cond="customer_id IS NULL OR TRIM(customer_id) = ''",
         dwd_cond="customer_id IS NULL OR customer_id = 'UNKNOWN'",
         dwd_table="dwd_orders_clean"),
    Rule("DQ03", "下单时间格式不规范", "有效性", "ods_orders_raw",
         "时间被 Excel 转成斜杠/中文格式，或缺少秒",
         "多格式正则解析为 DATETIME；解析失败计入「不可修复」",
         cond=r"order_time NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$'",
         dwd_cond="order_time IS NULL", dwd_table="dwd_orders_clean"),
    Rule("DQ04", "订单金额异常", "有效性", "ods_orders_raw",
         "实付 ≤0 或与「原价-优惠+运费」不勾稽（被放大/篡改）",
         "判定为坏值，整单隔离到异常表，不参与经营口径",
         cond="CAST(payable_amount AS DECIMAL(14,2)) <= 0 "
              "OR ABS(CAST(payable_amount AS DECIMAL(14,2)) "
              "- (CAST(total_amount AS DECIMAL(14,2)) - CAST(discount_amount AS DECIMAL(14,2)) "
              "+ CAST(freight_amount AS DECIMAL(14,2)))) > 0.01",
         dwd_cond="payable_amount <= 0 "
                  "OR ABS(payable_amount - (total_amount - discount_amount + freight_amount)) > 0.01",
         dwd_table="dwd_orders_clean"),
    Rule("DQ05", "订单状态不规范", "有效性", "ods_orders_raw",
         "状态带空格、中英混写或别名（已完成 / COMPLETED / 已 完成）",
         "映射到标准枚举；无法映射的置为「未知」并打标",
         cond=f"status NOT IN {STATUS_IN}",
         dwd_cond=f"status NOT IN {STATUS_IN}", dwd_table="dwd_orders_clean"),
    Rule("DQ06", "城市名不规范", "有效性", "ods_customers_raw",
         "城市名带空格/全角空格，或出现「上海市市」这类重复后缀",
         "去空白 + 全角转半角 + 去重复后缀",
         cond="city IS NULL OR city <> TRIM(city) OR city LIKE '% %' "
              "OR city LIKE '%　%' OR city LIKE '%市市'",
         dwd_cond="city IS NULL OR city <> TRIM(city) OR city LIKE '% %' "
                  "OR city LIKE '%　%' OR city LIKE '%市市'",
         dwd_table="dwd_customers_clean"),
    Rule("DQ07", "手机号格式非法", "有效性", "ods_customers_raw",
         "位数不对或含字母",
         "校验 `^1[3-9]\\d{9}$`，非法置 NULL 并打标（不伪造号码）",
         cond="phone IS NOT NULL AND phone <> '' "
              "AND phone NOT REGEXP '^1[3-9][0-9]{9}$'",
         dwd_cond="phone IS NOT NULL AND phone <> '' "
                  "AND phone NOT REGEXP '^1[3-9][0-9]{9}$'",
         dwd_table="dwd_customers_clean"),
    Rule("DQ08", "业务时间越界", "及时性", "ods_orders_raw",
         "订单时间落在 1999 年或数据区间之外",
         "无法推断真值，整单隔离到异常表并留痕",
         cond="order_time REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
              f"AND (order_time < '{DATA_MIN}' OR order_time > '{DATA_MAX}')",
         dwd_cond=f"order_time < '{DATA_MIN}' OR order_time > '{DATA_MAX}'",
         dwd_table="dwd_orders_clean"),
    Rule("DQ09", "已完成订单缺配送时间", "一致性", "ods_orders_raw",
         "状态=已完成 却没有实际送达时间，状态与履约记录矛盾",
         "标记为「履约记录缺失」，配送时长分析时排除，不臆造时间",
         cond="status = '已完成' AND (delivery_time IS NULL OR TRIM(delivery_time) = '')",
         dwd_cond="status = '已完成' AND delivery_time IS NULL",
         dwd_table="dwd_orders_clean"),
    Rule("DQ10", "备注含乱码", "有效性", "ods_orders_raw",
         "跨系统编码问题产生的乱码/emoji/脱敏星号",
         "置为 NULL 并打标，保留「有备注」事实供文本分析",
         cond="remark LIKE '%??%' OR remark LIKE '%café%' OR remark LIKE '%囧%' "
              "OR remark LIKE '%****%'",
         dwd_cond="remark LIKE '%??%' OR remark LIKE '%café%' OR remark LIKE '%囧%' "
                  "OR remark LIKE '%****%'",
         dwd_table="dwd_orders_clean"),
    Rule("DQ11", "商品价格异常", "有效性", "ods_products_raw",
         "门市价为 0 或 99999 这类明显错误值",
         "用同品类中位数回填，并打上 REPRICE 标记",
         cond="CAST(list_price AS DECIMAL(12,2)) <= 0 "
              "OR CAST(list_price AS DECIMAL(12,2)) > 10000",
         dwd_cond="list_price <= 0 OR list_price > 10000",
         dwd_table="dwd_products_clean"),
    Rule("DQ12", "客户记录重复", "唯一性", "ods_customers_raw",
         "同一客户被导出多次（重复行）",
         "按客户编号去重，保留首条",
         unique_key="customer_id", dwd_table="dwd_customers_clean"),
    Rule("DQ13", "会员等级脏值", "有效性", "ods_customers_raw",
         "等级被写成别名/大小写/带空格（silver、 金卡会员 、Diamond）",
         "别名映射回标准 5 档；无法映射置「未知」",
         cond=f"member_level IS NULL OR member_level NOT IN {LEVEL_IN}",
         dwd_cond=f"member_level IS NULL OR member_level NOT IN {LEVEL_IN}",
         dwd_table="dwd_customers_clean"),
    Rule("DQ14", "明细行重复", "唯一性", "ods_order_items_raw",
         "同一明细编号出现多行",
         "按明细编号去重",
         unique_key="item_id", dwd_table="dwd_order_items_clean"),
    Rule("DQ15", "明细数量非正", "有效性", "ods_order_items_raw",
         "数量 ≤ 0（退货冲销未拆单）",
         "隔离到异常表，避免负数拉低销量口径",
         cond="CAST(quantity AS SIGNED) <= 0",
         dwd_cond="quantity <= 0", dwd_table="dwd_order_items_clean"),
    Rule("DQ16", "明细缺订单号", "完整性", "ods_order_items_raw",
         "明细无法归属任何订单（孤立明细）",
         "隔离到异常表，并由对账确认未污染事实表",
         cond="order_id IS NULL OR TRIM(order_id) = ''",
         dwd_cond="order_id IS NULL OR order_id = ''",
         dwd_table="dwd_order_items_clean"),
    Rule("DQ17", "明细单价为 0", "有效性", "ods_order_items_raw",
         "赠品未打标或价格缺失",
         "按商品维表门市价回填并打标；找不到商品则隔离",
         cond="CAST(unit_price AS DECIMAL(12,2)) <= 0",
         dwd_cond="unit_price <= 0", dwd_table="dwd_order_items_clean"),
    Rule("DQ18", "明细金额与数量×单价不一致", "一致性", "ods_order_items_raw",
         "行金额 ≠ 数量 × 单价（手工改价/拆分错误）",
         "按 数量×单价 重算行金额并打标，保证「行合=订单合」",
         cond="ABS(CAST(line_amount AS DECIMAL(14,2)) "
              "- CAST(quantity AS SIGNED) * CAST(unit_price AS DECIMAL(12,2))) > 0.01",
         dwd_cond="ABS(line_amount - quantity * unit_price) > 0.01",
         dwd_table="dwd_order_items_clean"),
]

# ODS 表 → DWD 表映射（默认值）
ODS_TO_DWD: Dict[str, str] = {
    "ods_orders_raw": "dwd_orders_clean",
    "ods_order_items_raw": "dwd_order_items_clean",
    "ods_customers_raw": "dwd_customers_clean",
    "ods_products_raw": "dwd_products_clean",
}


@dataclass
class RuleResult:
    code: str
    name: str
    dimension: str
    table: str
    detected: int
    total: int
    expected: Optional[int] = None
    desc: str = ""
    fix_policy: str = ""

    @property
    def rate(self) -> float:
        return self.detected / self.total if self.total else 0.0


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------
def _scalar(sql: str) -> int:
    row = db.fetch_one(sql, database=MYSQL_DW_DB)
    return int(list(row.values())[0] or 0) if row else 0


def _table_count(layer: str, table: str) -> int:
    return _scalar(f"SELECT COUNT(*) FROM bloom_dw.{table}")


def load_injection_manifest() -> Dict[str, int]:
    """读取数据工厂记录的标准答案：每类规则注入了多少行。"""
    if not DATA_MANIFEST.exists():
        return {}
    data = json.loads(DATA_MANIFEST.read_text(encoding="utf-8"))
    out: Dict[str, int] = {}
    for item in data.get("dq_injection", []):
        out[item["rule"]] = out.get(item["rule"], 0) + int(item["affected"])
    return out


def run_rules(layer: str = "ods") -> tuple[List[RuleResult], Dict[str, int]]:
    """执行全部规则，返回（逐规则结果, 表行数）。"""
    tables = LAYER_TABLES[layer]
    counts = {t: _table_count(layer, t) for t in tables}
    manifest = load_injection_manifest() if layer == "ods" else {}
    results: List[RuleResult] = []

    for rule in RULES:
        ods_table = rule.layer_table
        table = ods_table if layer == "ods" else (rule.dwd_table or ODS_TO_DWD[ods_table])
        if table not in counts:
            continue
        cond = rule.cond if layer == "ods" else rule.dwd_cond

        if rule.unique_key:
            key = rule.unique_key
            detected = _scalar(
                f"SELECT COUNT(*) - COUNT(DISTINCT `{key}`) FROM bloom_dw.{table}"
            )
        elif cond:
            detected = _scalar(f"SELECT COUNT(*) FROM bloom_dw.{table} WHERE {cond}")
        else:
            continue

        results.append(
            RuleResult(
                code=rule.code, name=rule.name, dimension=rule.dimension,
                table=table, detected=detected, total=counts[table],
                expected=manifest.get(rule.code),
                desc=rule.desc, fix_policy=rule.fix_policy,
            )
        )
    return results, counts


def score_dimensions(results: List[RuleResult], counts: Dict[str, int]) -> Dict[str, float]:
    """按维度计算质量得分。

    维度违规行数用"该维度下所有规则命中的行数之和"近似（可能重复计数同一行），
    这是业界常见的简化口径：宁可报得严一点，也不要漏报。
    唯一性维度用 `总行数 - 去重行数` 精确计算，不与其它维度混算。
    """
    total_rows = sum(counts.values())
    scores: Dict[str, float] = {}

    for dim in DIMENSIONS:
        dim_rules = [r for r in results if r.dimension == dim]
        if not dim_rules:
            scores[dim] = 1.0
            continue
        # 每个表取该维度规则的检出之和，再跨表汇总
        by_table: Dict[str, int] = {}
        for r in dim_rules:
            by_table[r.table] = by_table.get(r.table, 0) + r.detected
        violations = sum(by_table.values())
        # 表级封顶：违规行数不可能超过该表总行数
        capped = 0
        for table, v in by_table.items():
            capped += min(v, counts.get(table, v))
        scores[dim] = round(max(0.0, 1 - capped / total_rows), 4) if total_rows else 1.0

    return scores


def overall_score(scores: Dict[str, float]) -> float:
    return round(sum(scores[d] * w for d, w in DIMENSIONS.items()), 4)


def grade(score: float) -> str:
    if score >= 0.995:
        return "优（A）"
    if score >= 0.98:
        return "良（B）"
    if score >= 0.95:
        return "中（C）"
    return "差（D）"


def evaluate(layer: str = "ods") -> Dict[str, Any]:
    """跑一整层（ods / dwd），返回报告所需全部结构化数据。"""
    results, counts = run_rules(layer)
    scores = score_dimensions(results, counts)
    return {
        "layer": layer,
        "tables": counts,
        "total_rows": sum(counts.values()),
        "rules": [r.__dict__ | {"rate": r.rate} for r in results],
        "dimensions": scores,
        "overall": overall_score(scores),
        "grade": grade(overall_score(scores)),
    }


def sample_bad_rows(code: str, limit: int = 3) -> List[Dict[str, Any]]:
    """取某条规则的坏数据样例（报告里给人看）。"""
    rule = next((r for r in RULES if r.code == code), None)
    if not rule or not rule.cond:
        return []
    sql = f"SELECT * FROM bloom_dw.{rule.layer_table} WHERE {rule.cond} LIMIT {limit}"
    try:
        return db.fetch_all(sql, database=MYSQL_DW_DB)
    except Exception:  # noqa: BLE001
        return []
