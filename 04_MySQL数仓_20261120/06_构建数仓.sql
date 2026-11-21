-- ============================================================================
--  06 · 数仓构建（维度 → 事实 → 汇总 → 应用）
--
--  前置条件：
--    1) 04 目录下 02/03/04/05 已执行完毕（建表 + 索引 + 视图 + 存储过程）
--    2) 业务库 bloom_oltp 已装载数据（10_装载业务库数据.bat）
--    3) dim_date / dim_customer / dim_product 已由 Python 装载
--       （这三张维度需要业务日历与 SCD2 逻辑，用 Python 处理更合适）
--
--  执行方式：python run.py dw（本文件由 Python 逐条执行）
--  说明：全部为「先清空再插入」，可重复执行，天然幂等。
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_dw;

-- 允许向自增列显式写入 0（用于"未知成员"，这是维度建模的常规做法）
SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION,NO_AUTO_VALUE_ON_ZERO';

-- ---------------------------------------------------------------------------
-- 1. 维度：渠道 / 门店 / 促销（结构简单，直接由业务库映射）
-- ---------------------------------------------------------------------------
TRUNCATE TABLE dim_channel;
INSERT INTO dim_channel (channel_id, channel_name, channel_type, commission_rate)
SELECT channel_id, channel_name, channel_type, commission_rate
FROM bloom_oltp.channels
ORDER BY channel_id;

TRUNCATE TABLE dim_store;
INSERT INTO dim_store (store_id, store_name, city, province, area_sqm, staff_count, open_date)
SELECT store_id, store_name, city, province, area_sqm, staff_count, open_date
FROM bloom_oltp.stores
ORDER BY store_id;

TRUNCATE TABLE dim_promotion;
-- 未知成员：订单未命中任何活动时挂到 sk=0，保证事实表外键不为空
INSERT INTO dim_promotion (promotion_sk, promotion_id, promotion_name, promotion_type,
                           festival, discount_rate, start_date, end_date)
VALUES (0, 'PM0000', '无促销', '无', '无', 1.0000, '1970-01-01', '1970-01-01');

INSERT INTO dim_promotion (promotion_id, promotion_name, promotion_type, festival,
                           discount_rate, start_date, end_date)
SELECT promotion_id, promotion_name, promotion_type, festival,
       discount_rate, start_date, end_date
FROM bloom_oltp.promotions
ORDER BY start_date, promotion_id;

-- 商品价格带：统一用 05 里定义的函数计算，避免各处口径不一致
UPDATE dim_product SET price_band = fn_price_band(unit_price);

-- ---------------------------------------------------------------------------
-- 2. 事实表：订单明细 × 维度（星型模型落库）
-- ---------------------------------------------------------------------------
TRUNCATE TABLE fact_sales;

INSERT INTO fact_sales
    (item_id, order_id, date_key, customer_sk, product_sk, store_sk, channel_sk, promotion_sk,
     quantity, gross_amount, discount_amount, freight_amount, net_amount, cost_amount,
     gross_profit, order_status, is_refunded, is_first_order, order_time)
SELECT
    i.item_id,
    i.order_id,
    dd.date_key,
    dc.customer_sk,
    dp.product_sk,
    ds.store_sk,
    dch.channel_sk,
    COALESCE(dpr.promotion_sk, 0)                    AS promotion_sk,
    i.quantity,
    i.line_amount                                    AS gross_amount,
    i.discount_amount,
    i.freight_amount,
    i.line_amount - i.discount_amount                AS net_amount,
    i.cost_amount,
    (i.line_amount - i.discount_amount) - i.cost_amount AS gross_profit,
    o.status,
    IF(o.status = '已退款', 1, 0)                     AS is_refunded,
    o.is_first_order,
    o.order_time
FROM bloom_oltp.order_items i
JOIN bloom_oltp.orders  o   ON o.order_id   = i.order_id
JOIN dim_date           dd  ON dd.full_date = DATE(o.order_time)
JOIN dim_customer       dc  ON dc.customer_id = o.customer_id AND dc.is_current = 1
JOIN dim_product        dp  ON dp.product_id  = COALESCE(i.bundle_id, i.sku_id)
JOIN dim_store          ds  ON ds.store_id    = o.store_id
JOIN dim_channel        dch ON dch.channel_id = o.channel_id
LEFT JOIN dim_promotion dpr ON dpr.promotion_id = o.promotion_id;

-- ---------------------------------------------------------------------------
-- 3. DWS：日 × 门店 × 渠道 轻度汇总
--    （取消订单不计入经营口径，但保留在事实表里供分析"为什么取消"）
-- ---------------------------------------------------------------------------
TRUNCATE TABLE dws_sales_daily;

INSERT INTO dws_sales_daily
    (date_key, store_sk, channel_sk, order_cnt, item_cnt, customer_cnt, quantity,
     gmv, item_revenue, discount_amt, freight_amt, cost_amt, gross_profit)
