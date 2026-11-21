-- ============================================================================
--  03 · 数仓分层建表（bloom_dw）
--  分层：ODS（贴源） → DWD/DIM（清洗+维度） → DWS（轻度汇总） → ADS（应用层）
--
--  为什么分四层？
--    ODS：原始落地，脏数据也要原样保留，出问题能追溯（不丢数据）
--    DIM/FACT：做清洗、去重、标准化，按星型模型组织，保证口径唯一
--    DWS：按"天×门店×渠道"预聚合，把重复计算下沉，查询提速 10 倍以上
--    ADS：直接面向报表/看板的宽表，前端只需 SELECT，不做 JOIN
--
--  执行账号：bloom_app
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_dw;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS ads_customer_rfm;
DROP TABLE IF EXISTS ads_product_performance;
DROP TABLE IF EXISTS ads_channel_monthly;
DROP TABLE IF EXISTS ads_kpi_monthly;
DROP TABLE IF EXISTS dws_sales_daily;
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_promotion;
DROP TABLE IF EXISTS dim_store;
DROP TABLE IF EXISTS dim_channel;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS ods_products_raw;
DROP TABLE IF EXISTS ods_customers_raw;
DROP TABLE IF EXISTS ods_order_items_raw;
DROP TABLE IF EXISTS ods_orders_raw;

