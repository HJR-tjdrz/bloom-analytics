-- ============================================================================
--  02 · 标签体系：标签目录 + 客户标签宽表 + 商品标签宽表
--
--  什么叫"标签体系"？
--    不是随便打几个标，而是有**目录、有口径、有层级、可维护**的一套资产：
--      · 目录（dim_tag）：标签编码、名称、分组、口径说明、来源表
--      · 值域：每个标签的可选值必须有限且可枚举（否则没法做人群圈选）
--      · 计算逻辑：每个标签一段确定的 SQL，可复算、可回溯
--
--  标签分四组：
--    人口属性（类型/城市/会员）、行为特征（渠道/品类/价格偏好/节日型）、
--    价值分层（消费档位/生命周期）、风险特征（退款敏感/用券偏好）
--
--  执行：python run.py gov（本文件由 Python 调用）
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_dw;

DROP TABLE IF EXISTS ads_customer_tags;
DROP TABLE IF EXISTS ads_product_tags;
DROP TABLE IF EXISTS dim_tag;

-- ---------------------------------------------------------------------------
-- 1. 标签目录（元数据，治理的核心：没有目录的标签等于没有标签）
-- ---------------------------------------------------------------------------
CREATE TABLE dim_tag (
    tag_code   VARCHAR(16)  NOT NULL COMMENT '标签编码',
    tag_name   VARCHAR(32)  NOT NULL COMMENT '标签名称',
    tag_type   VARCHAR(8)   NOT NULL COMMENT '标签类型：客户/商品',
    tag_group  VARCHAR(16)  NOT NULL COMMENT '标签分组：人口属性/行为特征/价值分层/风险特征/商品属性',
    value_domain VARCHAR(128)    NULL COMMENT '值域说明',
    rule_desc  VARCHAR(255)      NULL COMMENT '计算口径',
    source_table VARCHAR(64)     NULL COMMENT '来源表',
    owner      VARCHAR(32)       NULL COMMENT '责任人',
    update_freq VARCHAR(16)      NULL COMMENT '更新频率',
    PRIMARY KEY (tag_code),
    KEY idx_tag_type (tag_type, tag_group)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标签目录（元数据）';

INSERT INTO dim_tag (tag_code, tag_name, tag_type, tag_group, value_domain, rule_desc, source_table, owner, update_freq) VALUES
('T01', '客户类型', '客户', '人口属性', '个人/企业', '取自客户主数据 customer_type', 'dws_customer_profile', '数据组', '日'),
('T02', '城市线级', '客户', '人口属性', '一线/新一线/其他', '按城市名称映射线级', 'dws_customer_profile', '数据组', '日'),
('T03', '会员等级', '客户', '人口属性', '普通/银卡/金卡/钻石/企业', '取自会员主数据', 'dws_customer_profile', '会员组', '日'),
('T04', '入会年份', '客户', '人口属性', 'YYYY', '首单日期所在年份', 'dws_customer_profile', '数据组', '日'),
('T05', '生命周期', '客户', '价值分层', '新客/活跃/沉睡/流失', 'R≤90 新客或活跃；91-180 沉睡；>180 流失', 'dws_customer_profile', '会员组', '日'),
('T06', '消费档位', '客户', '价值分层', '低/中/高/极高', '累计消费 <500/500-2000/2000-5000/≥5000', 'dws_customer_profile', '会员组', '日'),
('T07', '品类偏好', '客户', '行为特征', '花束品类', '按销售额占比最高的品类', 'dws_customer_profile', '商品组', '日'),
('T08', '价格带偏好', '客户', '行为特征', '价格带', '按销售额占比最高的价格带', 'dws_customer_profile', '商品组', '日'),
('T09', '渠道偏好', '客户', '行为特征', '渠道名称', '按销售额占比最高的渠道', 'dws_customer_profile', '运营组', '日'),
('T10', '节日型客户', '客户', '行为特征', '是/否', '节日订单金额占比 ≥60%', 'dws_customer_profile', '运营组', '日'),
('T11', '退款敏感', '客户', '风险特征', '是/否', '退款次数 ≥2', 'dws_customer_profile', '客服组', '日'),
('T12', '用券偏好', '客户', '风险特征', '是/否', '用券订单 ≥3 次', 'dws_customer_profile', '运营组', '日'),
('T13', '商品类型', '商品', '商品属性', 'SKU/花束', '取自商品主数据 product_type', 'dws_product_profile', '商品组', '日'),
('T14', '价格带', '商品', '商品属性', '50元以下/50-199/200-499/500以上', 'FN_PRICE_BAND 统一口径', 'dws_product_profile', '商品组', '日'),
('T15', '动销等级', '商品', '商品属性', 'A/B/C', 'ABC 分类：销售额累计 80%/95% 分界', 'dws_product_profile', '商品组', '日'),
('T16', '毛利等级', '商品', '商品属性', '高/中/低', '毛利率 ≥60%/45%-60%/<45%', 'dws_product_profile', '商品组', '日'),
('T17', '节令性', '商品', '商品属性', '是/否', '节日销量占比 ≥50%', 'dws_product_profile', '商品组', '日'),
('T18', '复购属性', '商品', '商品属性', '是/否', '复购客户占购买客户 ≥5%', 'dws_product_profile', '商品组', '日');

-- ---------------------------------------------------------------------------
-- 2. 客户标签宽表（一行一个客户，12 个标签）
-- ---------------------------------------------------------------------------
CREATE TABLE ads_customer_tags (
    customer_id     VARCHAR(12) NOT NULL COMMENT '客户编号',
    customer_type   VARCHAR(8)      NULL COMMENT 'T01 客户类型',
    city_tier       VARCHAR(8)      NULL COMMENT 'T02 城市线级',
    member_level    VARCHAR(16)     NULL COMMENT 'T03 会员等级',
    tenure_year     VARCHAR(8)      NULL COMMENT 'T04 入会年份',
    lifecycle_stage VARCHAR(16)     NULL COMMENT 'T05 生命周期',
    value_tier      VARCHAR(8)      NULL COMMENT 'T06 消费档位',
    category_pref   VARCHAR(16)     NULL COMMENT 'T07 品类偏好',
    price_pref      VARCHAR(16)     NULL COMMENT 'T08 价格带偏好',
    channel_pref    VARCHAR(32)     NULL COMMENT 'T09 渠道偏好',
    festival_type   VARCHAR(8)      NULL COMMENT 'T10 是否节日型',
    refund_sensitive VARCHAR(2)     NULL COMMENT 'T11 退款敏感',
    coupon_lover    VARCHAR(2)      NULL COMMENT 'T12 用券偏好',
    calc_date       DATE            NULL COMMENT '计算日期',
    PRIMARY KEY (customer_id),
    KEY idx_ct_lifecycle (lifecycle_stage),
    KEY idx_ct_value (value_tier),
    KEY idx_ct_city (city_tier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-客户标签宽表';

INSERT INTO ads_customer_tags
    (customer_id, customer_type, city_tier, member_level, tenure_year, lifecycle_stage,
     value_tier, category_pref, price_pref, channel_pref, festival_type,
     refund_sensitive, coupon_lover, calc_date)
SELECT
    p.customer_id,
    p.customer_type,
    CASE
        WHEN p.city IN ('上海市', '北京市', '深圳市') THEN '一线'
        WHEN p.city IN ('杭州市', '南京市', '苏州市', '成都市', '武汉市') THEN '新一线'
        ELSE '其他'
    END AS city_tier,
    p.member_level,
    DATE_FORMAT(p.first_order_date, '%Y') AS tenure_year,
    p.lifecycle_stage,
    CASE
        WHEN p.monetary >= 5000 THEN '极高'
        WHEN p.monetary >= 2000 THEN '高'
        WHEN p.monetary >= 500  THEN '中'
        ELSE '低'
    END AS value_tier,
    p.prefer_category,
    p.prefer_price_band,
    p.prefer_channel,
    IF(p.festival_ratio >= 0.6, '是', '否') AS festival_type,
    IF(p.refund_cnt >= 2, '是', '否') AS refund_sensitive,
    IF(p.coupon_cnt >= 3, '是', '否') AS coupon_lover,
    p.calc_date
FROM dws_customer_profile p;

-- ---------------------------------------------------------------------------
-- 3. 商品标签宽表（一行一个商品，6 个标签）
-- ---------------------------------------------------------------------------
CREATE TABLE ads_product_tags (
    product_id     VARCHAR(10) NOT NULL COMMENT '商品编号',
    product_type   VARCHAR(8)      NULL COMMENT 'T13 商品类型',
    price_band     VARCHAR(16)     NULL COMMENT 'T14 价格带',
    abc_class      VARCHAR(2)      NULL COMMENT 'T15 动销等级',
    profit_tier    VARCHAR(8)      NULL COMMENT 'T16 毛利等级',
    seasonal_flag  VARCHAR(2)      NULL COMMENT 'T17 节令性',
    repurchase_flag VARCHAR(2)     NULL COMMENT 'T18 复购属性',
    calc_date      DATE            NULL COMMENT '计算日期',
    PRIMARY KEY (product_id),
    KEY idx_pt_abc (abc_class),
    KEY idx_pt_price (price_band)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-商品标签宽表';

INSERT INTO ads_product_tags
    (product_id, product_type, price_band, abc_class, profit_tier,
     seasonal_flag, repurchase_flag, calc_date)
SELECT
    product_id,
    product_type,
    price_band,
    abc_class,
    CASE
        WHEN profit_rate >= 0.60 THEN '高'
        WHEN profit_rate >= 0.45 THEN '中'
        ELSE '低'
    END AS profit_tier,
    IF(festival_qty_ratio >= 0.5, '是', '否') AS seasonal_flag,
    IF(customer_cnt > 0 AND repeat_customer_cnt / customer_cnt >= 0.05, '是', '否') AS repurchase_flag,
    calc_date
FROM dws_product_profile;
