"""中国节假日与花卉消费日历。

业务价值
--------
花店是典型的「强节日驱动」生意：情人节、520、母亲节、七夕、教师节、圣诞……
单日销售额可以是平日的 3~4 倍。如果分析时只按自然月、自然周看趋势，
节日效应会被平均掉，得出「生意很平稳」的错误结论。

这里把节日、法定假期、预售节奏统一刻成一张**业务日历**（dim_date 的雏形），
后面的趋势分析、季节分解、预测模型都基于它。

注意
----
农历节日（春节/元宵/端午/七夕/中秋）逐年不同，这里按 2024-2026 实际日期
硬编码，避免引入第三方农历库（降低依赖，也便于阅读）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

# --------------------------------------------------------------------------
# 农历节日（逐年硬编码，2024-2026）
# --------------------------------------------------------------------------
LUNAR_FESTIVALS: Dict[str, Dict[int, date]] = {
    "春节": {2024: date(2024, 2, 10), 2025: date(2025, 1, 29), 2026: date(2026, 2, 17)},
    "元宵节": {2024: date(2024, 2, 24), 2025: date(2025, 2, 12), 2026: date(2026, 3, 3)},
    "端午节": {2024: date(2024, 6, 10), 2025: date(2025, 5, 31), 2026: date(2026, 6, 19)},
    "七夕节": {2024: date(2024, 8, 10), 2025: date(2025, 8, 29), 2026: date(2026, 8, 19)},
    "中秋节": {2024: date(2024, 9, 17), 2025: date(2025, 10, 6), 2026: date(2026, 9, 25)},
}

# 法定节假日放假区间（含调休近似，用于 is_holiday 标记）
STATUTORY_HOLIDAYS: List[Tuple[str, str, str]] = [
    ("2024-01-01", "2024-01-01", "元旦"),
    ("2024-02-10", "2024-02-17", "春节"),
    ("2024-04-04", "2024-04-06", "清明节"),
    ("2024-05-01", "2024-05-05", "劳动节"),
    ("2024-06-08", "2024-06-10", "端午节"),
    ("2024-09-15", "2024-09-17", "中秋节"),
    ("2024-10-01", "2024-10-07", "国庆节"),
    ("2025-01-01", "2025-01-01", "元旦"),
    ("2025-01-28", "2025-02-04", "春节"),
    ("2025-04-04", "2025-04-06", "清明节"),
    ("2025-05-01", "2025-05-05", "劳动节"),
    ("2025-05-31", "2025-06-02", "端午节"),
    ("2025-10-01", "2025-10-08", "国庆节"),
    ("2026-01-01", "2026-01-03", "元旦"),
    ("2026-02-15", "2026-02-22", "春节"),
    ("2026-04-04", "2026-04-06", "清明节"),
    ("2026-05-01", "2026-05-05", "劳动节"),
    ("2026-06-19", "2026-06-21", "端午节"),
    ("2026-09-25", "2026-09-27", "中秋节"),
    ("2026-10-01", "2026-10-07", "国庆节"),
]


@dataclass(frozen=True)
class Festival:
    """一个营销节日：峰值系数 + 预售节奏 + 持续天数。"""

    name: str
    kind: str  # fixed / nth_weekday / lunar / range
    peak_mult: float
    month: int = 0
    day: int = 0
    nth: int = 0          # 第几个星期几（母亲节=5月第2个周日 → nth=2）
    weekday: int = 0      # 0=周一 … 6=周日
    window: int = 1       # 峰值持续天数（国庆=7）
    pre_ramp: Tuple[float, ...] = ()  # 节前第1/2/3…天，取峰值涨幅的百分之多少
    day_mult: Optional[float] = None  # 当日覆盖系数（春节当天门店几乎停业）
    range_start: Tuple[int, int] = (0, 0)  # kind=range 时用：月, 日
    range_end: Tuple[int, int] = (0, 0)


FESTIVALS: List[Festival] = [
    # ---- 核心大促（玫瑰/礼盒的主战场）----
    Festival("情人节", "fixed", 3.60, month=2, day=14, pre_ramp=(0.70, 0.48, 0.30)),
    Festival("520", "fixed", 3.00, month=5, day=20, pre_ramp=(0.55, 0.35)),
    Festival("母亲节", "nth_weekday", 2.60, month=5, nth=2, weekday=6, pre_ramp=(0.60, 0.40)),
    Festival("七夕节", "lunar", 3.10, pre_ramp=(0.62, 0.42)),
    Festival("平安夜", "fixed", 2.20, month=12, day=24, pre_ramp=(0.50,)),
    Festival("圣诞节", "fixed", 1.80, month=12, day=25),
    # ---- 次级节点 ----
    Festival("妇女节", "fixed", 1.75, month=3, day=8, pre_ramp=(0.50,)),
    Festival("教师节", "fixed", 1.50, month=9, day=10, pre_ramp=(0.40,)),
    Festival("元旦", "fixed", 1.50, month=1, day=1, pre_ramp=(0.45,)),
    Festival("中秋节", "lunar", 1.40, pre_ramp=(0.40,)),
    Festival("父亲节", "nth_weekday", 1.35, month=6, nth=3, weekday=6),
    Festival("国庆节", "fixed", 1.28, month=10, day=1, window=7, pre_ramp=(0.30,)),
    Festival("双11", "fixed", 1.25, month=11, day=11, pre_ramp=(0.40,)),
    Festival("元宵节", "lunar", 1.25),
    Festival("端午节", "lunar", 1.20, pre_ramp=(0.30,)),
    Festival("儿童节", "fixed", 1.15, month=6, day=1),
    # ---- 特殊场景 ----
    # 清明祭扫花（白菊/黄菊）是花店独有的季节性品类
    Festival("清明节", "fixed", 1.60, month=4, day=4, window=3, pre_ramp=(0.35,)),
    # 春节：节前 7 天是"年宵花"旺季，除夕~初六门店反而冷清
    Festival("春节", "lunar", 1.90, window=6, day_mult=0.68,
             pre_ramp=(0.85, 0.92, 1.00, 1.00, 0.95, 0.90, 0.75)),
    # 毕业季：长达半个月的平缓旺季，用 range 类型表达
    Festival("毕业季", "range", 1.18, range_start=(6, 15), range_end=(6, 30)),
]

# 月度季节系数：2 月（情人节）、5 月（520+母亲节）、12 月（圣诞）是高峰，7 月最淡
MONTH_FACTOR: Dict[int, float] = {
    1: 1.05, 2: 1.32, 3: 1.10, 4: 0.96, 5: 1.28, 6: 0.94,
    7: 0.80, 8: 1.18, 9: 1.06, 10: 0.96, 11: 0.90, 12: 1.24,
}

# 星期系数：周末是花店的主场
WEEKDAY_FACTOR: Dict[int, float] = {0: 0.92, 1: 0.90, 2: 0.94, 3: 0.98, 4: 1.12, 5: 1.38, 6: 1.25}

# 年度增长：门店在扩张，2025 同比 +18%，2026 继续 +12%
YEAR_FACTOR: Dict[int, float] = {2023: 0.86, 2024: 1.00, 2025: 1.18, 2026: 1.32}


def _nth_weekday_of_month(year: int, month: int, nth: int, weekday: int) -> date:
    """获取某月第 n 个星期几（weekday: 0=周一 … 6=周日）。"""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (nth - 1))


def _resolve(festival: Festival, year: int) -> Optional[Tuple[date, date]]:
    """返回节日在指定年份的 (起, 止) 日期；该年不存在则返回 None。"""
    if festival.kind == "fixed":
        try:
            start = date(year, festival.month, festival.day)
        except ValueError:  # 如 2 月 30 日这类非法日期
            return None
        return start, start + timedelta(days=festival.window - 1)
    if festival.kind == "nth_weekday":
        start = _nth_weekday_of_month(year, festival.month, festival.nth, festival.weekday)
        return start, start + timedelta(days=festival.window - 1)
    if festival.kind == "lunar":
        start = LUNAR_FESTIVALS[festival.name].get(year)
        if start is None:
            return None
        return start, start + timedelta(days=festival.window - 1)
    if festival.kind == "range":
        start = date(year, *festival.range_start)
        end = date(year, *festival.range_end)
        return start, end
    return None


def build_calendar(start: date, end: date) -> pd.DataFrame:
    """生成业务日历表（日期维度 + 需求指数）。

    返回列：date / year / quarter / month / month_name / week_of_year /
    day_of_week / day_name / is_weekend / is_holiday / holiday_name /
    is_festival / festival_name / season / month_key / demand_index

    注意：月份键命名为 month_key 而不是 year_month ——
    `YEAR_MONTH` 是 MySQL 8.0 的保留字（INTERVAL 语法用），不能直接做列名。
    """
    # 1) 节日 → 日期 → 系数（同一天有多个节日时取最大值，避免系数相乘爆表）
    fest_map: Dict[date, str] = {}
    mult_map: Dict[date, float] = {}

    def _apply(d: date, name: str, mult: float) -> None:
        if not (start <= d <= end):
            return
        if mult > mult_map.get(d, 0.0):
            mult_map[d] = mult
            fest_map[d] = name

    for festival in FESTIVALS:
        for year in range(start.year - 1, end.year + 2):
            resolved = _resolve(festival, year)
            if resolved is None:
                continue
            peak, last = resolved
            lift = festival.peak_mult - 1.0

            # 峰值日
            _apply(peak, festival.name, festival.peak_mult)
            # 当日覆盖（春节当天进店人数骤降，需要直接改写而非取最大值）
            if festival.day_mult is not None and start <= peak <= end:
                mult_map[peak] = festival.day_mult
                fest_map[peak] = festival.name
            # 峰值持续期（逐日衰减）
            for i in range(1, festival.window):
                decay = max(0.55, 1.0 - 0.12 * i)
                _apply(peak + timedelta(days=i), festival.name, 1.0 + lift * decay)
            # 活动窗口结束日（range 类型内部填充）
            if festival.kind == "range":
                cur = peak
                while cur <= last:
                    _apply(cur, festival.name, festival.peak_mult)
                    cur += timedelta(days=1)
            # 预售期（节前逐步升温）
            for k, ratio in enumerate(festival.pre_ramp, start=1):
                _apply(peak - timedelta(days=k), festival.name, 1.0 + lift * ratio)

    # 2) 法定节假日
    holiday_map: Dict[date, str] = {}
    for s, e, name in STATUTORY_HOLIDAYS:
        cur, stop = date.fromisoformat(s), date.fromisoformat(e)
        while cur <= stop:
            if start <= cur <= end:
                holiday_map[cur] = name
            cur += timedelta(days=1)

    # 3) 逐日组装
    rows: List[dict] = []
    digits = start
    while digits <= end:
        wd = digits.weekday()
        month_factor = MONTH_FACTOR[digits.month]
        year_factor = YEAR_FACTOR.get(digits.year, 1.0)
        demand = (
            month_factor
            * WEEKDAY_FACTOR[wd]
            * year_factor
            * mult_map.get(digits, 1.0)
        )
        rows.append(
            {
                "date": digits,
                "year": digits.year,
                "quarter": (digits.month - 1) // 3 + 1,
                "month": digits.month,
                "month_name": f"{digits.month}月",
                "week_of_year": digits.isocalendar()[1],
                "day_of_week": wd + 1,           # 1=周一 … 7=周日（对齐 MySQL DAYOFWEEK 习惯）
                "day_name": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wd],
                "is_weekend": 1 if wd >= 5 else 0,
                "is_holiday": 1 if digits in holiday_map else 0,
                "holiday_name": holiday_map.get(digits, ""),
                "is_festival": 1 if digits in fest_map else 0,
                "festival_name": fest_map.get(digits, ""),
                "season": _season(digits.month),
                "month_key": f"{digits.year}-{digits.month:02d}",
                "demand_index": round(demand, 4),
            }
        )
        digits += timedelta(days=1)

    return pd.DataFrame(rows)


def _season(month: int) -> str:
    if month in (3, 4, 5):
        return "春"
    if month in (6, 7, 8):
        return "夏"
    if month in (9, 10, 11):
        return "秋"
    return "冬"


def festival_affinity(festival_name: str) -> Dict[str, float]:
    """节日的品类偏好：用于影响订单里的商品选择。

    返回 {bundle_category 权重}，用于加权抽样。
    """
    if festival_name in ("情人节", "520", "七夕节", "平安夜", "圣诞节"):
        return {"爱情告白": 0.62, "节日限定": 0.22, "日常悦己": 0.10, "感恩祝福": 0.06}
    if festival_name in ("母亲节", "妇女节", "教师节", "父亲节"):
        return {"感恩祝福": 0.58, "日常悦己": 0.18, "节日限定": 0.14, "爱情告白": 0.10}
    if festival_name == "清明节":
        return {"祭扫追思": 0.82, "日常悦己": 0.12, "感恩祝福": 0.06}
    if festival_name in ("春节", "元宵节", "中秋节", "元旦"):
        return {"节日限定": 0.40, "感恩祝福": 0.26, "开业庆典": 0.18, "日常悦己": 0.16}
    if festival_name in ("毕业季", "儿童节"):
        return {"祝福庆祝": 0.50, "感恩祝福": 0.24, "日常悦己": 0.26}
    if festival_name in ("国庆节", "双11", "端午节"):
        return {"日常悦己": 0.40, "爱情告白": 0.24, "感恩祝福": 0.20, "开业庆典": 0.16}
    # 平日：悦己 + 送礼
    return {"日常悦己": 0.36, "爱情告白": 0.26, "感恩祝福": 0.22, "祝福庆祝": 0.10, "开业庆典": 0.06}
