"""数仓构建与验证：执行 SQL 脚本、输出对账报告。"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd

from ..config import DW_DIR, MYSQL_DW_DB
from ..logger import get_logger
from .. import db

log = get_logger("dw")


def build_dw() -> None:
    """执行 06_构建数仓.sql（维度→事实→汇总→应用）。"""
    sql_file: Path = DW_DIR / "06_构建数仓.sql"
    if not sql_file.exists():
        raise FileNotFoundError(f"未找到数仓构建脚本：{sql_file}")
    start = time.time()
    db.run_sql_file(sql_file, database=MYSQL_DW_DB)
    log.info("数仓构建完成，耗时 %.1f 秒", time.time() - start)


# --------------------------------------------------------------------------
# 校验：解析 07 文件里的 @check 标记，逐条执行并打印
# --------------------------------------------------------------------------
CHECK_MARK = re.compile(r"^--\s*@check:\s*(.+)$")


def parse_checks(path: Path) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    title: str | None = None
    buf: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = CHECK_MARK.match(line.strip())
        if m:
            if title and buf:
                blocks.append((title, "\n".join(buf)))
            title, buf = m.group(1).strip(), []
        elif title is not None:
            buf.append(line)
    if title and buf:
        blocks.append((title, "\n".join(buf)))
    return blocks


def run_checks() -> bool:
    """执行全部检查项，返回是否全部通过。

    判定分两类：
    - **硬性断言**（必须通过，否则数据不可用）：金额对账、订单数对账、
      维度完整性、SCD2 唯一性、数据新鲜度；
    - **信息项**（只展示不判定）：行数总览、ODS 脏数据体检、月度 KPI 预览。
      其中"脏数据体检"的通过条件是"脏数据确实存在"，
      因为这是后续清洗演示的输入，全 0 反而说明注入没生效。
    """
    path = DW_DIR / "07_验证与对账.sql"
    blocks = parse_checks(path)
    log.info("开始执行 %d 项数据验证 …", len(blocks))
    ok = True
    results: list[tuple[str, bool | None, str]] = []

    with db.connect(MYSQL_DW_DB) as conn:
        for title, sql in blocks:
            print()
            print("=" * 78)
            print(f"  {title}")
            print("=" * 78)
            statements = db.split_sql(sql)
            last_df = None
            for stmt in statements:
                with conn.cursor() as cur:
                    cur.execute(stmt)
                    rows = cur.fetchall()
                    if not rows:
                        print("  （无数据）")
                        continue
                    df = pd.DataFrame(rows)
                    print(df.to_string(index=False))
                    last_df = df

            if last_df is None:
                continue
            passed, reason = _judge(title, last_df)
            if passed is None:
                results.append((title, None, "信息项"))
            elif passed:
                print("  ✅ 通过")
                results.append((title, True, reason))
            else:
                print(f"  ❌ 未通过：{reason}")
                results.append((title, False, reason))
                ok = False

    print()
    print("=" * 78)
    print("  验证汇总")
    print("=" * 78)
    for title, passed, reason in results:
        flag = "✅ 通过  " if passed else ("ℹ️  信息项" if passed is None else "❌ 未通过")
        print(f"  {flag}  {title}")
    print("-" * 78)
    print("  结论：" + ("全部硬性校验通过 ✓" if ok else "存在未通过的硬性校验 ✗"))
    print("=" * 78)
    return ok


def _judge(title: str, df: pd.DataFrame) -> tuple[bool | None, str]:
    """对单个检查结果做判定，返回 (是否通过, 说明)；None 表示信息项。"""
    r = df.iloc[0]

    if "金额对账" in title:
        diff = abs(float(r.get("差额", 0) or 0))
        return diff < 0.005, f"业务库与事实表实付差额 {diff:.2f}（应为 0.00）"

    if "订单数对账" in title:
        same_orders = int(r["业务库订单数"]) == int(r["事实表订单数"])
        same_items = int(r["业务库明细数"]) == int(r["事实表明细数"])
        return same_orders and same_items, "订单数与明细数应分别一一对应"

    if "维度完整性" in title:
        cols = [c for c in df.columns if "缺失" in str(c)]
        bad = {c: int(r[c]) for c in cols if int(r[c]) != 0}
        return not bad, f"存在维度孤儿行：{bad}" if bad else "5 个维度键全部命中，无孤儿行"

    if "SCD2" in title:
        # 客户数（去重）× 当前版本数 必须一一对应；版本总行数会大于客户数（拉链正常现象）
        same = int(r["客户数"]) == int(r["当前版本数"])
        return same, "每个客户必须有且仅有一个 is_current=1 的版本"

    if "数据新鲜度" in title:
        same = str(r["业务库最新订单时间"]) == str(r["事实表最新订单时间"])
        return same, "业务库与事实表的最大下单时间应一致"

    if "脏数据体检" in title:
        dirt = sum(int(r[c]) for c in df.columns if c != "原始行数")
        return dirt > 0, f"ODS 检出 {dirt} 处质量问题（供清洗演示，应大于 0）"

    return None, "信息项"
