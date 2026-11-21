-- ============================================================================
--  04 · 索引、视图与分析型优化
--
--  优化思路（面试常问）：
--    1. 分析型查询是「大范围扫描 + 分组聚合」，索引不能只建在单列上，
--       要建在 WHERE 的等值列 + GROUP BY 列的组合上（最左前缀）；
--    2. 覆盖索引（covering index）让查询不回表，IO 直接减半；
--    3. 报表类查询用视图封装口径，避免每个看板各写一套 SQL 导致口径漂移；
--    4. 事实表按日期分区，历史冷数据可归档、可并行扫描。
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_oltp;

-- ---------------------------------------------------------------------------
-- 一、业务库补充索引（OLTP 侧的高频查询）
-- ---------------------------------------------------------------------------
-- 场景：客服按"状态 + 时间"捞最近订单（订单工作台首屏查询）
CREATE INDEX idx_order_status_time ON orders (status, order_time);

-- 场景：按订单追明细（覆盖索引，避免回表取 quantity/line_amount）
CREATE INDEX idx_item_order_cover ON order_items (order_id, product_type, quantity, line_amount);

-- 场景：客户列表按城市 + 最近下单排序
CREATE INDEX idx_cust_city_register ON customers (city, register_date);

USE bloom_dw;

-- ---------------------------------------------------------------------------
-- 二、数仓补充索引（OLAP 侧的高频查询）
-- ---------------------------------------------------------------------------
-- 场景：DWS 重建 / 按日期+门店+渠道下钻（最左前缀：date_key 选择性最高）
CREATE INDEX idx_fact_date_store_channel ON fact_sales (date_key, store_sk, channel_sk);

-- 场景：RFM 计算按客户聚合（覆盖索引，索引内即可完成 GROUP BY）
CREATE INDEX idx_fact_customer_cover ON fact_sales (customer_sk, order_time, net_amount, order_status);

-- 场景：商品 ABC / 波士顿矩阵按商品聚合
CREATE INDEX idx_fact_product_cover ON fact_sales (product_sk, net_amount, quantity, cost_amount);

-- ---------------------------------------------------------------------------
-- 三、分析视图：把口径固化下来
-- ---------------------------------------------------------------------------

-- 订单宽视图：把散落在多表的字段拼成"一行看懂一个订单"
-- 注意：视图建在 bloom_dw 里，但数据源在 bloom_oltp，必须写全库名，
--       否则 MySQL 会把 orders 解析成 bloom_dw.orders 而报 Table doesn't exist。
CREATE OR REPLACE VIEW v_order_detail AS
SELECT
    o.order_id,
    DATE(o.order_time)                AS order_date,
    o.order_time,
    o.status,
    o.delivery_type,
    c.customer_id,
    c.customer_name,
    c.customer_type,
    c.city,
    ch.channel_name,
    s.store_name,
    COUNT(i.item_id)                  AS item_lines,
    SUM(i.quantity)                   AS total_qty,
    o.total_amount,
    o.discount_amount,
    o.freight_amount,
    o.payable_amount,
    ROUND(o.payable_amount - o.cost_amount, 2) AS gross_profit,
    o.promotion_id,
    o.coupon_id,
    o.is_first_order
FROM bloom_oltp.orders o
LEFT JOIN bloom_oltp.customers c  ON c.customer_id = o.customer_id
LEFT JOIN bloom_oltp.channels  ch ON ch.channel_id = o.channel_id
LEFT JOIN bloom_oltp.stores    s  ON s.store_id    = o.store_id
LEFT JOIN bloom_oltp.order_items i ON i.order_id  = o.order_id
GROUP BY o.order_id, o.order_time, o.status, o.delivery_type,
         c.customer_id, c.customer_name, c.customer_type, c.city,
         ch.channel_name, s.store_name, o.total_amount, o.discount_amount,
         o.freight_amount, o.payable_amount, o.cost_amount, o.promotion_id,
         o.coupon_id, o.is_first_order;

-- 销售明细视图：事实表 + 维度，供临时分析/取数直接用
CREATE OR REPLACE VIEW v_sales_detail AS
SELECT
    f.date_key,
    d.full_date,
    d.year,
    d.month_key,
    d.day_name,
    d.is_weekend,
    d.is_holiday,
    d.holiday_name,
    d.is_festival,
    d.festival_name,
    dc.customer_id,
    dc.customer_name,
    dc.customer_type,
    dc.city,
    dp.product_id,
    dp.product_name,
    dp.product_type,
    dp.category,
    dp.price_band,
    ds.store_name,
    ds.city AS store_city,
    dch.channel_name,
    dch.channel_type,
    f.order_id,
    f.quantity,
    f.gross_amount,
    f.discount_amount,
    f.freight_amount,
    f.net_amount,
    f.cost_amount,
    f.gross_profit,
    f.order_status,
    f.is_refunded,
    f.is_first_order
