"""MySQL 访问层：连接、批量装载、契约校验。

为什么不用 ORM？
---------------
本项目要展示的是「手写 SQL + 索引优化 + 执行计划」的能力，
ORM 会把 SQL 藏起来，反而不利于简历表达。这里用 PyMySQL 直连，
保留完整的 SQL 控制权，同时用封装保证连接与批量写入的规范性。
"""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

import pymysql
from pymysql.cursors import DictCursor

from .config import (
    MYSQL_CHARSET,
    MYSQL_DW_DB,
    MYSQL_HOST,
    MYSQL_OLTP_DB,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
)
from .logger import get_logger

log = get_logger("db")


class DBError(RuntimeError):
    """数据库层异常，附带可执行的排查建议。"""


def _check_env() -> None:
    if not MYSQL_PASSWORD:
        raise DBError(
            "未找到数据库密码：请先双击 04_MySQL数仓_20261120/00_一键初始化数据库.bat，"
            "或在项目根目录创建 .env（可参考 .env.example）。"
        )


@contextmanager
def connect(database: Optional[str] = None, autocommit: bool = False) -> Iterator[pymysql.connections.Connection]:
    """获取连接（上下文管理器，自动提交/回滚/关闭）。"""
    _check_env()
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=database,
            charset=MYSQL_CHARSET,
            cursorclass=DictCursor,
            autocommit=autocommit,
            connect_timeout=10,
        )
    except pymysql.MySQLError as exc:  # pragma: no cover - 环境相关
        raise DBError(
            f"连接 MySQL 失败（{MYSQL_USER}@{MYSQL_HOST}:{MYSQL_PORT}）：{exc}\n"
            "排查建议：1) MySQL 服务是否启动；2) .env 中的账号密码是否正确；"
            "3) 是否已执行初始化脚本创建 bloom_oltp / bloom_dw。"
        ) from exc

    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def oltp_conn() -> Any:
    return connect(MYSQL_OLTP_DB)


def dw_conn() -> Any:
    return connect(MYSQL_DW_DB)


# --------------------------------------------------------------------------
# 查询与执行
# --------------------------------------------------------------------------
def fetch_all(sql: str, params: Optional[Sequence[Any]] = None, database: Optional[str] = None) -> List[Dict[str, Any]]:
    with connect(database) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def fetch_one(sql: str, params: Optional[Sequence[Any]] = None, database: Optional[str] = None) -> Optional[Dict[str, Any]]:
    rows = fetch_all(sql, params, database)
    return rows[0] if rows else None


def execute(sql: str, params: Optional[Sequence[Any]] = None, database: Optional[str] = None) -> int:
    with connect(database) as conn:
        with conn.cursor() as cur:
            return cur.execute(sql, params)


def run_sql_file(path: Path, database: Optional[str] = None) -> None:
    """执行单个 .sql 文件（按分号切分，忽略注释）。

    约束：文件中不允许出现 `DELIMITER`（存储过程请用 mysql 客户端执行，
    见 00_一键初始化数据库.ps1 的处理方式）。
    逐条统计耗时，慢语句（>5s）单独告警——这是发现"缺索引"最直接的手段。
    """
    content = path.read_text(encoding="utf-8")
    statements = split_sql(content)
    log.info("执行 SQL 文件：%s（%d 条语句）", path.name, len(statements))
    total_start = time.time()
    with connect(database) as conn:
        with conn.cursor() as cur:
            for idx, stmt in enumerate(statements, 1):
                t0 = time.time()
                try:
                    cur.execute(stmt)
                    # 多结果集（CALL）时要读完，否则后续语句报 "Commands out of sync"
                    while cur.nextset():
                        pass
                except pymysql.MySQLError as exc:
                    raise DBError(
                        f"{path.name} 第 {idx} 条语句执行失败：\n{stmt[:400]}\n→ {exc}"
                    ) from exc
                cost = time.time() - t0
                if cost >= 5:
                    log.warning(
                        "  ⏱ 第 %d 条语句耗时 %.1f 秒：%s",
                        idx, cost, " ".join(stmt.strip().splitlines()[:1])[:70],
                    )
    log.info("  ✓ %s 执行完毕，总耗时 %.1f 秒", path.name, time.time() - total_start)


_COMMENT_RE = re.compile(r"(--[^\n]*\n)|(/\*.*?\*/)", re.S)


def split_sql(sql_text: str) -> List[str]:
    """把 SQL 脚本切成可单条执行的语句。

    去掉行注释/块注释后按 `;` 切分，并过滤掉纯注释片段
    （只含注释的语句发给 MySQL 会报 "Query was empty"）。
    """
    cleaned = _COMMENT_RE.sub("\n", sql_text)
    parts = [p.strip() for p in cleaned.split(";")]
    return [p for p in parts if p and not _is_comment_only(p)]


def _is_comment_only(stmt: str) -> bool:
    for line in stmt.splitlines():
        s = line.strip()
        if not s or s.startswith("--") or s.startswith("#"):
            continue
        return False
    return True


# --------------------------------------------------------------------------
# 批量装载
# --------------------------------------------------------------------------
def table_columns(conn: Any, database: str, table: str) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (database, table),
        )
        return [row["COLUMN_NAME"] for row in cur.fetchall()]


def assert_schema_matches(conn: Any, database: str, table: str, columns: Sequence[str]) -> None:
    """校验 CSV 表头与数据库表结构一致（数据契约，防止字段错位）。"""
    db_cols = table_columns(conn, database, table)
    if not db_cols:
        raise DBError(f"表 {database}.{table} 不存在，请先执行建表 SQL。")
    missing = [c for c in columns if c not in db_cols]
    if missing:
        raise DBError(
            f"{database}.{table} 缺少字段 {missing}；\n"
            f"数据库实际字段：{db_cols}"
        )


def bulk_insert(
    conn: Any,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    batch_size: int = 2000,
) -> int:
    """批量 INSERT，返回写入行数。

    PyMySQL 的 executemany 会把 VALUES 片段拼成多行插入，
    比逐条 INSERT 快一个数量级（10 万行量级下差异非常明显）。
    """
    cols = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO `{table}` ({cols}) VALUES ({placeholders})"

    total = 0
    buf: List[Sequence[Any]] = []
    with conn.cursor() as cur:
        for row in rows:
            buf.append(row)
            if len(buf) >= batch_size:
                cur.executemany(sql, buf)
                total += len(buf)
                buf.clear()
        if buf:
            cur.executemany(sql, buf)
            total += len(buf)
    return total


def truncate(conn: Any, tables: Sequence[str]) -> None:
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for t in tables:
            cur.execute(f"TRUNCATE TABLE `{t}`")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")


def count_rows(conn: Any, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM `{table}`")
        return int(cur.fetchone()["c"])


def read_df(sql: str, params: Optional[Sequence[Any]] = None,
            database: Optional[str] = None) -> Any:
    """查询并返回 DataFrame。

    **不要用 `pd.read_sql(sql, pymysql_conn)`**：本项目连接使用 DictCursor，
    pandas 拿到的是一堆 dict 而不是 tuple，会得到错乱的结果
    （实测会把列名当成数据行）。这里先取 dict 列表再构造 DataFrame，行为确定。
    """
    import pandas as pd

    rows = fetch_all(sql, params, database)
    return pd.DataFrame(rows)
