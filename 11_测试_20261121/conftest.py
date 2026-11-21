"""pytest 公共配置：把工程代码加入 sys.path，并提供数据库可用性检测。

设计考虑
--------
很多同学的项目测试一跑就红，因为强依赖数据库。
这里把测试分成两类：
- **纯逻辑测试**（日历、清洗、RFM、规则定义）：任何机器都能跑；
- **依赖数据库的测试**（API、指标取数）：连不上 MySQL 时自动 skip，而不是 fail。
这样 CI 上（没有数据库）也能验证大部分逻辑。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = PROJECT_ROOT / "00_核心代码_20261120"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))


@pytest.fixture(scope="session")
def require_db():
    """数据库可用则返回 True，否则跳过当前测试。"""
    try:
        from bloom import db

        db.fetch_one("SELECT 1 AS ok", database="bloom_dw")
        return True
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL 不可用，跳过依赖数据库的测试：{exc}")