FROM fact_sales f
JOIN dim_date      d   ON d.date_key     = f.date_key
JOIN dim_customer  dc  ON dc.customer_sk = f.customer_sk
JOIN dim_product   dp  ON dp.product_sk  = f.product_sk
JOIN dim_store     ds  ON ds.store_sk    = f.store_sk
JOIN dim_channel   dch ON dch.channel_sk = f.channel_sk;

-- 客户价值视图：RFM 的取数底表（第3轮在其上做打分与分群）
CREATE OR REPLACE VIEW v_customer_value AS
SELECT
    f.customer_sk,
    dc.customer_id,
    dc.customer_name,
    dc.customer_type,
    dc.city,
    dc.member_level,
    MIN(d.full_date)                        AS first_order_date,
    MAX(d.full_date)                        AS last_order_date,
    COUNT(DISTINCT f.order_id)              AS frequency,
    SUM(f.net_amount + f.freight_amount)    AS monetary,
    ROUND(AVG(f.net_amount + f.freight_amount), 2) AS avg_order_value,
    SUM(f.quantity)                         AS total_qty
FROM fact_sales f
JOIN dim_date     d  ON d.date_key     = f.date_key
JOIN dim_customer dc ON dc.customer_sk = f.customer_sk
WHERE f.order_status <> '已取消'
GROUP BY f.customer_sk, dc.customer_id, dc.customer_name, dc.customer_type,
         dc.city, dc.member_level;

-- 日经营视图：看板首页"今日/近7日/近30日"直接查它
CREATE OR REPLACE VIEW v_daily_kpi AS
SELECT
    d.full_date,
    d.month_key,
    d.day_name,
    d.is_weekend,
    d.is_festival,
    d.festival_name,
    COUNT(DISTINCT f.order_id)                AS order_cnt,
    COUNT(DISTINCT f.customer_sk)             AS customer_cnt,
    SUM(f.quantity)                           AS qty,
    SUM(f.net_amount + f.freight_amount)      AS gmv,
    ROUND(SUM(f.net_amount + f.freight_amount) / COUNT(DISTINCT f.order_id), 2) AS aov,
    ROUND(SUM(f.gross_profit), 2)             AS gross_profit,
    ROUND(SUM(f.gross_profit) / SUM(f.net_amount + f.freight_amount), 4) AS profit_rate
FROM fact_sales f
JOIN dim_date d ON d.date_key = f.date_key
WHERE f.order_status <> '已取消'
GROUP BY d.full_date, d.month_key, d.day_name, d.is_weekend, d.is_festival, d.festival_name;

-- ---------------------------------------------------------------------------
-- 四、分区表实验：事实表按年分区
--
-- 真实项目里 3 年数据还能扛，5 年以上就必须分区/归档了。
-- 这里用同构表做对照，验证「分区裁剪（partition pruning）」的收益。
-- 注意：分区表的主键必须包含分区键，所以主键改为 (order_item_key, date_key)。
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS fact_sales_part;
CREATE TABLE fact_sales_part (
    order_item_key BIGINT       NOT NULL AUTO_INCREMENT,
    item_id        VARCHAR(32)  NOT NULL,
    order_id       VARCHAR(24)  NOT NULL,
    date_key       INT          NOT NULL,
    customer_sk    BIGINT       NOT NULL,
    product_sk     INT          NOT NULL,
    store_sk       INT          NOT NULL,
    channel_sk     INT          NOT NULL,
    promotion_sk   INT          NOT NULL DEFAULT 0,
    quantity       INT          NOT NULL,
    gross_amount   DECIMAL(12,2) NOT NULL,
    discount_amount DECIMAL(12,2) NOT NULL,
    freight_amount DECIMAL(12,2) NOT NULL,
    net_amount     DECIMAL(12,2) NOT NULL,
    cost_amount    DECIMAL(12,2) NOT NULL,
    gross_profit   DECIMAL(12,2) NOT NULL,
    order_status   VARCHAR(16)  NOT NULL,
    is_refunded    TINYINT      NOT NULL DEFAULT 0,
    is_first_order TINYINT      NOT NULL DEFAULT 0,
    order_time     DATETIME     NOT NULL,
    PRIMARY KEY (order_item_key, date_key),
    KEY idx_part_date (date_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='事实表分区实验（按年RANGE分区）'
PARTITION BY RANGE (date_key) (
    PARTITION p2023 VALUES LESS THAN (20240101),
    PARTITION p2024 VALUES LESS THAN (20250101),
    PARTITION p2025 VALUES LESS THAN (20260101),
    PARTITION p2026 VALUES LESS THAN (20270101),
    PARTITION pmax  VALUES LESS THAN MAXVALUE
);
