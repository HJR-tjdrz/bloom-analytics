"""数据落地：CSV、小样本、生成清单（manifest）。

编码选择
--------
统一 `utf-8-sig`（带 BOM）。原因很实际：运营同学会直接双击 CSV 用 Excel 打开，
不带 BOM 的中文会变乱码，这一步在真实项目里经常被忽略，但直接影响协作效率。
Python 侧 pandas 读取时加 `encoding='utf-8-sig'` 即可自动兼容。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict

import pandas as pd

from ..config import DATA_CLEAN_DIR, DATA_MANIFEST, DATA_RAW_DIR, DATA_SAMPLE_DIR
from ..logger import get_logger

log = get_logger("writer")

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _write(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig", date_format=DATE_FORMAT)
    log.info("  ✓ %-32s %8d 行  %6.1f MB", path.name, len(df), path.stat().st_size / 1024 / 1024)


def write_clean(tables: Dict[str, pd.DataFrame]) -> None:
    log.info("写出干净层数据 → %s", DATA_CLEAN_DIR)
    for name, df in tables.items():
        _write(df, DATA_CLEAN_DIR / f"{name}.csv")


def write_raw(tables: Dict[str, pd.DataFrame]) -> None:
    log.info("写出原始导出层（含脏数据）→ %s", DATA_RAW_DIR)
    for name, df in tables.items():
        _write(df, DATA_RAW_DIR / f"{name}.csv")


def write_samples(tables: Dict[str, pd.DataFrame], n: int = 200) -> None:
    log.info("写出 GitHub 演示样本（每表前 %d 行）→ %s", n, DATA_SAMPLE_DIR)
    for name, df in tables.items():
        _write(df.head(n), DATA_SAMPLE_DIR / f"{name}_sample.csv")


def build_manifest(clean: Dict[str, pd.DataFrame], raw: Dict[str, pd.DataFrame],
                   dq_manifest: list, params: dict) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "clean_tables": {
            name: {"rows": int(len(df)), "columns": list(df.columns)} for name, df in clean.items()
        },
        "raw_tables": {
            name: {"rows": int(len(df)), "columns": list(df.columns)} for name, df in raw.items()
        },
        "dq_injection": dq_manifest,
    }


def write_manifest(manifest: dict) -> None:
    DATA_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("写出生成清单 → %s", DATA_MANIFEST.name)
