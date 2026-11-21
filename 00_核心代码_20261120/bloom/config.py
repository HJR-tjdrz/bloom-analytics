"""全局配置：路径、数据库连接、数据生成参数。

设计原则
--------
1. 所有路径都由本文件推导，脚本从任何工作目录启动都能定位到项目根；
2. 连接信息统一走 .env，代码中不出现任何硬编码密码；
3. 数据生成参数集中在常量里，保证「同一份配置 → 同一份数据」。
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# 一、路径体系
# --------------------------------------------------------------------------
# config.py -> bloom -> 00_核心代码_20261120 -> 最新项目
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
CODE_ROOT: Final[Path] = PROJECT_ROOT / "00_核心代码_20261120"
WEB_DIR: Final[Path] = CODE_ROOT / "bloom" / "web"

DATA_FACTORY_DIR: Final[Path] = PROJECT_ROOT / "03_数据工厂_20261120"
DATA_CLEAN_DIR: Final[Path] = DATA_FACTORY_DIR / "data" / "clean"
DATA_RAW_DIR: Final[Path] = DATA_FACTORY_DIR / "data" / "raw_export"
DATA_SAMPLE_DIR: Final[Path] = DATA_FACTORY_DIR / "data" / "sample"
DATA_MANIFEST: Final[Path] = DATA_FACTORY_DIR / "data" / "manifest.json"

DW_DIR: Final[Path] = PROJECT_ROOT / "04_MySQL数仓_20261120"
GOVERNANCE_DIR: Final[Path] = PROJECT_ROOT / "05_数据治理_20261120"
ETL_DIR: Final[Path] = PROJECT_ROOT / "06_ETL与特征工程_20261120"
LOG_DIR: Final[Path] = PROJECT_ROOT / "logs"

for _d in (DATA_CLEAN_DIR, DATA_RAW_DIR, DATA_SAMPLE_DIR, LOG_DIR,
           GOVERNANCE_DIR, ETL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# 二、环境变量
# --------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")

MYSQL_HOST: Final[str] = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT: Final[int] = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER: Final[str] = os.getenv("MYSQL_USER", "bloom_app")
MYSQL_PASSWORD: Final[str] = os.getenv("MYSQL_PASSWORD", "")
MYSQL_OLTP_DB: Final[str] = os.getenv("MYSQL_OLTP_DB", "bloom_oltp")
MYSQL_DW_DB: Final[str] = os.getenv("MYSQL_DW_DB", "bloom_dw")

# 默认编码统一 utf8mb4：订单备注里有中文与 emoji，utf8 三字节存不下
MYSQL_CHARSET: Final[str] = "utf8mb4"

# --------------------------------------------------------------------------
# 三、数据生成参数
# --------------------------------------------------------------------------
SEED: Final[int] = int(os.getenv("BLOOM_SEED", "20241118"))
DATA_START: Final[date] = date.fromisoformat(os.getenv("BLOOM_DATA_START", "2024-01-01"))
DATA_END: Final[date] = date.fromisoformat(os.getenv("BLOOM_DATA_END", "2026-11-18"))

# 基准日订单量（再乘以节日/星期/月份/增长因子）
# 66 × 三年需求指数合计 1547 ≈ 10.2 万单，正好是"10 万单量级"的实战数据量
BASE_DAILY_ORDERS: Final[int] = 66
# 复购订单占比：真实花店老客复购率较高，取 0.72
REPEAT_ORDER_RATIO: Final[float] = 0.72
# 预生成的客户候选池上限（够用即可，实际按首单时间逐个激活）
CUSTOMER_POOL_SIZE: Final[int] = 42_000


def data_path(name: str, kind: str = "clean") -> Path:
    """返回某个数据文件的绝对路径。kind ∈ {clean, raw_export, sample}。"""
    base = {
        "clean": DATA_CLEAN_DIR,
        "raw_export": DATA_RAW_DIR,
        "sample": DATA_SAMPLE_DIR,
    }[kind]
    return base / f"{name}.csv"
