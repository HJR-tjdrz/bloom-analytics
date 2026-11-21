"""数据库初始化：建库、建专用账号、建表、建索引与存储过程。

为什么用 Python 而不是 .bat / .ps1？
------------------------------------
1. Windows 命令行对中文路径和编码极不友好，Python 一次搞定；
2. root 密码用 getpass 读取，不落盘、不进命令历史；
3. 生成的专用账号密码随机且只写进 .env，代码里永远看不到明文。

用法（推荐直接双击 00_一键初始化数据库.bat）：
    python 00_init_database.py
"""
from __future__ import annotations

import getpass
import secrets
import string
import sys
from pathlib import Path

try:
    import pymysql
except ImportError:  # pragma: no cover
    print("缺少依赖 pymysql，请先双击项目根目录的 00_安装依赖.bat")
    sys.exit(1)

SQL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SQL_DIR.parent
ENV_PATH = PROJECT_ROOT / ".env"

HOST = "127.0.0.1"
PORT = 3306
ROOT_USER = "root"
APP_USER = "bloom_app"
APP_DB_OLTP = "bloom_oltp"
APP_DB_DW = "bloom_dw"

# 按执行顺序排列；01 需要注入密码，单独处理
SQL_FILES = [
    "02_OLTP业务库建表.sql",
    "03_数仓分层建表.sql",
    "04_索引视图与优化.sql",
    "05_存储过程与函数.sql",
]

BANNER = """
============================================================
  花店全渠道经营数据分析平台 · 数据库初始化
  将创建：bloom_oltp（业务库）、bloom_dw（数仓）
          专用账号 bloom_app（非 root，最小权限）
============================================================
"""


def _is_comment_only(stmt: str) -> bool:
    """判断语句是否只由注释/空行构成（这类语句送到 MySQL 会报 Query was empty）。"""
    for line in stmt.splitlines():
        s = line.strip()
        if not s or s.startswith("--") or s.startswith("#"):
            continue
        return False
    return True


def split_statements(sql_text: str) -> list[str]:
    """按 DELIMITER 切分 SQL 语句（兼容存储过程里的分号）。"""
    statements: list[str] = []
    buf: list[str] = []
    delimiter = ";"

    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER"):
            parts = stripped.split()
            if "".join(buf).strip():
                statements.append("".join(buf).strip())
            buf = []
            delimiter = parts[1] if len(parts) > 1 else ";"
            continue
        buf.append(line + "\n")
        joined = "".join(buf)
        if joined.rstrip().endswith(delimiter):
            stmt = joined.rstrip()[: -len(delimiter)].strip()
            if stmt:
                statements.append(stmt)
            buf = []

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if not _is_comment_only(s)]


def run_sql_file(conn, path: Path, label: str) -> list[dict]:
    """执行一个 SQL 文件；单条失败不中断，收集后统一报告。

    为什么要"失败不中断"？
    建表脚本里前面的失败往往只是后面失败的连锁反应，
    一次性把所有失败列出来，比"改一条跑一次"快得多。
    """
    text = path.read_text(encoding="utf-8")
    statements = split_statements(text)
    print(f"  → {label:<22} {len(statements):>2} 条语句 … ", end="", flush=True)
    failures: list[dict] = []
    with conn.cursor() as cur:
        for idx, stmt in enumerate(statements, 1):
            try:
                cur.execute(stmt)
                while cur.nextset():
                    pass
            except Exception as exc:  # noqa: BLE001
                failures.append({"file": label, "idx": idx, "error": str(exc), "stmt": stmt})
    conn.commit()
    if failures:
        print(f"失败 {len(failures)} 条")
    else:
        print("完成")
    return failures


def report_failures(failures: list[dict]) -> None:
    dump_dir = PROJECT_ROOT / "logs"
    dump_dir.mkdir(exist_ok=True)
    print("\n" + "=" * 70)
    print(f"  共 {len(failures)} 条语句执行失败")
    print("=" * 70)
    for i, f in enumerate(failures, 1):
        path = dump_dir / f"failed_statement_{i:02d}.sql"
        path.write_text(
            f"-- 文件：{f['file']} 第 {f['idx']} 条语句\n-- 错误：{f['error']}\n\n{f['stmt']}\n",
            encoding="utf-8",
        )
        print(f"\n[{i}] {f['file']} 第 {f['idx']} 条")
        print(f"    错误：{f['error']}")
        head = " ".join(f["stmt"].strip().splitlines()[-3:])[:180]
        print(f"    语句：…{head}")
    print(f"\n出错语句已逐条保存到：{dump_dir}")
    print("提示：把上面这段整体复制发给我即可定位；脚本幂等，修好后可直接重跑。")


