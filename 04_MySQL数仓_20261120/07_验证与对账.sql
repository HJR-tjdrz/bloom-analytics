-- ============================================================================
--  07 · 数据验证与对账（可重复执行，只读不写）
--
--  为什么必须做对账？
--    数仓最容易出的问题不是"跑不出来"，而是"跑出来了但数字不对"：
--    清洗丢了一批行、JOIN 放大了金额、维度键对不上导致静默丢数。
--    对账是数据工程师的基本功，也是这类项目最能体现工程素养的地方。
--
--  执行：python run.py check
--  说明：每个查询前的 `-- @check: 标题` 会被程序解析成报告小标题。
-- ============================================================================

SET NAMES utf8mb4;

-- @check: 一、各表层行数总览
SELECT '业务库-订单'   AS 对象, COUNT(*) AS 行数 FROM bloom_oltp.orders
UNION ALL SELECT '业务库-订单明细', COUNT(*) FROM bloom_oltp.order_items
UNION ALL SELECT '业务库-客户',     COUNT(*) FROM bloom_oltp.customers
UNION ALL SELECT '业务库-商品SKU',  COUNT(*) FROM bloom_oltp.flower_skus
UNION ALL SELECT '业务库-花束',     COUNT(*) FROM bloom_oltp.bundles
UNION ALL SELECT '业务库-退款',     COUNT(*) FROM bloom_oltp.refunds
UNION ALL SELECT '业务库-库存',     COUNT(*) FROM bloom_oltp.inventory_daily
UNION ALL SELECT 'ODS-订单原始',    COUNT(*) FROM bloom_dw.ods_orders_raw
UNION ALL SELECT '维度-日期',       COUNT(*) FROM bloom_dw.dim_date
UNION ALL SELECT '维度-客户',       COUNT(*) FROM bloom_dw.dim_customer
UNION ALL SELECT '维度-商品',       COUNT(*) FROM bloom_dw.dim_product
UNION ALL SELECT '事实-销售明细',   COUNT(*) FROM bloom_dw.fact_sales
UNION ALL SELECT '汇总-日门店渠道', COUNT(*) FROM bloom_dw.dws_sales_daily
UNION ALL SELECT '应用-月度KPI',    COUNT(*) FROM bloom_dw.ads_kpi_monthly;

-- @check: 二、金额对账（业务库实付 vs 事实表实付+运费）
SELECT
    ROUND((SELECT SUM(payable_amount) FROM bloom_oltp.orders), 2)                     AS 业务库实付合计,
    ROUND((SELECT SUM(net_amount + freight_amount) FROM bloom_dw.fact_sales), 2)      AS 事实表实付合计,
    ROUND((SELECT SUM(payable_amount) FROM bloom_oltp.orders)
        - (SELECT SUM(net_amount + freight_amount) FROM bloom_dw.fact_sales), 2)      AS 差额;

-- @check: 三、订单数对账（去重订单数必须一致）
SELECT
    (SELECT COUNT(*) FROM bloom_oltp.orders)                       AS 业务库订单数,
    (SELECT COUNT(DISTINCT order_id) FROM bloom_dw.fact_sales)     AS 事实表订单数,
    (SELECT COUNT(*) FROM bloom_oltp.order_items)                  AS 业务库明细数,
    (SELECT COUNT(*) FROM bloom_dw.fact_sales)                     AS 事实表明细数;

-- @check: 四、维度完整性（事实表挂不上维度的孤儿行应为 0）
SELECT
    SUM(CASE WHEN dd.date_key   IS NULL THEN 1 ELSE 0 END) AS 日期维缺失,
    SUM(CASE WHEN dc.customer_sk IS NULL THEN 1 ELSE 0 END) AS 客户维缺失,
    SUM(CASE WHEN dp.product_sk IS NULL THEN 1 ELSE 0 END) AS 商品维缺失,
    SUM(CASE WHEN ds.store_sk   IS NULL THEN 1 ELSE 0 END) AS 门店维缺失,
    SUM(CASE WHEN dch.channel_sk IS NULL THEN 1 ELSE 0 END) AS 渠道维缺失
FROM bloom_dw.fact_sales f
LEFT JOIN bloom_dw.dim_date     dd  ON dd.date_key     = f.date_key
LEFT JOIN bloom_dw.dim_customer dc  ON dc.customer_sk  = f.customer_sk
LEFT JOIN bloom_dw.dim_product  dp  ON dp.product_sk   = f.product_sk
LEFT JOIN bloom_dw.dim_store    ds  ON ds.store_sk     = f.store_sk
LEFT JOIN bloom_dw.dim_channel  dch ON dch.channel_sk  = f.channel_sk;

-- @check: 五、ODS 脏数据体检（清洗前的数据质量现状）
SELECT
    COUNT(*)                                                                    AS 原始行数,
    COUNT(*) - COUNT(DISTINCT order_id)                                         AS 重复订单行,
    SUM(CASE WHEN customer_id IS NULL OR customer_id = '' THEN 1 ELSE 0 END)     AS 客户ID缺失,
    SUM(CASE WHEN status NOT IN ('已付款','制作中','待配送','已完成','已退款','已取消') THEN 1 ELSE 0 END) AS 状态值异常,
    SUM(CASE WHEN CAST(payable_amount AS DECIMAL(12,2)) <= 0 THEN 1 ELSE 0 END)  AS 金额非正,
    SUM(CASE WHEN order_time NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}' THEN 1 ELSE 0 END) AS 时间格式异常,
    SUM(CASE WHEN order_time < '2020-01-01' OR order_time > '2027-01-01' THEN 1 ELSE 0 END) AS 时间越界
FROM bloom_dw.ods_orders_raw;

-- @check: 六、客户维度 SCD2 版本检查（每个客户有且仅有一个当前版本）
SELECT
    COUNT(DISTINCT customer_id)                       AS 客户数,
    COUNT(*)                                          AS 版本总行数,
    SUM(CASE WHEN is_current = 1 THEN 1 ELSE 0 END)   AS 当前版本数,
    SUM(CASE WHEN version > 1 THEN 1 ELSE 0 END)      AS 历史版本数,
    MAX(version)                                      AS 最大版本号
FROM bloom_dw.dim_customer;

-- @check: 七、经营 KPI 月度汇总（最近 12 个月，单位：元）
SELECT
    month_key                    AS 年月,
    order_cnt                     AS 订单数,
    customer_cnt                  AS 客户数,
    ROUND(gmv, 0)                 AS GMV,
    aov                           AS 客单价,
    ROUND(profit_rate * 100, 2)   AS 毛利率百分比
FROM bloom_dw.ads_kpi_monthly
ORDER BY month_key DESC
LIMIT 12;

-- @check: 八、数据新鲜度（业务库与事实表的最大下单时间应一致）
SELECT
    (SELECT MAX(order_time) FROM bloom_oltp.orders) AS 业务库最新订单时间,
    (SELECT MAX(order_time) FROM bloom_dw.fact_sales) AS 事实表最新订单时间,
    (SELECT MAX(r.refund_time) FROM bloom_oltp.refunds r) AS 最新退款时间;
