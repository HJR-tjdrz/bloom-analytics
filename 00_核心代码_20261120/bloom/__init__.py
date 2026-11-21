"""BloomAnalytics · 花店全渠道经营数据分析平台

工程代码根包。所有可执行入口见项目根目录 run.py。
"""

import os as _os

# joblib/sklearn 在 Windows 上会调 wmic 数物理核，沙箱环境里会被拦住并打印一堆警告。
# 固定为 1，既避免噪音，也让 K-Means 结果完全可复现。
_os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

__version__ = "0.1.0"
__author__ = "BloomAnalytics"