SELECT
    f.date_key,
    f.store_sk,
    f.channel_sk,
    COUNT(DISTINCT f.order_id)                    AS order_cnt,
    COUNT(*)                                      AS item_cnt,
    COUNT(DISTINCT f.customer_sk)                  AS customer_cnt,
    SUM(f.quantity)                               AS quantity,
    SUM(f.net_amount + f.freight_amount)          AS gmv,
    SUM(f.net_amount)                             AS item_revenue,
    SUM(f.discount_amount)                        AS discount_amt,
    SUM(f.freight_amount)                         AS freight_amt,
    SUM(f.cost_amount)                            AS cost_amt,
    SUM(f.gross_profit)                           AS gross_profit
FROM fact_sales f
WHERE f.order_status <> '已取消'
GROUP BY f.date_key, f.store_sk, f.channel_sk;

-- ---------------------------------------------------------------------------
-- 4. ADS：月度经营 KPI（看板首页 + 经营分析报告的数据源）
-- ---------------------------------------------------------------------------
TRUNCATE TABLE ads_kpi_monthly;

INSERT INTO ads_kpi_monthly
    (month_key, order_cnt, item_qty, customer_cnt, new_customer_cnt,
     gmv, item_revenue, discount_amt, freight_amt, cost_amt, gross_profit,
     aov, profit_rate, refund_amt)
SELECT
    dd.month_key,
    COUNT(DISTINCT f.order_id)                                     AS order_cnt,
    SUM(f.quantity)                                                AS item_qty,
    COUNT(DISTINCT f.customer_sk)                                  AS customer_cnt,
    COUNT(DISTINCT CASE WHEN f.is_first_order = 1 THEN f.customer_sk END) AS new_customer_cnt,
    SUM(f.net_amount + f.freight_amount)                           AS gmv,
    SUM(f.net_amount)                                              AS item_revenue,
    SUM(f.discount_amount)                                         AS discount_amt,
    SUM(f.freight_amount)                                          AS freight_amt,
    SUM(f.cost_amount)                                             AS cost_amt,
    SUM(f.gross_profit)                                            AS gross_profit,
    ROUND(SUM(f.net_amount + f.freight_amount) / COUNT(DISTINCT f.order_id), 2) AS aov,
    fn_profit_rate(SUM(f.net_amount + f.freight_amount), SUM(f.gross_profit))    AS profit_rate,
    IFNULL(MAX(rf.refund_amt), 0)                                  AS refund_amt
FROM fact_sales f
JOIN dim_date dd ON dd.date_key = f.date_key
LEFT JOIN (
    SELECT DATE_FORMAT(refund_time, '%Y-%m') AS ym, SUM(refund_amount) AS refund_amt
    FROM bloom_oltp.refunds
    GROUP BY DATE_FORMAT(refund_time, '%Y-%m')
) rf ON rf.ym = dd.month_key
WHERE f.order_status <> '已取消'
GROUP BY dd.month_key;

-- ---------------------------------------------------------------------------
-- 5. ADS：渠道月度经营（扣掉平台佣金后才是真实毛利）
-- ---------------------------------------------------------------------------
TRUNCATE TABLE ads_channel_monthly;

INSERT INTO ads_channel_monthly
    (month_key, channel_id, channel_name, order_cnt, customer_cnt,
     gmv, aov, commission_amt, gross_profit, profit_rate)
SELECT
    dd.month_key,
    dch.channel_id,
    dch.channel_name,
    COUNT(DISTINCT f.order_id)                          AS order_cnt,
    COUNT(DISTINCT f.customer_sk)                       AS customer_cnt,
    SUM(f.net_amount + f.freight_amount)                AS gmv,
    ROUND(SUM(f.net_amount + f.freight_amount) / COUNT(DISTINCT f.order_id), 2) AS aov,
    ROUND(SUM(f.net_amount + f.freight_amount) * dch.commission_rate, 2)        AS commission_amt,
    ROUND(SUM(f.gross_profit) - SUM(f.net_amount + f.freight_amount) * dch.commission_rate, 2) AS gross_profit,
    fn_profit_rate(
        SUM(f.net_amount + f.freight_amount),
        SUM(f.gross_profit) - SUM(f.net_amount + f.freight_amount) * dch.commission_rate
    ) AS profit_rate
FROM fact_sales f
JOIN dim_date    dd  ON dd.date_key    = f.date_key
JOIN dim_channel dch ON dch.channel_sk = f.channel_sk
WHERE f.order_status <> '已取消'
GROUP BY dd.month_key, dch.channel_id, dch.channel_name, dch.commission_rate;

-- ---------------------------------------------------------------------------
-- 6. 更新统计信息（让优化器拿到真实的基数，执行计划才准）
-- ---------------------------------------------------------------------------
ANALYZE TABLE fact_sales, dws_sales_daily, dim_customer, dim_product, dim_date;
