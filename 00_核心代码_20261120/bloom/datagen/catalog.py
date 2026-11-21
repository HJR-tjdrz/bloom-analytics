"""商品主数据（花材 SKU / 花束 BOM / 渠道 / 门店 / 员工 / 供应商 / 促销 / 优惠券）。

业务建模要点
-----------
1. **花束 = BOM（物料清单）**：一个花束由多种花材按支数组成，
   成本必须按 BOM 逐项还原（`bundle_items`），否则毛利率是假的。
   这是本项目比"订单 CRUD"高一个层次的地方：能算清楚每一束花的真实成本。
2. **花材是生鲜**：有 shelf_life_days（货架期），才会产生损耗、缺货、临期打折，
   后续库存周转与损耗分析才有业务基础。
3. **价格带**：上市价与成本之间区分 list_price / cost_price，
   分析时才能拆「量价拆分」（销量增长 vs 客单价提升）。
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Tuple

import pandas as pd

# --------------------------------------------------------------------------
# 供应商
# --------------------------------------------------------------------------
SUPPLIERS: List[dict] = [
    dict(supplier_id="SUP01", supplier_name="昆明滇池花卉合作社", province="云南省",
         lead_time_days=2, rating=4.8, cooperation_start="2019-03-01"),
    dict(supplier_id="SUP02", supplier_name="广州岭南花卉市场", province="广东省",
         lead_time_days=3, rating=4.5, cooperation_start="2020-06-01"),
    dict(supplier_id="SUP03", supplier_name="荷兰皇家花卉中国分部", province="上海市",
         lead_time_days=5, rating=4.6, cooperation_start="2021-01-10"),
    dict(supplier_id="SUP04", supplier_name="本地花卉合作社", province="江苏省",
         lead_time_days=1, rating=4.2, cooperation_start="2020-01-01"),
    dict(supplier_id="SUP05", supplier_name="义乌花材包装厂", province="浙江省",
         lead_time_days=4, rating=4.3, cooperation_start="2020-09-01"),
    dict(supplier_id="SUP06", supplier_name="上海礼品供应链", province="上海市",
         lead_time_days=3, rating=4.7, cooperation_start="2021-05-01"),
]

# --------------------------------------------------------------------------
# 花材 / 商品 SKU  —— (编码, 名称, 品类, 颜色, 单位, 规格, 成本, 售价, 货架期, 产地, 供应商)
# --------------------------------------------------------------------------
_SKU_ROWS: List[tuple] = [
    ("SKU001", "红玫瑰·卡罗拉", "玫瑰", "红色", "支", "20支/扎", 3.20, 8.00, 7, "云南昆明", "SUP01"),
    ("SKU002", "粉玫瑰·艾莎", "玫瑰", "粉色", "支", "20支/扎", 3.80, 9.00, 7, "云南昆明", "SUP01"),
    ("SKU003", "香槟玫瑰", "玫瑰", "香槟色", "支", "20支/扎", 3.60, 9.50, 7, "云南昆明", "SUP01"),
    ("SKU004", "白玫瑰·雪山", "玫瑰", "白色", "支", "20支/扎", 3.40, 8.50, 7, "云南昆明", "SUP01"),
    ("SKU005", "蓝妖姬（染色）", "玫瑰", "蓝色", "支", "20支/扎", 4.50, 12.00, 6, "云南昆明", "SUP01"),
    ("SKU006", "进口肯尼亚红玫瑰", "玫瑰", "红色", "支", "10支/扎", 9.50, 22.00, 5, "肯尼亚", "SUP03"),
    ("SKU007", "白百合·西伯利亚", "百合", "白色", "支", "3支/扎", 6.50, 15.00, 10, "云南昆明", "SUP01"),
    ("SKU008", "粉百合·索邦", "百合", "粉色", "支", "3支/扎", 7.00, 16.00, 10, "云南昆明", "SUP01"),
    ("SKU009", "香水百合", "百合", "白色", "支", "3支/扎", 9.00, 20.00, 10, "云南昆明", "SUP01"),
    ("SKU010", "红郁金香", "郁金香", "红色", "支", "10支/扎", 4.20, 10.00, 7, "荷兰", "SUP03"),
    ("SKU011", "粉郁金香", "郁金香", "粉色", "支", "10支/扎", 4.20, 10.00, 7, "荷兰", "SUP03"),
    ("SKU012", "黄郁金香", "郁金香", "黄色", "支", "10支/扎", 4.00, 9.50, 7, "荷兰", "SUP03"),
    ("SKU013", "向日葵", "向日葵", "黄色", "支", "5支/扎", 5.00, 12.00, 8, "云南昆明", "SUP01"),
    ("SKU014", "粉康乃馨", "康乃馨", "粉色", "支", "20支/扎", 1.80, 5.00, 12, "云南昆明", "SUP01"),
    ("SKU015", "红康乃馨", "康乃馨", "红色", "支", "20支/扎", 1.80, 5.00, 12, "云南昆明", "SUP01"),
    ("SKU016", "白康乃馨", "康乃馨", "白色", "支", "20支/扎", 1.90, 5.20, 12, "云南昆明", "SUP01"),
    ("SKU017", "蓝绣球", "绣球", "蓝色", "支", "单支", 18.00, 38.00, 6, "云南昆明", "SUP01"),
    ("SKU018", "粉绣球", "绣球", "粉色", "支", "单支", 18.00, 38.00, 6, "云南昆明", "SUP01"),
    ("SKU019", "白绣球", "绣球", "白色", "支", "单支", 19.00, 40.00, 6, "云南昆明", "SUP01"),
    ("SKU020", "白洋桔梗", "洋桔梗", "白色", "支", "10支/扎", 3.00, 7.50, 9, "云南昆明", "SUP01"),
    ("SKU021", "粉洋桔梗", "洋桔梗", "粉色", "支", "10支/扎", 3.00, 7.50, 9, "云南昆明", "SUP01"),
    ("SKU022", "紫洋桔梗", "洋桔梗", "紫色", "支", "10支/扎", 3.20, 8.00, 9, "云南昆明", "SUP01"),
    ("SKU023", "白满天星", "满天星", "白色", "扎", "扎", 12.00, 28.00, 10, "云南昆明", "SUP01"),
    ("SKU024", "粉满天星", "满天星", "粉色", "扎", "扎", 12.50, 29.00, 10, "云南昆明", "SUP01"),
    ("SKU025", "白菊", "菊花", "白色", "支", "20支/扎", 1.50, 4.50, 14, "本地", "SUP04"),
    ("SKU026", "黄菊", "菊花", "黄色", "支", "20支/扎", 1.50, 4.50, 14, "本地", "SUP04"),
    ("SKU027", "乒乓菊", "菊花", "混色", "支", "10支/扎", 3.50, 8.50, 12, "广州", "SUP02"),
    ("SKU028", "尤加利叶", "配叶", "绿色", "扎", "扎", 8.00, 18.00, 15, "云南昆明", "SUP01"),
    ("SKU029", "排草", "配叶", "绿色", "扎", "扎", 6.00, 14.00, 15, "本地", "SUP04"),
    ("SKU030", "栀子叶", "配叶", "绿色", "扎", "扎", 5.00, 12.00, 12, "本地", "SUP04"),
    ("SKU031", "情人草", "配叶", "紫色", "扎", "扎", 7.00, 16.00, 20, "云南昆明", "SUP01"),
    ("SKU032", "玻璃花瓶", "花器", "透明", "个", "中号", 12.00, 39.00, 999, "义乌", "SUP05"),
    ("SKU033", "陶瓷花瓶", "花器", "白色", "个", "中号", 26.00, 79.00, 999, "景德镇", "SUP05"),
    ("SKU034", "鲜花礼盒（空盒）", "花器", "粉色", "个", "方形", 8.00, 25.00, 999, "义乌", "SUP05"),
    ("SKU035", "费列罗巧克力礼盒", "礼盒", "金色", "盒", "24粒", 45.00, 99.00, 180, "进口", "SUP06"),
    ("SKU036", "德芙心形礼盒", "礼盒", "红色", "盒", "18粒", 28.00, 69.00, 180, "国产", "SUP06"),
    ("SKU037", "手写祝福卡", "贺卡", "多色", "张", "标准", 1.20, 5.00, 999, "义乌", "SUP05"),
    ("SKU038", "立体贺卡", "贺卡", "多色", "张", "精装", 3.50, 12.00, 999, "义乌", "SUP05"),
    ("SKU039", "小熊玩偶", "礼盒", "棕色", "个", "25cm", 18.00, 49.00, 999, "义乌", "SUP05"),
    ("SKU040", "永生玫瑰礼盒", "永生花", "红色", "盒", "单朵", 55.00, 158.00, 999, "进口", "SUP06"),
    ("SKU041", "永生花音乐盒", "永生花", "粉色", "盒", "精装", 88.00, 238.00, 999, "进口", "SUP06"),
]

# SKU 编码 <-> 短码，便于 BOM 里书写
SKU_CODE: Dict[str, str] = {
    "rose_red": "SKU001", "rose_pink": "SKU002", "rose_champagne": "SKU003",
    "rose_white": "SKU004", "rose_blue": "SKU005", "rose_import": "SKU006",
    "lily_white": "SKU007", "lily_pink": "SKU008", "lily_scent": "SKU009",
    "tulip_red": "SKU010", "tulip_pink": "SKU011", "tulip_yellow": "SKU012",
    "sun_flower": "SKU013", "carnation_pink": "SKU014", "carnation_red": "SKU015",
    "carnation_white": "SKU016", "hydrangea_blue": "SKU017", "hydrangea_pink": "SKU018",
    "hydrangea_white": "SKU019", "eustoma_white": "SKU020", "eustoma_pink": "SKU021",
    "eustoma_purple": "SKU022", "babysbreath_white": "SKU023", "babysbreath_pink": "SKU024",
    "chrys_white": "SKU025", "chrys_yellow": "SKU026", "chrys_ball": "SKU027",
    "eucalyptus": "SKU028", "ruscus": "SKU029", "fern": "SKU030", "greenery_grass": "SKU031",
    "vase_glass": "SKU032", "vase_ceramic": "SKU033", "box_gift": "SKU034",
    "choco_ferrero": "SKU035", "choco_dove": "SKU036", "card_hand": "SKU037",
    "card_3d": "SKU038", "bear_plush": "SKU039", "eternal_rose": "SKU040",
    "eternal_box": "SKU041",
}

# --------------------------------------------------------------------------
# 花束（成品）：(编码, 名称, 品类, 场景, 描述, BOM[(短码, 数量)], 加价率, 上市日)
# 加价率 = 售价 / 主材成本，花艺行业常见 1.4~1.6
# --------------------------------------------------------------------------
_BUNDLE_ROWS: List[tuple] = [
    ("BD001", "唯一·99朵红玫瑰", "爱情告白", "求婚",
     "99支卡罗拉红玫瑰+尤加利，经典求婚款",
     [("rose_red", 99), ("eucalyptus", 2)], 1.42, "2019-05-20"),
    ("BD002", "心动·33朵红玫瑰", "爱情告白", "表白",
     "33支红玫瑰+满天星，寓意三生三世",
     [("rose_red", 33), ("babysbreath_white", 1), ("fern", 1)], 1.45, "2019-08-07"),
    ("BD003", "初恋·11朵粉玫瑰", "爱情告白", "表白",
     "11支艾莎粉玫瑰+白洋桔梗，温柔初恋感",
     [("rose_pink", 11), ("eustoma_white", 3), ("eucalyptus", 1)], 1.48, "2020-02-14"),
    ("BD004", "星河·19朵香槟玫瑰", "爱情告白", "纪念日",
     "19支香槟玫瑰+粉满天星，纪念日首选",
     [("rose_champagne", 19), ("babysbreath_pink", 1)], 1.46, "2020-05-20"),
    ("BD005", "纯白誓言·21朵白玫瑰", "爱情告白", "婚礼",
     "21支雪山白玫瑰，婚礼与纯粹的爱",
     [("rose_white", 21), ("fern", 2)], 1.44, "2020-10-01"),
    ("BD006", "蓝色恋人·11朵蓝妖姬", "爱情告白", "表白",
     "11支染色蓝玫瑰，个性表白款",
     [("rose_blue", 11), ("babysbreath_white", 1)], 1.50, "2021-02-14"),
    ("BD007", "奢华·进口红玫瑰礼盒", "爱情告白", "求婚",
     "18支肯尼亚进口红玫瑰+礼盒+费列罗",
     [("rose_import", 18), ("box_gift", 1), ("choco_ferrero", 1)], 1.38, "2021-05-20"),
    ("BD008", "甜蜜告白·玫瑰巧克力", "爱情告白", "表白",
     "11支红玫瑰+费列罗+手写卡",
     [("rose_red", 11), ("choco_ferrero", 1), ("card_hand", 1)], 1.47, "2021-08-14"),
    ("BD009", "永恒之约·永生玫瑰盒", "爱情告白", "纪念日",
     "永生玫瑰+立体贺卡，长久不凋",
     [("eternal_rose", 1), ("card_3d", 1)], 1.40, "2022-02-14"),
    ("BD010", "玫瑰小熊礼盒", "爱情告白", "表白",
     "9支红玫瑰+小熊玩偶+礼盒",
     [("rose_red", 9), ("bear_plush", 1), ("box_gift", 1)], 1.43, "2022-05-20"),
    ("BD011", "感恩·康乃馨花束", "感恩祝福", "母亲节",
     "19支粉康乃馨+6支红康乃馨+排草",
     [("carnation_pink", 19), ("carnation_red", 6), ("ruscus", 2)], 1.52, "2019-05-12"),
    ("BD012", "母亲的爱·康乃馨礼盒", "感恩祝福", "母亲节",
     "33支粉康乃馨礼盒+手写卡",
     [("carnation_pink", 33), ("box_gift", 1), ("card_hand", 1)], 1.50, "2020-05-10"),
    ("BD013", "谢师恩·百合向日葵", "感恩祝福", "教师节",
     "白百合+向日葵+排草，教师节经典搭配",
     [("lily_white", 3), ("sun_flower", 3), ("ruscus", 2)], 1.49, "2020-09-10"),
    ("BD014", "温情·百合康乃馨", "感恩祝福", "探病慰问",
     "粉百合+红康乃馨+尤加利，温和不张扬",
     [("lily_pink", 2), ("carnation_red", 12), ("eucalyptus", 1)], 1.47, "2021-03-08"),
    ("BD015", "祝福·香水百合花篮", "感恩祝福", "乔迁",
     "6支香水百合+排草+立体贺卡",
     [("lily_scent", 6), ("ruscus", 2), ("card_3d", 1)], 1.45, "2021-09-10"),
    ("BD016", "毕业快乐·向日葵束", "祝福庆祝", "毕业",
     "6支向日葵+满天星+手写卡，毕业季爆款",
     [("sun_flower", 6), ("babysbreath_white", 1), ("card_hand", 1)], 1.51, "2020-06-15"),
    ("BD017", "前程似锦·混搭花束", "祝福庆祝", "毕业",
     "向日葵+紫洋桔梗+香槟玫瑰，混搭设计款",
     [("sun_flower", 3), ("eustoma_purple", 5), ("rose_champagne", 6)], 1.46, "2021-06-15"),
    ("BD018", "生日快乐·粉系花束", "祝福庆祝", "生日",
     "粉玫瑰+粉绣球+立体贺卡",
     [("rose_pink", 9), ("hydrangea_pink", 1), ("card_3d", 1)], 1.47, "2020-01-01"),
    ("BD019", "越来越好·郁金香束", "祝福庆祝", "升职",
     "粉/黄郁金香+尤加利，职场祝福",
     [("tulip_pink", 10), ("tulip_yellow", 5), ("eucalyptus", 1)], 1.48, "2021-04-01"),
    ("BD020", "乔迁之喜·绣球花礼", "祝福庆祝", "乔迁",
     "蓝绣球+玻璃花瓶，新居装饰",
     [("hydrangea_blue", 2), ("vase_glass", 1)], 1.44, "2021-10-01"),
    ("BD021", "开业大吉·开业花篮", "开业庆典", "开业",
     "向日葵+乒乓菊+排草，标准开业花篮",
     [("sun_flower", 9), ("chrys_ball", 10), ("ruscus", 4)], 1.40, "2019-01-01"),
    ("BD022", "生意兴隆·双联花篮", "开业庆典", "开业",
     "双联大花篮，向日葵+红康乃馨",
     [("sun_flower", 12), ("carnation_red", 20), ("fern", 4)], 1.38, "2019-03-01"),
    ("BD023", "庆典·百合花篮", "开业庆典", "开业",
     "白百合+乒乓菊+尤加利，典雅庆典款",
     [("lily_white", 8), ("chrys_ball", 6), ("eucalyptus", 3)], 1.42, "2020-07-01"),
    ("BD024", "一周心情·混合花束", "日常悦己", "日常",
     "郁金香+洋桔梗+满天星，办公室周花",
     [("tulip_red", 5), ("eustoma_white", 4), ("babysbreath_white", 1)], 1.53, "2022-03-01"),
    ("BD025", "办公桌小花·向日葵", "日常悦己", "日常",
     "3支向日葵+尤加利，小份量办公桌花",
     [("sun_flower", 3), ("eucalyptus", 1)], 1.55, "2022-04-01"),
    ("BD026", "浪漫满屋·粉绣球", "日常悦己", "日常",
     "粉绣球+粉玫瑰+玻璃花瓶",
     [("hydrangea_pink", 1), ("rose_pink", 6), ("vase_glass", 1)], 1.45, "2022-06-01"),
    ("BD027", "清新·白绿花束", "日常悦己", "日常",
     "白百合+尤加利+栀子叶，极简白绿",
     [("lily_white", 2), ("eucalyptus", 2), ("fern", 2)], 1.56, "2022-08-01"),
    ("BD028", "郁见你·郁金香花束", "日常悦己", "日常",
     "粉红郁金香+情人草，春日限定",
     [("tulip_pink", 9), ("tulip_red", 3), ("greenery_grass", 1)], 1.50, "2023-02-01"),
    ("BD029", "情人节·红玫瑰礼盒", "节日限定", "情人节",
     "19支红玫瑰礼盒+德芙+立体贺卡",
     [("rose_red", 19), ("box_gift", 1), ("choco_dove", 1), ("card_3d", 1)], 1.43, "2019-02-01"),
    ("BD030", "520·爱的告白花束", "节日限定", "520",
     "11支粉玫瑰+费列罗+手写卡",
     [("rose_pink", 11), ("choco_ferrero", 1), ("card_hand", 1)], 1.46, "2020-05-01"),
    ("BD031", "七夕·星河玫瑰束", "节日限定", "七夕",
     "19支香槟玫瑰+粉满天星+立体贺卡",
     [("rose_champagne", 19), ("babysbreath_pink", 1), ("card_3d", 1)], 1.45, "2020-08-01"),
    ("BD032", "圣诞·圣诞花环", "节日限定", "圣诞",
     "乒乓菊+尤加利+情人草编织花环",
     [("chrys_ball", 8), ("eucalyptus", 3), ("greenery_grass", 2)], 1.48, "2021-12-01"),
    ("BD033", "中秋·花好月圆礼盒", "节日限定", "中秋",
     "白百合+乒乓菊+礼盒，中秋礼赠",
     [("lily_white", 3), ("chrys_ball", 6), ("box_gift", 1)], 1.44, "2021-09-01"),
    ("BD034", "年宵花·富贵吉祥组合", "节日限定", "春节",
     "尤加利+陶瓷花瓶+情人草，年宵花组合",
     [("eucalyptus", 3), ("vase_ceramic", 1), ("greenery_grass", 2)], 1.42, "2020-01-10"),
    ("BD035", "追思·白菊花束", "祭扫追思", "清明",
     "21支白菊+栀子叶，肃穆追思",
     [("chrys_white", 21), ("fern", 2)], 1.58, "2019-04-01"),
    ("BD036", "缅怀·黄白菊", "祭扫追思", "清明",
     "白菊+黄菊各11支+排草",
     [("chrys_white", 11), ("chrys_yellow", 11), ("ruscus", 2)], 1.57, "2019-04-01"),
    ("BD037", "永生花音乐盒", "日常悦己", "纪念日",
     "永生花音乐盒+手写卡，送礼不掉价",
     [("eternal_box", 1), ("card_hand", 1)], 1.41, "2023-01-01"),
    ("BD038", "感恩有你·混搭康乃馨", "感恩祝福", "教师节",
     "粉/红康乃馨+满天星，多人分享装",
     [("carnation_pink", 12), ("carnation_red", 6), ("babysbreath_white", 1)], 1.51, "2022-09-01"),
]

# 包装与人工成本（按花束规模分摊，体现真实毛利）
PACKAGING_COST = {"爱情告白": 8.0, "感恩祝福": 7.0, "祝福庆祝": 7.5,
                  "开业庆典": 15.0, "日常悦己": 6.0, "节日限定": 9.0, "祭扫追思": 6.5}

# --------------------------------------------------------------------------
# 渠道 / 门店 / 员工
# --------------------------------------------------------------------------
CHANNELS: List[dict] = [
    dict(channel_id="CH01", channel_name="线下门店", channel_type="线下", commission_rate=0.000, is_active=1),
    dict(channel_id="CH02", channel_name="微信小程序", channel_type="线上", commission_rate=0.006, is_active=1),
    dict(channel_id="CH03", channel_name="美团外卖", channel_type="线上", commission_rate=0.180, is_active=1),
    dict(channel_id="CH04", channel_name="抖音直播", channel_type="线上", commission_rate=0.120, is_active=1),
    dict(channel_id="CH05", channel_name="淘宝旗舰店", channel_type="线上", commission_rate=0.050, is_active=1),
    dict(channel_id="CH06", channel_name="企业团购", channel_type="线下", commission_rate=0.000, is_active=1),
]

STORES: List[dict] = [
    dict(store_id="ST01", store_name="静安旗舰店", city="上海市", province="上海市",
         open_date="2020-03-15", area_sqm=120, staff_count=9, status="营业中"),
    dict(store_id="ST02", store_name="徐汇滨江店", city="上海市", province="上海市",
         open_date="2021-06-01", area_sqm=85, staff_count=7, status="营业中"),
    dict(store_id="ST03", store_name="浦东陆家嘴店", city="上海市", province="上海市",
         open_date="2022-09-10", area_sqm=95, staff_count=8, status="营业中"),
    dict(store_id="ST04", store_name="西湖文化广场店", city="杭州市", province="浙江省",
         open_date="2021-04-20", area_sqm=90, staff_count=7, status="营业中"),
    dict(store_id="ST05", store_name="滨江星光大道店", city="杭州市", province="浙江省",
         open_date="2023-05-18", area_sqm=78, staff_count=6, status="营业中"),
    dict(store_id="ST06", store_name="南京新街口店", city="南京市", province="江苏省",
         open_date="2020-11-11", area_sqm=88, staff_count=7, status="营业中"),
    dict(store_id="ST07", store_name="苏州观前街店", city="苏州市", province="江苏省",
         open_date="2022-03-08", area_sqm=76, staff_count=6, status="营业中"),
    dict(store_id="ST08", store_name="线上云仓（华东）", city="上海市", province="上海市",
         open_date="2023-08-01", area_sqm=200, staff_count=5, status="营业中"),
]

_STORE_CITY = {s["store_id"]: s["city"] for s in STORES}

EMPLOYEES: List[dict] = []
_ROLE_PLAN = ["店长", "花艺师", "花艺师", "花艺师助理", "客服", "配送员"]
for _i, _store in enumerate(STORES, start=1):
    _n = max(4, _store["staff_count"] - 2)
    for _j in range(_n):
        EMPLOYEES.append(
            dict(
                employee_id=f"EMP{_i:02d}{_j + 1:02d}",
                employee_name="",  # 由 customers 里的姓名池统一填充
                store_id=_store["store_id"],
                role_name=_ROLE_PLAN[_j % len(_ROLE_PLAN)],
                hire_date=f"{2020 + (_j % 5)}-{(1 + _j % 12):02d}-01",
                is_active=1,
            )
        )

# --------------------------------------------------------------------------
# 促销活动
# --------------------------------------------------------------------------
# 每个年份针对重点节日配置一场活动（含预售期），再叠加每月会员日与日常满减
_PROMO_PLAN: List[Tuple[str, str, float, float, float, float]] = [
    # (节日, 类型, 折扣率, 门槛, 立减, 预算万元)
    ("情人节", "折扣", 0.88, 0.0, 0.0, 8.0),
    ("情人节", "满减", 1.00, 399.0, 60.0, 6.0),
    ("520", "折扣", 0.90, 0.0, 0.0, 6.5),
    ("520", "满减", 1.00, 299.0, 40.0, 5.0),
    ("母亲节", "折扣", 0.92, 0.0, 0.0, 5.0),
    ("七夕节", "满减", 1.00, 399.0, 55.0, 6.0),
    ("七夕节", "秒杀", 0.80, 0.0, 0.0, 2.0),
    ("教师节", "折扣", 0.93, 0.0, 0.0, 2.5),
    ("国庆节", "满减", 1.00, 299.0, 30.0, 3.0),
    ("平安夜", "买赠", 0.95, 0.0, 0.0, 3.5),
    ("圣诞节", "折扣", 0.90, 0.0, 0.0, 3.0),
    ("春节", "满减", 1.00, 499.0, 80.0, 5.5),
    ("清明节", "折扣", 0.95, 0.0, 0.0, 1.5),
    ("毕业季", "满减", 1.00, 199.0, 25.0, 2.2),
]


def build_skus() -> pd.DataFrame:
    rows = [
        dict(
            sku_id=r[0], sku_name=r[1], category=r[2], color=r[3], unit=r[4], spec=r[5],
            cost_price=r[6], list_price=r[7], shelf_life_days=r[8], origin_place=r[9],
            supplier_id=r[10], status="在售",
        )
        for r in _SKU_ROWS
    ]
    return pd.DataFrame(rows)


def build_bundles(sku_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (花束表, 花束BOM表)。售价按主材成本 × 加价率并做尾数取整。"""
    price_map = dict(zip(sku_df["sku_id"], sku_df["list_price"]))
    cost_map = dict(zip(sku_df["sku_id"], sku_df["cost_price"]))
    shelf_map = dict(zip(sku_df["sku_id"], sku_df["shelf_life_days"]))

    bundle_rows: List[dict] = []
    bom_rows: List[dict] = []
    for (bid, name, category, scene, desc, bom, markup, launch) in _BUNDLE_ROWS:
        material_price = 0.0
        material_cost = 0.0
        min_shelf = 999
        for key, qty in bom:
            sku_id = SKU_CODE[key]
            material_price += price_map[sku_id] * qty
            material_cost += cost_map[sku_id] * qty
            min_shelf = min(min_shelf, shelf_map[sku_id])
            bom_rows.append(dict(bundle_id=bid, sku_id=sku_id, quantity=qty))

        raw = material_price * markup
        # 心理定价：向上取整到 10 元再减 1（如 348 → 349）
        list_price = float(int((raw + 9) // 10) * 10 - 1)
        packaging = PACKAGING_COST[category]
        labor = round(min(28.0, 6.0 + len(bom) * 3.5), 1)
        cost_price = round(material_cost + packaging + labor, 2)

        bundle_rows.append(
            dict(
                bundle_id=bid, bundle_name=name, category=category, scene=scene,
                description=desc, list_price=list_price, cost_price=cost_price,
                launch_date=launch, status="在售",
            )
        )

    return pd.DataFrame(bundle_rows), pd.DataFrame(bom_rows)


def build_promotions(years: List[int]) -> pd.DataFrame:
    """按节日生成促销活动：预售 3 天 + 活动当天，覆盖全年重点节点。"""
    from .calendar_cn import FESTIVALS, _resolve

    promo_rows: List[dict] = []
    seq = 1
    for year in years:
        for festival_name, ptype, rate, threshold, reduce, budget in _PROMO_PLAN:
            festival = next((f for f in FESTIVALS if f.name == festival_name), None)
            if festival is None:
                continue
            resolved = _resolve(festival, year)
            if resolved is None:
                continue
            peak, _ = resolved
            from datetime import timedelta

            start = peak - timedelta(days=3)
            if ptype == "买赠":
                name = f"{year}·{festival_name}买赠活动"
            elif ptype == "秒杀":
                name = f"{year}·{festival_name}限时秒杀"
            elif ptype == "满减":
                name = f"{year}·{festival_name}满{int(threshold)}减{int(reduce)}"
            else:
                name = f"{year}·{festival_name}全场{rate * 10:.1f}折"
            promo_rows.append(
                dict(
                    promotion_id=f"PM{seq:04d}",
                    promotion_name=name,
                    promotion_type=ptype,
                    festival=festival_name,
                    start_date=start,
                    end_date=peak,
                    discount_rate=rate,
                    threshold_amount=threshold,
                    reduce_amount=reduce,
                    budget_amount=round(budget * 10_000, 2),
                    status="已结束",
                )
            )
            seq += 1

        # 每月 8 日会员日（长期活动）
        for month in range(1, 13):
            promo_rows.append(
                dict(
                    promotion_id=f"PM{seq:04d}",
                    promotion_name=f"{year}年{month}月会员日",
                    promotion_type="折扣",
                    festival="会员日",
                    start_date=date(year, month, 8),
                    end_date=date(year, month, 8),
                    discount_rate=0.95,
                    threshold_amount=0.0,
                    reduce_amount=0.0,
                    budget_amount=8000.0,
                    status="已结束",
                )
            )
            seq += 1

    return pd.DataFrame(promo_rows)


def build_coupons() -> pd.DataFrame:
    rows = [
        ("CP01", "新客首单立减20元", "满减券", 20.0, 99.0, "2024-01-01", "2026-12-31"),
        ("CP02", "新客首单立减30元", "满减券", 30.0, 159.0, "2024-01-01", "2026-12-31"),
        ("CP03", "会员日满199减20", "满减券", 20.0, 199.0, "2024-01-01", "2026-12-31"),
        ("CP04", "会员日满299减40", "满减券", 40.0, 299.0, "2024-01-01", "2026-12-31"),
        ("CP05", "情人节专享券50元", "满减券", 50.0, 399.0, "2024-02-01", "2026-02-28"),
        ("CP06", "母亲节专享券30元", "满减券", 30.0, 259.0, "2024-05-01", "2026-05-31"),
        ("CP07", "无门槛10元券", "无门槛券", 10.0, 0.0, "2024-01-01", "2026-12-31"),
        ("CP08", "9折会员券", "折扣券", 0.0, 199.0, "2024-01-01", "2026-12-31"),
        ("CP09", "企业团购券100元", "满减券", 100.0, 999.0, "2024-01-01", "2026-12-31"),
        ("CP10", "七夕专享券40元", "满减券", 40.0, 329.0, "2024-08-01", "2026-08-31"),
        ("CP11", "圣诞专享券30元", "满减券", 30.0, 259.0, "2024-12-01", "2026-12-31"),
        ("CP12", "沉睡客户召回券25元", "满减券", 25.0, 159.0, "2024-01-01", "2026-12-31"),
    ]
    return pd.DataFrame(
        rows,
        columns=["coupon_id", "coupon_name", "coupon_type", "face_value",
                 "threshold_amount", "valid_from", "valid_to"],
    ).assign(status="生效中")


def build_channels() -> pd.DataFrame:
    return pd.DataFrame(CHANNELS)


def build_stores() -> pd.DataFrame:
    return pd.DataFrame(STORES)


def build_employees() -> pd.DataFrame:
    return pd.DataFrame(EMPLOYEES)


def build_suppliers() -> pd.DataFrame:
    return pd.DataFrame(SUPPLIERS)


# 渠道组合的演化：抖音直播逐月崛起、美团稳定、淘宝式微
def channel_weight(year: int, month: int) -> Dict[str, float]:
    """返回某月的渠道结构权重（模拟运营策略调整）。"""
    t = (year - 2024) * 12 + (month - 1)  # 月份序号
    douyin = min(0.20, 0.04 + 0.0055 * t)
    taobao = max(0.05, 0.11 - 0.0022 * t)
    miniapp = 0.24 + 0.0012 * t
    meituan = 0.17
    store = max(0.28, 0.42 - 0.004 * t)
    b2b = 0.02
    total = douyin + taobao + miniapp + meituan + store + b2b
    return {
        "CH01": store / total, "CH02": miniapp / total, "CH03": meituan / total,
        "CH04": douyin / total, "CH05": taobao / total, "CH06": b2b / total,
    }