def load_or_create_password() -> tuple[str, bool]:
    """复用已有 .env 里的密码，或生成一个新的强密码。"""
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MYSQL_PASSWORD="):
                pwd = line.split("=", 1)[1].strip()
                if pwd and "请替换" not in pwd:
                    return pwd, False
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(24)), True


def write_env(password: str) -> None:
    content = f"""# ============================================================
#  由 00_init_database.py 自动生成，请勿提交到 Git（已在 .gitignore 中）
#  生成时间：{__import__("datetime").datetime.now():%Y-%m-%d %H:%M:%S}
# ============================================================
MYSQL_HOST={HOST}
MYSQL_PORT={PORT}
MYSQL_USER={APP_USER}
MYSQL_PASSWORD={password}
MYSQL_OLTP_DB={APP_DB_OLTP}
MYSQL_DW_DB={APP_DB_DW}

BLOOM_SEED=20241118
BLOOM_DATA_START=2024-01-01
BLOOM_DATA_END=2026-11-18
"""
    ENV_PATH.write_text(content, encoding="utf-8")


def has_existing_data(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='orders'", (APP_DB_OLTP,)
            )
            if cur.fetchone()[0] == 0:
                return False
            cur.execute(f"SELECT COUNT(*) FROM {APP_DB_OLTP}.orders")
            return cur.fetchone()[0] > 0
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    print(BANNER)
    password_root = getpass.getpass("请输入本机 MySQL 的 root 密码（输入时不显示）：")
    if not password_root:
        print("未输入密码，已退出。")
        return

    print(f"\n正在连接 MySQL {HOST}:{PORT} …")
    try:
        conn = pymysql.connect(
            host=HOST, port=PORT, user=ROOT_USER, password=password_root,
            charset="utf8mb4", autocommit=False,
        )
    except pymysql.MySQLError as exc:
        print(f"\n[错误] 无法用 root 登录：{exc}")
        print("请确认：1) MySQL 服务已启动；2) root 密码正确；3) 端口为 3306。")
        return

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
        print(f"连接成功，MySQL 版本 {version}\n")

        if has_existing_data(conn):
            print("⚠ 检测到业务库已有数据，继续将删除并重建全部表结构。")
            answer = input("确认继续？输入 y 回车继续，其他任意键取消：").strip().lower()
            if answer != "y":
                print("已取消，未做任何修改。")
                return
            print()

        app_password, is_new = load_or_create_password()

        # ---- 1. 建库 + 建账号（密码动态注入）----
        init_sql = (SQL_DIR / "01_初始化库与账号.sql").read_text(encoding="utf-8")
        init_sql = init_sql.replace("__APP_PASSWORD__", app_password)
        print("  → 01 建库与专用账号 … ", end="", flush=True)
        with conn.cursor() as cur:
            for stmt in split_statements(init_sql):
                cur.execute(stmt)
                while cur.nextset():
                    pass
        conn.commit()
        print("完成")

        # ---- 2. 立刻写 .env ----
        # 放在建表之前：万一后面 DDL 失败，账号密码也不会丢，避免"账号建了但不知道密码"
        write_env(app_password)
        print(f"  → 生成配置文件 .env（专用账号密码{'随机新生成' if is_new else '沿用原有'}）")

        # ---- 3. 建表 / 索引 / 视图 / 存储过程（失败不中断，最后统一报告）----
        all_failures: list[dict] = []
        for name in SQL_FILES:
            path = SQL_DIR / name
            if not path.exists():
                print(f"  ! 跳过（文件不存在）：{name}")
                continue
            all_failures += run_sql_file(conn, path, name)

        # ---- 4. 自检 ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_SCHEMA, COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA IN (%s, %s) GROUP BY TABLE_SCHEMA",
                (APP_DB_OLTP, APP_DB_DW),
            )
            stats = {row[0]: row[1] for row in cur.fetchall()}
        print(f"  → 自检：{APP_DB_OLTP} 共 {stats.get(APP_DB_OLTP, 0)} 张表，"
              f"{APP_DB_DW} 共 {stats.get(APP_DB_DW, 0)} 张表")

        if all_failures:
            report_failures(all_failures)
            return

    finally:
        conn.close()

    print("""
============================================================
  初始化完成 ✓

  下一步（按顺序）：
    1) 03_数据工厂_20261120\\01_generate_data.bat   生成模拟数据
    2) 04_MySQL数仓_20261120\\10_load_data.bat      装载业务库与ODS
    3) 04_MySQL数仓_20261120\\11_build_dw.bat       构建数仓（维度+事实+汇总）
    4) 04_MySQL数仓_20261120\\12_validate.bat       数据验证与对账
============================================================""")


if __name__ == "__main__":
    main()
