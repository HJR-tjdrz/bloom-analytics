-- ============================================================================
--  01 · DWD 清洗层 + 特征表建表
--
--  这一层和 04 里的 DWD 有什么不同？
--    04 的 fact_sales 是"已经洗干净后"直接由业务库构建的星型事实；
--    本文件建的 dwd_*_clean 是"从脏的 ODS 出发、经过规则清洗后重建"的明细层，
--    两者最终应当可以互相对账 —— 这正是本轮要证明的事：
--    **清洗链路没有丢数据，也没有造数据。**
--
--  设计要点：
--    1. 每张 clean 表都带 `repair_flags`：记录这一行被打了哪些修复补丁（如 E02,E06），
--       出问题能反查到"当时改了什么"，这是数据治理的审计要求；
--    2. 无法修复的行不硬修，进 `dwd_rejects` 异常表，带原因码与原始快照；
--    3. 维表补全（如单价为 0 用商品门市价回填）要留痕，不能静默改数。
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_dw;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS dwd_orders_clean;
DROP TABLE IF EXISTS dwd_order_items_clean;
DROP TABLE IF EXISTS dwd_customers_clean;
DROP TABLE IF EXISTS dwd_products_clean;
DROP TABLE IF EXISTS dwd_rejects;
DROP TABLE IF EXISTS dws_customer_profile;
DROP TABLE IF EXISTS dws_product_profile;
DROP TABLE IF EXISTS dws_daily_features;

