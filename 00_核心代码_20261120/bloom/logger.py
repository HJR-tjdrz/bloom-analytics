"""统一日志：控制台（UTF-8）+ 文件（按天切分，落在项目 logs/ 下）。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import LOG_DIR

_CONFIGURED: bool = False


def setup_logging(name: str = "bloom", level: int = logging.INFO) -> logging.Logger:
    """初始化并返回 logger；重复调用不会重复挂 handler。"""
    global _CONFIGURED
    logger = logging.getLogger(name)

    if not _CONFIGURED:
        # Windows 控制台默认 GBK，中文 + emoji 会炸，这里强制 UTF-8
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 极端环境兜底
            pass

        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"
        )

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)

        log_file: Path = LOG_DIR / f"{datetime.now():%Y%m%d}_{name}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        root = logging.getLogger("bloom")
        root.setLevel(level)
        root.addHandler(console)
        root.addHandler(file_handler)
        root.propagate = False
        _CONFIGURED = True

    return logger


def get_logger(suffix: Optional[str] = None) -> logging.Logger:
    return setup_logging(f"bloom.{suffix}" if suffix else "bloom")
