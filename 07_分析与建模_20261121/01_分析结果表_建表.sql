-- ============================================================================
--  01 · 第 3 轮分析结果表（ADS 层）
--
--  这些表是"分析结论"的落地形态：
--    · 第 4 轮 Web 看板直接 SELECT，不再在接口里跑复杂计算；
--    · 结果带 calc_date，可做历史对比（今天的分群 vs 上周的分群）；
--    · 每张表都能追溯到一段确定性 SQL/算法（见 bloom/analysis/*.py）。
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_dw;

DROP TABLE IF EXISTS ads_promo_roi;
DROP TABLE IF EXISTS ads_assoc_rules;
DROP TABLE IF EXISTS ads_forecast;
DROP TABLE IF EXISTS ads_channel_efficiency;
DROP TABLE IF EXISTS ads_inventory_analysis;

-- ---------------------------------------------------------------------------
-- 1. 促销活动 ROI
-- ---------------------------------------------------------------------------
CREATE TABLE ads_promo_roi (
    promotion_id    VARCHAR(12)   NOT NULL COMMENT '活动编号',
    promotion_name  VARCHAR(64)       NULL COMMENT '活动名称',
    promotion_type  VARCHAR(16)       NULL COMMENT '活动类型',
    festival        VARCHAR(16)       NULL COMMENT '关联节日',
    start_date      DATE              NULL COMMENT '开始日期',
    end_date        DATE              NULL COMMENT '结束日期',
    order_cnt       INT               NULL COMMENT '活动订单数',
    gmv             DECIMAL(16,2)     NULL COMMENT '活动 GMV',
    discount_amt    DECIMAL(14,2)     NULL COMMENT '折扣让利金额',
    gross_profit    DECIMAL(14,2)     NULL COMMENT '毛利',
    profit_rate     DECIMAL(8,4)      NULL COMMENT '毛利率',
    discount_rate   DECIMAL(8,4)      NULL COMMENT '折扣率',
    aov             DECIMAL(12,2)     NULL COMMENT '客单价',
    roi             DECIMAL(10,2)     NULL COMMENT 'ROI = 毛利 / 让利',
    calc_date       DATE              NULL COMMENT '计算日期',
    PRIMARY KEY (promotion_id),
    KEY idx_roi_type (promotion_type),
    KEY idx_roi_festival (festival)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-促销活动ROI';

-- ---------------------------------------------------------------------------
-- 2. 关联规则（Apriori 输出）
-- ---------------------------------------------------------------------------
CREATE TABLE ads_assoc_rules (
    rule_id     INT           NOT NULL AUTO_INCREMENT COMMENT '规则主键',
    rule_level  VARCHAR(16)   NOT NULL COMMENT '商品级/品类级',
    antecedent  VARCHAR(64)   NOT NULL COMMENT '前项（购买 A）',
    consequent  VARCHAR(64)   NOT NULL COMMENT '后项（也会买 B）',
    support     DECIMAL(8,4)  NOT NULL COMMENT '支持度',
    confidence  DECIMAL(8,4)  NOT NULL COMMENT '置信度',
    lift        DECIMAL(8,2)  NOT NULL COMMENT '提升度',
    order_cnt   INT               NULL COMMENT '同时出现的订单数',
    calc_date   DATE              NULL COMMENT '计算日期',
    PRIMARY KEY (rule_id),
    KEY idx_rule_level (rule_level),
    KEY idx_rule_lift (lift)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-商品关联规则';

-- ---------------------------------------------------------------------------
-- 3. 销量预测
-- ---------------------------------------------------------------------------
CREATE TABLE ads_forecast (
    forecast_date DATE          NOT NULL COMMENT '预测日期',
    predicted_gmv DECIMAL(14,2)     NULL COMMENT '预测 GMV',
    lower_bound   DECIMAL(14,2)     NULL COMMENT '置信区间下界',
    upper_bound   DECIMAL(14,2)     NULL COMMENT '置信区间上界',
    is_weekend    TINYINT           NULL COMMENT '是否周末',
    festival_name VARCHAR(32)       NULL COMMENT '节日',
    model_name    VARCHAR(32)       NULL COMMENT '模型',
    calc_date     DATE              NULL COMMENT '计算日期',
    PRIMARY KEY (forecast_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-日GMV预测';

-- ---------------------------------------------------------------------------
-- 4. 渠道效率
-- ---------------------------------------------------------------------------
CREATE TABLE ads_channel_efficiency (
    channel_id     VARCHAR(10)  NOT NULL COMMENT '渠道编号',
    channel_name   VARCHAR(32)      NULL COMMENT '渠道名称',
    channel_type   VARCHAR(8)       NULL COMMENT '线上/线下',
    order_cnt      INT              NULL COMMENT '订单数',
    customer_cnt   INT              NULL COMMENT '客户数',
    gmv            DECIMAL(16,2)    NULL COMMENT 'GMV',
    aov            DECIMAL(12,2)    NULL COMMENT '客单价',
    gmv_share      DECIMAL(8,4)     NULL COMMENT 'GMV 占比',
    commission_amt DECIMAL(14,2)    NULL COMMENT '平台佣金',
    net_profit     DECIMAL(16,2)    NULL COMMENT '佣金后毛利',
    net_margin     DECIMAL(8,4)     NULL COMMENT '佣金后净利率',
    calc_date      DATE             NULL COMMENT '计算日期',
    PRIMARY KEY (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-渠道效率';

-- ---------------------------------------------------------------------------
-- 5. 库存与损耗（按品类）
-- ---------------------------------------------------------------------------
CREATE TABLE ads_inventory_analysis (
    category       VARCHAR(16)  NOT NULL COMMENT '花材品类',
    sku_cnt        INT              NULL COMMENT 'SKU 数',
    out_qty        INT              NULL COMMENT '累计出库',
    on_hand_value  DECIMAL(16,2)    NULL COMMENT '平均库存金额',
    turnover_days  DECIMAL(10,2)    NULL COMMENT '周转天数',
    waste_rate     DECIMAL(8,4)     NULL COMMENT '损耗率',
    waste_amt      DECIMAL(14,2)    NULL COMMENT '损耗金额',
    low_stock_ratio DECIMAL(8,4)    NULL COMMENT '低库存天数占比',
    calc_date      DATE             NULL COMMENT '计算日期',
    PRIMARY KEY (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-库存与损耗分析';
