"""客户主数据生成：姓名池、地域分布、会员分层。

真实性设计
----------
- 姓名按性别从常见字库组合，避免"客户1/客户2"这种一眼假的数据；
- 地域按门店所在城市（沪/杭/宁/苏）加权，另留一部分跨省快递客户；
- 会员等级与消费能力相关（后面 RFM 分析会自然分层）；
- 客户分「个人」与「企业」两类，企业客户对应开业花篮这类 B2B 订单。
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

SURNAMES = list(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
)
MALE_GIVEN = [
    "伟", "强", "磊", "军", "洋", "勇", "杰", "涛", "明", "超", "浩然", "子轩", "宇航", "俊杰",
    "晨", "博文", "天佑", "文轩", "泽宇", "建国", "志强", "海涛", "鹏飞", "嘉豪", "子墨",
    "睿", "家辉", "思远", "承宇", "毅", "晓峰", "逸凡", "铭轩", "子豪", "凯",
]
FEMALE_GIVEN = [
    "芳", "娜", "敏", "静", "丽", "艳", "婷", "雪", "颖", "琳", "欣怡", "雨萱", "思琪",
    "佳琪", "梦洁", "若曦", "雅静", "诗涵", "晓月", "佳怡", "紫涵", "静怡", "可欣",
    "婉清", "语嫣", "心悦", "嘉怡", "琼", "慧", "秀英", "桂芳", "丽娟", "文静", "安琪",
]
COMPANY_PREFIX = [
    "上海静安", "上海徐汇", "杭州西湖", "南京鼓楼", "苏州工业园", "上海浦东", "杭州滨江",
    "南京建邺", "上海黄浦", "苏州姑苏",
]
COMPANY_SUFFIX = [
    "科技有限公司", "文化传媒有限公司", "餐饮管理有限公司", "贸易有限公司", "设计咨询有限公司",
    "医疗器械有限公司", "教育培训有限公司", "智能制造有限公司",
]

# 城市分布：与门店布局一致，另留跨省快递客户
CITY_POOL: List[tuple] = [
    # (城市, 省份, 权重)
    ("上海市", "上海市", 0.34),
    ("杭州市", "浙江省", 0.22),
    ("南京市", "江苏省", 0.15),
    ("苏州市", "江苏省", 0.10),
    ("北京市", "北京市", 0.06),
    ("深圳市", "广东省", 0.05),
    ("成都市", "四川省", 0.04),
    ("武汉市", "湖北省", 0.04),
]

SOURCE_CHANNELS = [("线下门店", 0.34), ("微信小程序", 0.24), ("美团外卖", 0.17),
                   ("抖音直播", 0.13), ("淘宝旗舰店", 0.07), ("朋友推荐", 0.05)]

MEMBER_LEVELS = [("普通会员", 0.60), ("银卡会员", 0.25), ("金卡会员", 0.11), ("钻石会员", 0.04)]


def _weighted_pick(rng: np.random.Generator, pairs: List[tuple], n: int) -> List[str]:
    """按权重抽样。pairs 形如 (值, 权重) 或 (城市, 省份, 权重)，权重一律取最后一位。"""
    values = [p[0] for p in pairs]
    probs = np.array([p[-1] for p in pairs], dtype=float)
    probs = probs / probs.sum()
    return list(rng.choice(values, size=n, p=probs))


def build_customer_pool(n_personal: int, n_enterprise: int, rng: np.random.Generator) -> List[dict]:
    """生成客户候选池（register_date 留空，由首单日期激活）。"""
    pool: List[dict] = []

    genders = rng.choice(["男", "女"], size=n_personal, p=[0.42, 0.58])
    cities = _weighted_pick(rng, CITY_POOL, n_personal)
    city2prov = {c: p for c, p, _ in CITY_POOL}
    sources = _weighted_pick(rng, SOURCE_CHANNELS, n_personal)
    levels = _weighted_pick(rng, MEMBER_LEVELS, n_personal)
    birthdays = rng.integers(0, (2006 - 1970) * 365, size=n_personal)
    phones = rng.choice(["138", "139", "136", "150", "158", "186", "187", "159"], size=n_personal)
    tails = rng.integers(10_000_000, 99_999_999, size=n_personal)
    loyalty = rng.beta(1.25, 3.2, size=n_personal)  # 复购倾向，决定后续被抽中的概率

    for i in range(n_personal):
        gender = str(genders[i])
        given = MALE_GIVEN[int(rng.integers(len(MALE_GIVEN)))] if gender == "男" \
            else FEMALE_GIVEN[int(rng.integers(len(FEMALE_GIVEN)))]
        name = SURNAMES[int(rng.integers(len(SURNAMES)))] + given
        pool.append(
            {
                "customer_id": f"C{i + 1:06d}",
                "customer_name": name,
                "customer_type": "个人",
                "gender": gender,
                "phone": f"{phones[i]}{tails[i]}",
                "city": cities[i],
                "province": city2prov[cities[i]],
                "birthday": (pd.Timestamp("1970-01-01") + pd.Timedelta(days=int(birthdays[i]))).date(),
                "register_date": None,
                "member_level": levels[i],
                "source_channel": sources[i],
                "is_active": 1,
                "_loyalty": float(loyalty[i]),
            }
        )

    for j in range(n_enterprise):
        pool.append(
            {
                "customer_id": f"E{j + 1:05d}",
                "customer_name": COMPANY_PREFIX[int(rng.integers(len(COMPANY_PREFIX)))]
                + COMPANY_SUFFIX[int(rng.integers(len(COMPANY_SUFFIX)))],
                "customer_type": "企业",
                "gender": "未知",
                "phone": f"021{int(rng.integers(60_000_000, 69_999_999))}",
                "city": "上海市",
                "province": "上海市",
                "birthday": None,
                "register_date": None,
                "member_level": "企业客户",
                "source_channel": "企业团购",
                "is_active": 1,
                "_loyalty": float(rng.beta(1.8, 2.0)),
            }
        )

    return pool


def pool_to_frame(pool: List[dict]) -> pd.DataFrame:
    """激活后的客户池 → DataFrame（去掉内部字段）。

    注意：激活顺序是"首单先后"，企业客户与个人客户混排，过滤后索引必不连续。
    这里必须 reset_index，否则下游按位置取值的逻辑会 KeyError。
    """
    df = pd.DataFrame(pool)
    df = df[df["register_date"].notna()].copy()
    df["register_date"] = pd.to_datetime(df["register_date"]).dt.date
    return df.drop(columns=["_loyalty"]).reset_index(drop=True)


def make_employee_names(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """给员工表填真实姓名（与客户姓名池同源，风格一致）。"""
    out = df.copy()
    names = []
    for i in range(len(out)):
        gender = "男" if i % 2 == 0 else "女"
        given = MALE_GIVEN[int(rng.integers(len(MALE_GIVEN)))] if gender == "男" \
            else FEMALE_GIVEN[int(rng.integers(len(FEMALE_GIVEN)))]
        names.append(SURNAMES[int(rng.integers(len(SURNAMES)))] + given)
    out["employee_name"] = names
    return out
