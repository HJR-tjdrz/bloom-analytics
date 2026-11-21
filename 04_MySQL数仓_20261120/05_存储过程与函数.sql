-- ============================================================================
--  05 · 存储过程与自定义函数
--
--  使用场景说明（不是为写而写）：
--    · 价格带、毛利率这种"口径函数"，写成 FUNCTION 后所有报表口径一致，
--      改一处即可全站生效，避免每个看板自己写 CASE WHEN 写歪；
--    · 月度 KPI 刷新做成 PROCEDURE，配合调度（Windows 计划任务 / Airflow）
--      就能每天自动跑批，前端只读结果表。
--
--  执行方式：必须用 mysql 客户端执行（内含 DELIMITER），
--           由 00_一键初始化数据库.ps1 自动调用。
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_dw;

DROP PROCEDURE IF EXISTS sp_refresh_month_kpi;
DROP FUNCTION  IF EXISTS fn_price_band;
DROP FUNCTION  IF EXISTS fn_profit_rate;

DELIMITER $$

-- ---------------------------------------------------------------------------
-- 函数 1：价格带划分（商品分析、波士顿矩阵都要用）
-- ---------------------------------------------------------------------------
CREATE FUNCTION fn_price_band(p_price DECIMAL(10,2))
RETURNS VARCHAR(16)
DETERMINISTIC
COMMENT '按门市价划分价格带'
BEGIN
    DECLARE v_band VARCHAR(16);
    IF p_price IS NULL THEN
        SET v_band = '未知';
    ELSEIF p_price < 50 THEN
        SET v_band = '50元以下';
    ELSEIF p_price < 200 THEN
        SET v_band = '50-199元';
    ELSEIF p_price < 500 THEN
        SET v_band = '200-499元';
    ELSE
        SET v_band = '500元以上';
    END IF;
    RETURN v_band;
END$$

-- ---------------------------------------------------------------------------
-- 函数 2：毛利率（分母为 0 时返回 0，避免报表出现 NULL/除零错误）
-- ---------------------------------------------------------------------------
CREATE FUNCTION fn_profit_rate(p_revenue DECIMAL(14,2), p_profit DECIMAL(14,2))
RETURNS DECIMAL(8,4)
DETERMINISTIC
COMMENT '毛利率=毛利/收入，收入为0时返回0'
BEGIN
    IF p_revenue IS NULL OR p_revenue = 0 THEN
        RETURN 0;
    END IF;
    RETURN ROUND(p_profit / p_revenue, 4);
END$$

-- ---------------------------------------------------------------------------
-- 过程 1：刷新某个月的经营 KPI（可重复执行，先删后插保证幂等）
-- 用法：CALL sp_refresh_month_kpi('2026-05');
-- ---------------------------------------------------------------------------
CREATE PROCEDURE sp_refresh_month_kpi(IN p_month VARCHAR(8))
COMMENT '刷新指定年月的 ads_kpi_monthly'
BEGIN
    DECLARE v_rows INT DEFAULT 0;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SELECT CONCAT('刷新失败，已回滚：', p_month) AS result;
    END;

    START TRANSACTION;

    DELETE FROM ads_kpi_monthly WHERE month_key = p_month;

    INSERT INTO ads_kpi_monthly
        (month_key, order_cnt, item_qty, customer_cnt, new_customer_cnt,
         gmv, item_revenue, discount_amt, freight_amt, cost_amt, gross_profit,
         aov, profit_rate, refund_amt)
    SELECT
        dd.month_key,
        COUNT(DISTINCT f.order_id)                                   AS order_cnt,
        SUM(f.quantity)                                              AS item_qty,
        COUNT(DISTINCT f.customer_sk)                                AS customer_cnt,
        COUNT(DISTINCT CASE WHEN f.is_first_order = 1 THEN f.customer_sk END) AS new_customer_cnt,
        SUM(f.net_amount + f.freight_amount)                         AS gmv,
        SUM(f.net_amount)                                            AS item_revenue,
        SUM(f.discount_amount)                                       AS discount_amt,
        SUM(f.freight_amount)                                        AS freight_amt,
        SUM(f.cost_amount)                                           AS cost_amt,
        SUM(f.gross_profit)                                          AS gross_profit,
        ROUND(SUM(f.net_amount + f.freight_amount) / COUNT(DISTINCT f.order_id), 2) AS aov,
        fn_profit_rate(SUM(f.net_amount + f.freight_amount), SUM(f.gross_profit))     AS profit_rate,
        IFNULL(MAX(rf.refund_amt), 0)                                AS refund_amt
    FROM fact_sales f
    JOIN dim_date dd ON dd.date_key = f.date_key
    LEFT JOIN (
        SELECT DATE_FORMAT(refund_time, '%Y-%m') AS ym, SUM(refund_amount) AS refund_amt
        FROM bloom_oltp.refunds
        GROUP BY DATE_FORMAT(refund_time, '%Y-%m')
    ) rf ON rf.ym = dd.month_key
    WHERE f.order_status <> '已取消'
      AND dd.month_key = p_month
    GROUP BY dd.month_key;

    SET v_rows = ROW_COUNT();
    COMMIT;

    SELECT CONCAT('刷新完成：', p_month, '，写入 ', v_rows, ' 行') AS result;
END$$

DELIMITER ;

-- 自检：刷新最近一个月，验证过程可正常调用
-- CALL sp_refresh_month_kpi('2026-10');