-- ============================================================================
--  第一层：ODS 贴源层
--  设计要点：所有字段一律 VARCHAR —— 脏数据（"已完成 "、负金额、1999 年）
--  在 ODS 必须能装得进去，清洗动作放在 DWD，绝不能因为脏数据导不进来。
-- ============================================================================
CREATE TABLE ods_orders_raw (
    ods_id                 BIGINT       NOT NULL AUTO_INCREMENT COMMENT 'ODS自增主键',
    batch_id               VARCHAR(32)      NULL COMMENT '装载批次',
    load_time              DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '装载时间',
    order_id               VARCHAR(32)      NULL COMMENT '订单号(原始)',
    customer_id            VARCHAR(32)      NULL COMMENT '客户编号(原始)',
    store_id               VARCHAR(32)      NULL COMMENT '门店编号',
    channel_id             VARCHAR(32)      NULL COMMENT '渠道编号',
    order_time             VARCHAR(64)      NULL COMMENT '下单时间(原始字符串)',
    pay_time               VARCHAR(64)      NULL COMMENT '支付时间(原始)',
    status                 VARCHAR(32)      NULL COMMENT '订单状态(原始，可能带空格/英文)',
    delivery_type          VARCHAR(32)      NULL COMMENT '配送方式',
    expected_delivery_time VARCHAR(64)      NULL COMMENT '承诺送达时间(原始)',
    delivery_time          VARCHAR(64)      NULL COMMENT '实际送达时间(原始)',
    total_amount           VARCHAR(32)      NULL COMMENT '商品原价(原始)',
    discount_amount        VARCHAR(32)      NULL COMMENT '优惠金额(原始)',
    freight_amount         VARCHAR(32)      NULL COMMENT '运费(原始)',
    payable_amount         VARCHAR(32)      NULL COMMENT '实付金额(原始，可能为负)',
    cost_amount            VARCHAR(32)      NULL COMMENT '成本(原始)',
    promotion_id           VARCHAR(32)      NULL COMMENT '活动编号',
    coupon_id              VARCHAR(32)      NULL COMMENT '优惠券编号',
    is_first_order         VARCHAR(8)       NULL COMMENT '是否首单',
    remark                 VARCHAR(512)     NULL COMMENT '备注(可能含乱码/emoji)',
    PRIMARY KEY (ods_id),
    KEY idx_ods_order (order_id),
    KEY idx_ods_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS-订单原始表（贴源，不设任何约束）';

CREATE TABLE ods_order_items_raw (
    ods_id          BIGINT      NOT NULL AUTO_INCREMENT,
    batch_id        VARCHAR(32)     NULL,
    load_time       DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    item_id         VARCHAR(64)     NULL,
    order_id        VARCHAR(32)     NULL,
    product_type    VARCHAR(16)     NULL,
    sku_id          VARCHAR(32)     NULL,
    bundle_id       VARCHAR(32)     NULL,
    quantity        VARCHAR(16)     NULL,
    unit_price      VARCHAR(32)     NULL,
    discount_amount VARCHAR(32)     NULL,
    freight_amount  VARCHAR(32)     NULL,
    line_amount     VARCHAR(32)     NULL,
    cost_amount     VARCHAR(32)     NULL,
    PRIMARY KEY (ods_id),
    KEY idx_ods_item_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS-订单明细原始表';

CREATE TABLE ods_customers_raw (
    ods_id         BIGINT      NOT NULL AUTO_INCREMENT,
    batch_id       VARCHAR(32)     NULL,
    load_time      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    customer_id    VARCHAR(32)     NULL,
    customer_name  VARCHAR(128)    NULL,
    customer_type  VARCHAR(32)     NULL,
    gender         VARCHAR(16)     NULL,
    phone          VARCHAR(32)     NULL,
    city           VARCHAR(64)     NULL,
    province       VARCHAR(64)     NULL,
    birthday       VARCHAR(32)     NULL,
    register_date  VARCHAR(32)     NULL,
    member_level   VARCHAR(64)     NULL,
    source_channel VARCHAR(64)     NULL,
    is_active      VARCHAR(8)      NULL,
    PRIMARY KEY (ods_id),
    KEY idx_ods_cust (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS-客户原始表';

CREATE TABLE ods_products_raw (
    ods_id          BIGINT      NOT NULL AUTO_INCREMENT,
    batch_id        VARCHAR(32)     NULL,
    load_time       DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sku_id          VARCHAR(32)     NULL,
    sku_name        VARCHAR(128)    NULL,
    category        VARCHAR(32)     NULL,
    color           VARCHAR(32)     NULL,
    unit            VARCHAR(16)     NULL,
    spec            VARCHAR(64)     NULL,
    cost_price      VARCHAR(32)     NULL,
    list_price      VARCHAR(32)     NULL,
    shelf_life_days VARCHAR(16)     NULL,
    origin_place    VARCHAR(64)     NULL,
    supplier_id     VARCHAR(32)     NULL,
    status          VARCHAR(32)     NULL,
    PRIMARY KEY (ods_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ODS-商品原始表';

-- ============================================================================
--  第二层：DIM 维度层（星型模型）
-- ============================================================================

-- 日期维表：含业务日历（节假日、节日、需求指数），是趋势分析与预测的基础
CREATE TABLE dim_date (
    date_key       INT          NOT NULL COMMENT '日期主键 yyyymmdd',
    full_date      DATE         NOT NULL COMMENT '日期',
    year           INT          NOT NULL COMMENT '年',
    quarter        INT          NOT NULL COMMENT '季度',
    month          INT          NOT NULL COMMENT '月',
    month_name     VARCHAR(16)      NULL COMMENT '月份中文名',
    week_of_year   INT              NULL COMMENT 'ISO周序号',
    day_of_week    INT              NULL COMMENT '1=周一 … 7=周日',
    day_name       VARCHAR(8)       NULL COMMENT '星期中文名',
    is_weekend     TINYINT      NOT NULL DEFAULT 0 COMMENT '是否周末',
    is_holiday     TINYINT      NOT NULL DEFAULT 0 COMMENT '是否法定节假日',
    holiday_name   VARCHAR(32)      NULL COMMENT '节假日名称',
    is_festival    TINYINT      NOT NULL DEFAULT 0 COMMENT '是否营销节日',
    festival_name  VARCHAR(32)      NULL COMMENT '节日名称',
    season         VARCHAR(8)       NULL COMMENT '季节',
    month_key     VARCHAR(8)   NOT NULL COMMENT '年月 yyyy-mm',
    demand_index   DECIMAL(8,4)     NULL COMMENT '需求指数(季节×星期×节日×增长)',
    PRIMARY KEY (date_key),
    KEY idx_dim_date_ym (month_key),
    -- 事实表按 DATE(order_time) 关联日期维时必须能命中 full_date 索引，
    -- 否则优化器会把 1000 多行的 dim_date 当驱动表做全表嵌套（实测慢 30 倍）
    KEY idx_dim_date_full (full_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日期维度（业务日历）';

-- 客户维度（SCD2 拉链表）：会员等级变化会保留历史版本，保证历史订单口径正确
CREATE TABLE dim_customer (
    customer_sk    BIGINT      NOT NULL AUTO_INCREMENT COMMENT '客户代理键',
    customer_id    VARCHAR(12) NOT NULL COMMENT '客户业务编号',
    customer_name  VARCHAR(64)     NULL COMMENT '客户名称',
    customer_type  VARCHAR(8)      NULL COMMENT '个人/企业',
    gender         VARCHAR(4)      NULL COMMENT '性别',
    city           VARCHAR(32)     NULL COMMENT '城市',
    province       VARCHAR(32)     NULL COMMENT '省份',
    member_level   VARCHAR(16)     NULL COMMENT '会员等级(历史版本)',
    source_channel VARCHAR(32)     NULL COMMENT '注册来源',
    register_date  DATE            NULL COMMENT '注册日期',
    valid_from     DATETIME    NOT NULL COMMENT '版本生效时间',
    valid_to       DATETIME        NULL COMMENT '版本失效时间(NULL=当前)',
    is_current     TINYINT     NOT NULL DEFAULT 1 COMMENT '是否当前版本',
    version        INT         NOT NULL DEFAULT 1 COMMENT '版本号',
    PRIMARY KEY (customer_sk),
    KEY idx_dim_cust_id (customer_id, is_current),
    KEY idx_dim_cust_city (city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户维度（SCD2拉链表）';

-- 商品维度：SKU 与花束统一到一张维表，避免事实表出现两种粒度
CREATE TABLE dim_product (
    product_sk   INT          NOT NULL AUTO_INCREMENT COMMENT '商品代理键',
    product_id   VARCHAR(10)  NOT NULL COMMENT '商品业务编号(SKU/花束)',
    product_type VARCHAR(8)   NOT NULL COMMENT 'SKU/BUNDLE',
    product_name VARCHAR(64)  NOT NULL COMMENT '商品名称',
    category     VARCHAR(16)      NULL COMMENT '品类',
    scene        VARCHAR(16)      NULL COMMENT '场景(花束)',
    unit_price   DECIMAL(10,2)    NULL COMMENT '门市价',
    cost_price   DECIMAL(10,2)    NULL COMMENT '成本价',
    price_band   VARCHAR(16)      NULL COMMENT '价格带：<50/50-199/200-499/500+',
    launch_date  DATE             NULL COMMENT '上市日期',
    status       VARCHAR(16)      NULL COMMENT '状态',
    PRIMARY KEY (product_sk),
    UNIQUE KEY uk_dim_product (product_id),
    KEY idx_dim_product_cat (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品维度（SKU+花束）';

CREATE TABLE dim_channel (
    channel_sk      INT          NOT NULL AUTO_INCREMENT,
    channel_id      VARCHAR(10)  NOT NULL,
    channel_name    VARCHAR(32)  NOT NULL,
    channel_type    VARCHAR(8)   NOT NULL,
    commission_rate DECIMAL(5,4) NOT NULL DEFAULT 0 COMMENT '佣金率',
    PRIMARY KEY (channel_sk),
    UNIQUE KEY uk_dim_channel (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='渠道维度';

CREATE TABLE dim_store (
    store_sk    INT         NOT NULL AUTO_INCREMENT,
    store_id    VARCHAR(10) NOT NULL,
    store_name  VARCHAR(64) NOT NULL,
    city        VARCHAR(32) NOT NULL,
    province    VARCHAR(32) NOT NULL,
    area_sqm    INT             NULL,
    staff_count INT             NULL,
    open_date   DATE            NULL,
    PRIMARY KEY (store_sk),
    UNIQUE KEY uk_dim_store (store_id),
    KEY idx_dim_store_city (city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='门店维度';

CREATE TABLE dim_promotion (
    promotion_sk   INT           NOT NULL AUTO_INCREMENT,
    promotion_id   VARCHAR(12)   NOT NULL,
    promotion_name VARCHAR(64)   NOT NULL,
    promotion_type VARCHAR(16)   NOT NULL,
    festival       VARCHAR(16)       NULL,
    discount_rate  DECIMAL(5,4)  NOT NULL DEFAULT 1,
    start_date     DATE          NOT NULL,
    end_date       DATE          NOT NULL,
    PRIMARY KEY (promotion_sk),
    UNIQUE KEY uk_dim_promotion (promotion_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='促销维度';

-- ============================================================================
--  第三层：FACT 事实表（星型中心，粒度 = 订单明细行）
-- ============================================================================
CREATE TABLE fact_sales (
    order_item_key BIGINT       NOT NULL AUTO_INCREMENT COMMENT '事实主键',
    item_id        VARCHAR(32)  NOT NULL COMMENT '明细编号',
    order_id       VARCHAR(24)  NOT NULL COMMENT '订单号',
    date_key       INT          NOT NULL COMMENT '下单日期键',
    customer_sk    BIGINT       NOT NULL COMMENT '客户代理键',
    product_sk     INT          NOT NULL COMMENT '商品代理键',
    store_sk       INT          NOT NULL COMMENT '门店代理键',
    channel_sk     INT          NOT NULL COMMENT '渠道代理键',
    promotion_sk   INT          NOT NULL DEFAULT 0 COMMENT '促销代理键(0=无活动)',
    quantity       INT          NOT NULL COMMENT '数量',
    gross_amount   DECIMAL(12,2) NOT NULL COMMENT '原价金额',
    discount_amount DECIMAL(12,2) NOT NULL COMMENT '优惠金额',
    freight_amount DECIMAL(12,2) NOT NULL COMMENT '运费',
    net_amount     DECIMAL(12,2) NOT NULL COMMENT '商品实收=原价-优惠',
    cost_amount    DECIMAL(12,2) NOT NULL COMMENT '商品成本',
    gross_profit   DECIMAL(12,2) NOT NULL COMMENT '商品毛利=实收-成本',
    order_status   VARCHAR(16)  NOT NULL COMMENT '订单状态',
    is_refunded    TINYINT      NOT NULL DEFAULT 0 COMMENT '是否退款',
    is_first_order TINYINT      NOT NULL DEFAULT 0 COMMENT '是否首单',
    order_time     DATETIME     NOT NULL COMMENT '下单时间(退化维度)',
    PRIMARY KEY (order_item_key),
    KEY idx_fact_date (date_key),
    KEY idx_fact_customer (customer_sk),
    KEY idx_fact_product (product_sk),
    KEY idx_fact_store_channel (store_sk, channel_sk),
    KEY idx_fact_status (order_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售明细事实表（星型中心）';

-- ============================================================================
--  第四层：DWS 轻度汇总层
-- ============================================================================
CREATE TABLE dws_sales_daily (
    date_key       INT          NOT NULL COMMENT '日期键',
    store_sk       INT          NOT NULL COMMENT '门店代理键',
    channel_sk     INT          NOT NULL COMMENT '渠道代理键',
    order_cnt      INT          NOT NULL DEFAULT 0 COMMENT '订单数',
    item_cnt       INT          NOT NULL DEFAULT 0 COMMENT '明细行数',
    customer_cnt   INT          NOT NULL DEFAULT 0 COMMENT '客户数',
    quantity       INT          NOT NULL DEFAULT 0 COMMENT '件数',
    gmv            DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '实付金额(含运费)',
    item_revenue   DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '商品实收',
    discount_amt   DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '优惠金额',
    freight_amt    DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '运费',
    cost_amt       DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '成本',
    gross_profit   DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '毛利',
    PRIMARY KEY (date_key, store_sk, channel_sk),
    KEY idx_dws_channel (channel_sk),
    KEY idx_dws_store (store_sk)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日×门店×渠道销售汇总';

-- ============================================================================
--  第五层：ADS 应用层（看板/报表直接查这层，不再 JOIN）
-- ============================================================================
CREATE TABLE ads_kpi_monthly (
    month_key       VARCHAR(8)   NOT NULL COMMENT '年月',
    order_cnt        INT          NOT NULL DEFAULT 0 COMMENT '订单数',
    item_qty         INT          NOT NULL DEFAULT 0 COMMENT '销售件数',
    customer_cnt     INT          NOT NULL DEFAULT 0 COMMENT '成交客户数',
    new_customer_cnt INT          NOT NULL DEFAULT 0 COMMENT '新客数',
    gmv              DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT 'GMV(实付含运费)',
    item_revenue     DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '商品实收',
    discount_amt     DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '优惠总额',
    freight_amt      DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '运费收入',
    cost_amt         DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '成本',
    gross_profit     DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '毛利',
    aov              DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '客单价',
    profit_rate      DECIMAL(8,4)  NOT NULL DEFAULT 0 COMMENT '毛利率',
    refund_amt       DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '退款金额',
    PRIMARY KEY (month_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-月度经营KPI';

CREATE TABLE ads_channel_monthly (
    month_key     VARCHAR(8)   NOT NULL COMMENT '年月',
    channel_id     VARCHAR(10)  NOT NULL COMMENT '渠道编号',
    channel_name   VARCHAR(32)  NOT NULL COMMENT '渠道名称',
    order_cnt      INT          NOT NULL DEFAULT 0 COMMENT '订单数',
    customer_cnt   INT          NOT NULL DEFAULT 0 COMMENT '客户数',
    gmv            DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT 'GMV',
    aov            DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '客单价',
    commission_amt DECIMAL(14,2) NOT NULL DEFAULT 0 COMMENT '平台佣金',
    gross_profit   DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '毛利(扣佣金后)',
    profit_rate    DECIMAL(8,4)  NOT NULL DEFAULT 0 COMMENT '利润率',
    PRIMARY KEY (month_key, channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-渠道月度经营';

CREATE TABLE ads_customer_rfm (
    customer_id    VARCHAR(12)  NOT NULL COMMENT '客户编号',
    recency_days   INT          NOT NULL COMMENT '最近一次购买距今天数(R)',
    frequency      INT          NOT NULL COMMENT '购买频次(F)',
    monetary       DECIMAL(14,2) NOT NULL COMMENT '累计消费金额(M)',
    r_score        TINYINT      NOT NULL COMMENT 'R得分1-5',
    f_score        TINYINT      NOT NULL COMMENT 'F得分1-5',
    m_score        TINYINT      NOT NULL COMMENT 'M得分1-5',
    rfm_segment    VARCHAR(32)      NULL COMMENT 'RFM分群：重要价值/重要保持/…',
    cluster_label  TINYINT          NULL COMMENT 'K-Means聚类标签',
    calc_date      DATE             NULL COMMENT '计算日期',
    PRIMARY KEY (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-RFM用户价值分层（第3轮建模填充）';

CREATE TABLE ads_product_performance (
    product_id    VARCHAR(10)  NOT NULL COMMENT '商品编号',
    product_name  VARCHAR(64)  NOT NULL COMMENT '商品名称',
    product_type  VARCHAR(8)   NOT NULL COMMENT 'SKU/BUNDLE',
    category      VARCHAR(16)      NULL COMMENT '品类',
    sales_qty     INT          NOT NULL DEFAULT 0 COMMENT '销量',
    sales_amt     DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '销售额',
    gross_profit  DECIMAL(16,2) NOT NULL DEFAULT 0 COMMENT '毛利',
    profit_rate   DECIMAL(8,4)  NOT NULL DEFAULT 0 COMMENT '毛利率',
    qty_share     DECIMAL(8,4)  NOT NULL DEFAULT 0 COMMENT '销量份额',
    growth_rate   DECIMAL(8,4)  NOT NULL DEFAULT 0 COMMENT '同比增长率',
    abc_class     VARCHAR(2)       NULL COMMENT 'ABC分类',
    bcg_class     VARCHAR(16)      NULL COMMENT '波士顿矩阵分类',
    calc_date     DATE             NULL COMMENT '计算日期',
    PRIMARY KEY (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ADS-商品经营表现/ABC/波士顿矩阵（第3轮建模填充）';

SET FOREIGN_KEY_CHECKS = 1;
