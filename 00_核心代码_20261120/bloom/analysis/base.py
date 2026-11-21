"""分析层公共设施：图表风格、Markdown 渲染、结果落库。

为什么统一在这里管？
------------------
8 个分析模块如果各自 `plt.rcParams` 各画一套颜色，报告会像拼贴画。
这里统一：字体、配色、网格、留白、图片尺寸，保证十几张图视觉一致。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..logger import get_logger

log = get_logger("analysis")

ANALYSIS_DIR = PROJECT_ROOT / "07_分析与建模_20261121"
CHART_DIR = ANALYSIS_DIR / "charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)

# ---- 统一视觉 ----
PALETTE = {
    "blue": "#4C6FFF", "green": "#2BC48A", "orange": "#F5A524", "red": "#F04E4E",
    "purple": "#8B5CF6", "teal": "#14B8A6", "pink": "#EC4899", "gray": "#8A94A6",
    "navy": "#1E3A8A", "lime": "#84CC16",
}
SERIES = [PALETTE[k] for k in ("blue", "green", "orange", "purple", "teal", "pink", "red", "gray")]

plt.rcParams.update({
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS"],
    "axes.unicode_minus": False,
    "figure.dpi": 120,
    "savefig.dpi": 150,
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linestyle": "--",
    "legend.frameon": False,
    "figure.facecolor": "white",
})


def save(fig, name: str) -> str:
    """保存图表，返回相对路径（写进 Markdown 用）。"""
    path = CHART_DIR / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("  📊 %s", path.name)
    return f"charts/{path.name}"


def md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]],
             align: str = "---") -> List[str]:
    """生成 Markdown 表格（列表形式，便于拼接）。"""
    head = "| " + " | ".join(str(h) for h in headers) + " |"
    sep = "| " + " | ".join(align for _ in headers) + " |"
    lines = [head, sep]
    for r in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return lines


def write_md(path: Path, title: str, blocks: List[str]) -> None:
    header = [
        f"# {title}\n",
        f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}｜"
        "数据范围：2024-01-01 ~ 2026-11-18｜口径：剔除已取消订单\n",
    ]
    path.write_text("\n".join(header + blocks) + "\n", encoding="utf-8")
    log.info("  📄 %s", path.name)


def fmt_money(v: float, unit: str = "元") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿{unit}"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f} 万{unit}"
    return f"{v:,.0f} {unit}"


def fmt_pct(v: float, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v * 100:.{digits}f}%"


def write_frame(table: str, df: pd.DataFrame) -> int:
    """写入 ADS 结果表（先清空，保证幂等）。"""
    from .. import db
    from ..config import MYSQL_DW_DB
    from ..etl.clean import _to_rows

    with db.connect(MYSQL_DW_DB) as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE `{table}`")
        n = db.bulk_insert(conn, table, list(df.columns), _to_rows(df), batch_size=1000)
        conn.commit()
    log.info("  💾 %-28s %6d 行", table, n)
    return n


def read_df(sql: str) -> pd.DataFrame:
    from .. import db
    from ..config import MYSQL_DW_DB

    return db.read_df(sql, database=MYSQL_DW_DB)


def calc_date() -> str:
    """全项目的"今天"固定为数据截止日 + 1 天，保证 RFM 可复现。"""
    return "2026-11-19"
