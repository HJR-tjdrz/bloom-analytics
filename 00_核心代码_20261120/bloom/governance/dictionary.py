"""数据字典：从 information_schema 自动生成，杜绝"文档和库不一致"。

为什么不用手工维护的 Excel 字典？
--------------------------------
手工字典 100% 会过期——加个字段没人更新，三个月后就是错误文档。
自动生成的好处是：**永远和数据库一致**，还能顺带带上行数、注释、默认值。
真正需要人写的只有"业务解释"（表注释/字段注释），这些在建表时就写好了。

输出：Markdown（适合 Git 阅读）+ Excel（适合发给业务方）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .. import db
from ..config import MYSQL_DW_DB, MYSQL_OLTP_DB
from ..logger import get_logger

log = get_logger("dictionary")

DBS = [MYSQL_OLTP_DB, MYSQL_DW_DB]
DB_TITLES = {MYSQL_OLTP_DB: "OLTP 业务库", MYSQL_DW_DB: "OLAP 数仓"}


def collect() -> List[Dict[str, Any]]:
    """汇总两个库里所有表的字段信息。"""
    rows: List[Dict[str, Any]] = []
    for database in DBS:
        tables = db.fetch_all(
            """
            SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS, ENGINE
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """,
            (database,), database=MYSQL_DW_DB,
        )
        for t in tables:
            cols = db.fetch_all(
                """
                SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT,
                       COLUMN_KEY, COLUMN_COMMENT, ORDINAL_POSITION
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (database, t["TABLE_NAME"]), database=MYSQL_DW_DB,
            )
            for c in cols:
                rows.append({
                    "db": database,
                    "db_title": DB_TITLES.get(database, database),
                    "table": t["TABLE_NAME"],
                    "table_comment": t["TABLE_COMMENT"] or "",
                    "table_rows": int(t["TABLE_ROWS"] or 0),
                    "column": c["COLUMN_NAME"],
                    "type": c["COLUMN_TYPE"],
                    "nullable": c["IS_NULLABLE"],
                    "default": c["COLUMN_DEFAULT"],
                    "key": c["COLUMN_KEY"],
                    "comment": c["COLUMN_COMMENT"] or "",
                })
    log.info("采集数据字典：%d 个字段", len(rows))
    return rows


def render_markdown(rows: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# 数据字典（自动生成）\n")
    lines.append(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}｜"
                 f"来源：`information_schema` 实时读取｜字段总数：**{len(rows)}**\n")
    lines.append("> 说明：本文件由 `bloom.governance.dictionary` 自动生成，"
                 "表/字段注释来自建表 DDL，**与数据库永远一致**，请勿手工修改。\n")

    for database in DBS:
        db_rows = [r for r in rows if r["db"] == database]
        tables = sorted({r["table"] for r in db_rows})
        lines.append(f"\n## {DB_TITLES.get(database, database)} `{database}`（{len(tables)} 张表）\n")
        for table in tables:
            t_rows = [r for r in db_rows if r["table"] == table]
            first = t_rows[0]
            lines.append(f"\n### `{table}` — {first['table_comment']}\n")
            lines.append(f"字段 {len(t_rows)} 个｜约 {first['table_rows']:,} 行\n")
            lines.append("| # | 字段 | 类型 | 空 | 键 | 默认值 | 说明 |")
            lines.append("| ---: | --- | --- | --- | --- | --- | --- |")
            for i, r in enumerate(t_rows, start=1):
                default = "" if r["default"] is None else str(r["default"])
                lines.append(
                    f"| {i} | `{r['column']}` | {r['type']} | "
                    f"{'是' if r['nullable'] == 'YES' else '否'} | {r['key']} | {default} | "
                    f"{r['comment']} |"
                )
    return "\n".join(lines) + "\n"


def render_excel(rows: List[Dict[str, Any]], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "数据字典"

    headers = ["库", "表名", "表说明", "字段名", "类型", "可空", "键", "默认值", "字段说明", "表行数"]
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

    for r in rows:
        ws.append([
            r["db_title"], r["table"], r["table_comment"], r["column"], r["type"],
            "是" if r["nullable"] == "YES" else "否", r["key"],
            "" if r["default"] is None else str(r["default"]), r["comment"], r["table_rows"],
        ])

    # 斑马纹 + 边框
    zebra = PatternFill("solid", fgColor="F2F5FA")
    for i, row in enumerate(ws.iter_rows(min_row=2, max_row=ws.max_row), start=1):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=(cell.column == 9))
            if i % 2 == 0:
                cell.fill = zebra

    widths = [16, 22, 30, 22, 20, 6, 6, 14, 42, 10]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # 第二张表：表清单
    ws2 = wb.create_sheet("表清单")
    ws2.append(["库", "表名", "表说明", "字段数", "约行数"])
    for cell in ws2[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
    seen = {}
    for r in rows:
        key = (r["db_title"], r["table"])
        if key not in seen:
            seen[key] = {"cols": 0, "rows": r["table_rows"], "comment": r["table_comment"]}
        seen[key]["cols"] += 1
    for (db_title, table), info in sorted(seen.items()):
        ws2.append([db_title, table, info["comment"], info["cols"], info["rows"]])
    for idx, width in enumerate([16, 24, 40, 10, 12], start=1):
        ws2.column_dimensions[get_column_letter(idx)].width = width
    ws2.freeze_panes = "A2"

    wb.save(path)
    log.info("写出数据字典 Excel：%s", path.name)