-- ---------------------------------------------------------------------------
-- 1. 订单清洗层
-- ---------------------------------------------------------------------------
CREATE TABLE dwd_orders_clean (
    order_id               VARCHAR(24)   NOT NULL COMMENT '订单号',
    customer_id            VARCHAR(12)   NOT NULL COMMENT '客户编号（缺失时填 UNKNOWN）',
    store_id               VARCHAR(10)       NULL COMMENT '门店编号',
    channel_id             VARCHAR(10)       NULL COMMENT '渠道编号',
    order_time             DATETIME      NOT NULL COMMENT '下单时间（多格式已归一）',
    pay_time               DATETIME          NULL COMMENT '支付时间',
    status                 VARCHAR(16)   NOT NULL COMMENT '订单状态（已标准化）',
    delivery_type          VARCHAR(16)       NULL COMMENT '配送方式',
    expected_delivery_time DATETIME          NULL COMMENT '承诺送达时间',
    delivery_time          DATETIME          NULL COMMENT '实际送达时间',
    total_amount           DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '商品原价',
    discount_amount        DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '优惠金额',
    freight_amount         DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '运费',
    payable_amount         DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '实付金额',
    cost_amount            DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '成本',
    promotion_id           VARCHAR(12)       NULL COMMENT '活动编号',
    coupon_id              VARCHAR(10)       NULL COMMENT '优惠券编号',
    is_first_order         TINYINT       NOT NULL DEFAULT 0 COMMENT '是否首单',
    remark                 VARCHAR(255)      NULL COMMENT '备注（乱码已置空）',
    repair_flags           VARCHAR(64)       NULL COMMENT '修复标记：E01,E02…（逗号分隔）',
    source_batch           VARCHAR(32)       NULL COMMENT '来源 ODS 批次',
    etl_time               DATETIME          NULL COMMENT '入仓时间',
    PRIMARY KEY (order_id),
    KEY idx_dwd_order_time (order_time),
    KEY idx_dwd_order_status (status),
    KEY idx_dwd_order_customer (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWD-订单清洗层';

-- ---------------------------------------------------------------------------
-- 2. 明细清洗层
-- ---------------------------------------------------------------------------
CREATE TABLE dwd_order_items_clean (
    item_id         VARCHAR(32)   NOT NULL COMMENT '明细编号',
    order_id        VARCHAR(24)   NOT NULL COMMENT '订单号（已确认存在于订单表）',
    product_type    VARCHAR(8)    NOT NULL DEFAULT 'BUNDLE' COMMENT 'SKU/BUNDLE',
    sku_id          VARCHAR(10)       NULL COMMENT '花材编号',
    bundle_id       VARCHAR(10)       NULL COMMENT '花束编号',
    quantity        INT           NOT NULL COMMENT '数量（>0）',
    unit_price      DECIMAL(10,2) NOT NULL COMMENT '单价（为 0 时按门市价回填）',
    discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '分摊优惠',
    freight_amount  DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '分摊运费',
    line_amount     DECIMAL(12,2) NOT NULL COMMENT '行金额（= 数量×单价，已重算）',
    cost_amount     DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '行成本',
    repair_flags    VARCHAR(64)       NULL COMMENT '修复标记',
    source_batch    VARCHAR(32)       NULL COMMENT '来源批次',
    etl_time        DATETIME          NULL COMMENT '入仓时间',
    PRIMARY KEY (item_id),
    KEY idx_dwd_item_order (order_id),
    KEY idx_dwd_item_product (bundle_id, sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWD-订单明细清洗层';

-- ---------------------------------------------------------------------------
-- 3. 客户清洗层
-- ---------------------------------------------------------------------------
CREATE TABLE dwd_customers_clean (
    customer_id    VARCHAR(12) NOT NULL COMMENT '客户编号',
    customer_name  VARCHAR(64)     NULL COMMENT '客户名称',
    customer_type  VARCHAR(8)      NULL COMMENT '个人/企业',
    gender         VARCHAR(4)      NULL COMMENT '性别',
    phone          VARCHAR(20)     NULL COMMENT '手机号（非法置 NULL）',
    city           VARCHAR(32)     NULL COMMENT '城市（已标准化）',
    province       VARCHAR(32)     NULL COMMENT '省份',
    birthday       DATE            NULL COMMENT '生日',
    register_date  DATE            NULL COMMENT '注册日期',
    member_level   VARCHAR(16)     NULL COMMENT '会员等级（已标准化）',
    source_channel VARCHAR(32)     NULL COMMENT '注册来源',
    is_active      TINYINT     NOT NULL DEFAULT 1 COMMENT '是否活跃',
    repair_flags   VARCHAR(64)     NULL COMMENT '修复标记',
    source_batch   VARCHAR(32)     NULL COMMENT '来源批次',
    etl_time       DATETIME        NULL COMMENT '入仓时间',
    PRIMARY KEY (customer_id),
    KEY idx_dwd_cust_city (city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWD-客户清洗层';

-- ---------------------------------------------------------------------------
-- 4. 商品清洗层
-- ---------------------------------------------------------------------------
CREATE TABLE dwd_products_clean (
    sku_id          VARCHAR(10)   NOT NULL COMMENT '商品编号',
    sku_name        VARCHAR(64)       NULL COMMENT '商品名称',
    category        VARCHAR(16)       NULL COMMENT '品类',
    color           VARCHAR(16)       NULL COMMENT '颜色',
    unit            VARCHAR(8)        NULL COMMENT '单位',
    spec            VARCHAR(32)       NULL COMMENT '规格',
    cost_price      DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '成本价',
    list_price      DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '门市价（异常已回填）',
    shelf_life_days INT               NULL COMMENT '货架期',
    origin_place    VARCHAR(32)       NULL COMMENT '产地',
    supplier_id     VARCHAR(10)       NULL COMMENT '供应商',
    status          VARCHAR(16)       NULL COMMENT '状态',
    repair_flags    VARCHAR(64)       NULL COMMENT '修复标记',
    source_batch    VARCHAR(32)       NULL COMMENT '来源批次',
    etl_time        DATETIME          NULL COMMENT '入仓时间',
    PRIMARY KEY (sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWD-商品清洗层';

-- ---------------------------------------------------------------------------
-- 5. 异常数据隔离表（不硬修、不静默丢弃，全部留痕）
-- ---------------------------------------------------------------------------
CREATE TABLE dwd_rejects (
    reject_id   BIGINT       NOT NULL AUTO_INCREMENT COMMENT '隔离记录主键',
    batch_id    VARCHAR(32)  NOT NULL COMMENT 'ETL 批次',
    source_table VARCHAR(32) NOT NULL COMMENT '来源表',
    biz_key     VARCHAR(64)      NULL COMMENT '业务主键（订单号/明细号）',
    reason_code VARCHAR(16)  NOT NULL COMMENT '原因码：E03/E04/E15…',
    reason_desc VARCHAR(255)     NULL COMMENT '原因说明',
    payload     JSON             NULL COMMENT '原始行快照（JSON）',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (reject_id),
    KEY idx_reject_batch (batch_id),
    KEY idx_reject_reason (reason_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWD-异常数据隔离表';

-- ---------------------------------------------------------------------------
-- 6. 客户画像特征表（第 3 轮 RFM / CLV / 标签建模的输入）
-- ---------------------------------------------------------------------------
CREATE TABLE dws_customer_profile (
    customer_id      VARCHAR(12) NOT NULL COMMENT '客户编号',
    customer_type    VARCHAR(8)      NULL COMMENT '个人/企业',
    city             VARCHAR(32)     NULL COMMENT '城市',
    member_level     VARCHAR(16)     NULL COMMENT '会员等级',
    first_order_date DATE             NULL COMMENT '首单日期',
    last_order_date  DATE             NULL COMMENT '末单日期',
    recency_days     INT              NULL COMMENT '最近一次购买距数据截止日天数(R)',
    frequency        INT              NULL COMMENT '购买频次(F)',
    monetary         DECIMAL(14,2)    NULL COMMENT '累计实付金额(M)',
    avg_order_value  DECIMAL(12,2)    NULL COMMENT '客单价',
    total_qty        INT              NULL COMMENT '累计件数',
    active_days      INT              NULL COMMENT '有消费的天数',
    avg_interval_days DECIMAL(8,1)    NULL COMMENT '平均购买间隔(天)',
    prefer_category  VARCHAR(16)      NULL COMMENT '偏好花束品类',
    prefer_channel   VARCHAR(32)      NULL COMMENT '偏好渠道',
    prefer_price_band VARCHAR(16)     NULL COMMENT '偏好价格带',
    festival_ratio   DECIMAL(6,4)     NULL COMMENT '节日订单占比',
    refund_cnt       INT              NULL COMMENT '退款次数',
    coupon_cnt       INT              NULL COMMENT '用券次数',
    lifecycle_stage  VARCHAR(16)      NULL COMMENT '生命周期阶段',
    high_value_flag  TINYINT          NULL COMMENT '高价值客户(累计≥5000)',
    calc_date        DATE             NULL COMMENT '计算日期',
    PRIMARY KEY (customer_id),
    KEY idx_cp_lifecycle (lifecycle_stage),
    KEY idx_cp_member (member_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWS-客户画像特征';

-- ---------------------------------------------------------------------------
-- 7. 商品画像特征表（ABC / 波士顿矩阵的输入）
-- ---------------------------------------------------------------------------
CREATE TABLE dws_product_profile (
    product_id       VARCHAR(10)  NOT NULL COMMENT '商品编号',
    product_name     VARCHAR(64)      NULL COMMENT '商品名称',
    product_type     VARCHAR(8)       NULL COMMENT 'SKU/BUNDLE',
    category         VARCHAR(16)      NULL COMMENT '品类',
    price_band       VARCHAR(16)      NULL COMMENT '价格带',
    qty              INT              NULL COMMENT '累计销量',
    revenue          DECIMAL(16,2)    NULL COMMENT '累计销售额',
    cost             DECIMAL(16,2)    NULL COMMENT '累计成本',
    gross_profit     DECIMAL(16,2)    NULL COMMENT '累计毛利',
    profit_rate      DECIMAL(8,4)     NULL COMMENT '毛利率',
    order_cnt        INT              NULL COMMENT '出现订单数',
    customer_cnt     INT              NULL COMMENT '购买客户数',
    repeat_customer_cnt INT           NULL COMMENT '复购客户数',
    festival_qty_ratio DECIMAL(6,4)   NULL COMMENT '节日销量占比',
    yoy_growth       DECIMAL(8,4)     NULL COMMENT '同比增长率',
    revenue_share    DECIMAL(8,4)     NULL COMMENT '销售额份额',
    abc_class        VARCHAR(2)       NULL COMMENT 'ABC分类',
    calc_date        DATE             NULL COMMENT '计算日期',
    PRIMARY KEY (product_id),
    KEY idx_pp_category (category),
    KEY idx_pp_abc (abc_class)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWS-商品画像特征';

-- ---------------------------------------------------------------------------
-- 8. 日粒度特征表（第 3 轮时间序列预测的输入）
-- ---------------------------------------------------------------------------
CREATE TABLE dws_daily_features (
    date_key       INT           NOT NULL COMMENT '日期键',
    full_date      DATE          NOT NULL COMMENT '日期',
    order_cnt      INT           NOT NULL DEFAULT 0 COMMENT '订单数',
    customer_cnt   INT           NOT NULL DEFAULT 0 COMMENT '客户数',
    qty            INT           NOT NULL DEFAULT 0 COMMENT '件数',
    gmv            DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT 'GMV',
    aov            DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '客单价',
    gross_profit   DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '毛利',
    profit_rate    DECIMAL(8,4)  NOT NULL DEFAULT 0 COMMENT '毛利率',
    is_weekend     TINYINT       NOT NULL DEFAULT 0 COMMENT '是否周末',
    is_holiday     TINYINT       NOT NULL DEFAULT 0 COMMENT '是否法定节假日',
    festival_name  VARCHAR(32)       NULL COMMENT '节日名称',
    demand_index   DECIMAL(8,4)      NULL COMMENT '需求指数',
    lag_7_gmv      DECIMAL(14,2)     NULL COMMENT '7天前GMV（滞后特征）',
    lag_28_gmv     DECIMAL(14,2)     NULL COMMENT '28天前GMV',
    ma_7_gmv       DECIMAL(14,2)     NULL COMMENT '近7日移动平均',
    ma_28_gmv      DECIMAL(14,2)     NULL COMMENT '近28日移动平均',
    yoy_gmv        DECIMAL(14,2)     NULL COMMENT '去年同日GMV',
    PRIMARY KEY (date_key),
    KEY idx_df_date (full_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DWS-日粒度特征';

SET FOREIGN_KEY_CHECKS = 1;
