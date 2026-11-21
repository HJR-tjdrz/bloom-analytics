-- ============================================================================
--  02 · OLTP 业务库建表（bloom_oltp）
--  定位：模拟花店真实在用的交易系统数据库（第三范式，读写分离式设计）
--  说明：
--    * 全部 InnoDB + utf8mb4，金额统一 DECIMAL(12,2)，杜绝浮点误差
--    * 主键尽量用业务编号（订单号/商品编码），便于与上游系统对齐
--    * 外键保留关键约束，其余靠应用层与 ETL 校验（真实系统也常这么做）
--  执行账号：bloom_app
-- ============================================================================

SET NAMES utf8mb4;
USE bloom_oltp;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS order_status_log;
DROP TABLE IF EXISTS inventory_daily;
DROP TABLE IF EXISTS refunds;
DROP TABLE IF EXISTS order_promotions;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS coupons;
DROP TABLE IF EXISTS promotions;
DROP TABLE IF EXISTS bundle_items;
DROP TABLE IF EXISTS bundles;
DROP TABLE IF EXISTS flower_skus;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS stores;
DROP TABLE IF EXISTS channels;
DROP TABLE IF EXISTS suppliers;

-- ---------------------------------------------------------------------------
-- 1. 供应商
-- ---------------------------------------------------------------------------
CREATE TABLE suppliers (
    supplier_id        VARCHAR(10)  NOT NULL COMMENT '供应商编号',
    supplier_name      VARCHAR(64)  NOT NULL COMMENT '供应商名称',
    province           VARCHAR(32)      NULL COMMENT '所在省份',
    lead_time_days     INT              NULL COMMENT '到货提前期(天)',
    rating             DECIMAL(2,1)     NULL COMMENT '供应商评分(5分制)',
    cooperation_start  DATE             NULL COMMENT '合作起始日期',
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (supplier_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商维表';

-- ---------------------------------------------------------------------------
-- 2. 销售渠道
-- ---------------------------------------------------------------------------
CREATE TABLE channels (
    channel_id      VARCHAR(10)   NOT NULL COMMENT '渠道编号',
    channel_name    VARCHAR(32)   NOT NULL COMMENT '渠道名称',
    channel_type    VARCHAR(8)    NOT NULL COMMENT '渠道类型：线上/线下',
    commission_rate DECIMAL(5,4)  NOT NULL DEFAULT 0 COMMENT '平台佣金率',
    is_active       TINYINT       NOT NULL DEFAULT 1 COMMENT '是否启用',
    PRIMARY KEY (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售渠道维表';

-- ---------------------------------------------------------------------------
-- 3. 门店
-- ---------------------------------------------------------------------------
CREATE TABLE stores (
    store_id    VARCHAR(10)  NOT NULL COMMENT '门店编号',
    store_name  VARCHAR(64)  NOT NULL COMMENT '门店名称',
    city        VARCHAR(32)  NOT NULL COMMENT '城市',
    province    VARCHAR(32)  NOT NULL COMMENT '省份',
    open_date   DATE             NULL COMMENT '开业日期',
    area_sqm    INT              NULL COMMENT '营业面积(㎡)',
    staff_count INT              NULL COMMENT '员工人数',
    status      VARCHAR(16)  NOT NULL DEFAULT '营业中' COMMENT '门店状态',
    PRIMARY KEY (store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='门店维表';

-- ---------------------------------------------------------------------------
-- 4. 员工
-- ---------------------------------------------------------------------------
CREATE TABLE employees (
    employee_id   VARCHAR(12) NOT NULL COMMENT '员工编号',
    employee_name VARCHAR(32) NOT NULL COMMENT '员工姓名',
    store_id      VARCHAR(10) NOT NULL COMMENT '所属门店',
    role_name     VARCHAR(32)     NULL COMMENT '岗位：店长/花艺师/客服/配送员',
    hire_date     DATE            NULL COMMENT '入职日期',
    is_active     TINYINT     NOT NULL DEFAULT 1 COMMENT '在职标记',
    PRIMARY KEY (employee_id),
    KEY idx_emp_store (store_id),
    CONSTRAINT fk_emp_store FOREIGN KEY (store_id) REFERENCES stores (store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='员工维表';

-- ---------------------------------------------------------------------------
-- 5. 花材 / 商品 SKU
-- ---------------------------------------------------------------------------
CREATE TABLE flower_skus (
    sku_id          VARCHAR(10)   NOT NULL COMMENT '商品编码',
    sku_name        VARCHAR(64)   NOT NULL COMMENT '商品名称',
    category        VARCHAR(16)   NOT NULL COMMENT '品类：玫瑰/百合/郁金香/配叶/花器/礼盒…',
    color           VARCHAR(16)       NULL COMMENT '颜色',
    unit            VARCHAR(8)    NOT NULL COMMENT '销售单位：支/扎/个/盒/张',
    spec            VARCHAR(32)       NULL COMMENT '规格',
    cost_price      DECIMAL(10,2) NOT NULL COMMENT '成本价',
    list_price      DECIMAL(10,2) NOT NULL COMMENT '门市价',
    shelf_life_days INT           NOT NULL DEFAULT 7 COMMENT '货架期(天)，>900 视为非生鲜',
    origin_place    VARCHAR(32)       NULL COMMENT '产地',
    supplier_id     VARCHAR(10)       NULL COMMENT '主供应商',
    status          VARCHAR(16)   NOT NULL DEFAULT '在售' COMMENT '商品状态',
    PRIMARY KEY (sku_id),
    KEY idx_sku_category (category),
    KEY idx_sku_supplier (supplier_id),
    CONSTRAINT fk_sku_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='花材与商品SKU表';

-- ---------------------------------------------------------------------------
-- 6. 花束（成品）
-- ---------------------------------------------------------------------------
CREATE TABLE bundles (
    bundle_id   VARCHAR(10)   NOT NULL COMMENT '花束编码',
    bundle_name VARCHAR(64)   NOT NULL COMMENT '花束名称',
    category    VARCHAR(16)   NOT NULL COMMENT '花束品类：爱情告白/感恩祝福/开业庆典…',
    scene       VARCHAR(16)       NULL COMMENT '适用场景：求婚/表白/开业/清明…',
    description VARCHAR(255)      NULL COMMENT '商品描述',
    list_price  DECIMAL(10,2) NOT NULL COMMENT '销售价',
    cost_price  DECIMAL(10,2) NOT NULL COMMENT 'BOM口径成本(含包装与人工)',
    launch_date DATE              NULL COMMENT '上市日期',
    status      VARCHAR(16)   NOT NULL DEFAULT '在售' COMMENT '商品状态',
    PRIMARY KEY (bundle_id),
    KEY idx_bundle_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='花束成品表';

-- ---------------------------------------------------------------------------
-- 7. 花束 BOM（物料清单）—— 成本与库存还原的关键
-- ---------------------------------------------------------------------------
CREATE TABLE bundle_items (
    bundle_id VARCHAR(10) NOT NULL COMMENT '花束编码',
    sku_id    VARCHAR(10) NOT NULL COMMENT '花材编码',
    quantity  INT         NOT NULL DEFAULT 1 COMMENT '用量(支/扎)',
    PRIMARY KEY (bundle_id, sku_id),
    KEY idx_bom_sku (sku_id),
    CONSTRAINT fk_bom_bundle FOREIGN KEY (bundle_id) REFERENCES bundles (bundle_id),
    CONSTRAINT fk_bom_sku FOREIGN KEY (sku_id) REFERENCES flower_skus (sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='花束BOM物料清单';

-- ---------------------------------------------------------------------------
-- 8. 客户
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    customer_id    VARCHAR(12) NOT NULL COMMENT '客户编号',
    customer_name  VARCHAR(64) NOT NULL COMMENT '客户名称（个人姓名/企业名）',
    customer_type  VARCHAR(8)  NOT NULL DEFAULT '个人' COMMENT '客户类型：个人/企业',
    gender         VARCHAR(4)      NULL COMMENT '性别',
    phone          VARCHAR(20)     NULL COMMENT '手机号',
    city           VARCHAR(32)     NULL COMMENT '城市',
    province       VARCHAR(32)     NULL COMMENT '省份',
    birthday       DATE            NULL COMMENT '生日',
    register_date  DATE            NULL COMMENT '注册(首单)日期',
    member_level   VARCHAR(16)     NULL COMMENT '会员等级',
    source_channel VARCHAR(32)     NULL COMMENT '注册来源渠道',
    is_active      TINYINT     NOT NULL DEFAULT 1 COMMENT '是否活跃',
    PRIMARY KEY (customer_id),
    KEY idx_cust_city (city),
    KEY idx_cust_register (register_date),
    KEY idx_cust_phone (phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户维表';

-- ---------------------------------------------------------------------------
-- 9. 促销活动
-- ---------------------------------------------------------------------------
CREATE TABLE promotions (
    promotion_id     VARCHAR(12)   NOT NULL COMMENT '活动编号',
    promotion_name   VARCHAR(64)   NOT NULL COMMENT '活动名称',
    promotion_type   VARCHAR(16)   NOT NULL COMMENT '活动类型：折扣/满减/买赠/秒杀',
    festival         VARCHAR(16)       NULL COMMENT '关联节日',
    start_date       DATE          NOT NULL COMMENT '开始日期',
    end_date         DATE          NOT NULL COMMENT '结束日期',
    discount_rate    DECIMAL(5,4)  NOT NULL DEFAULT 1 COMMENT '折扣率(1=无折扣)',
    threshold_amount DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '满减门槛',
    reduce_amount    DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '满减金额',
    budget_amount    DECIMAL(12,2)     NULL COMMENT '活动预算',
    status           VARCHAR(16)   NOT NULL DEFAULT '已结束' COMMENT '活动状态',
    PRIMARY KEY (promotion_id),
    KEY idx_promo_date (start_date, end_date),
    KEY idx_promo_festival (festival)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='促销活动表';

-- ---------------------------------------------------------------------------
-- 10. 优惠券
-- ---------------------------------------------------------------------------
CREATE TABLE coupons (
    coupon_id        VARCHAR(10)   NOT NULL COMMENT '优惠券编号',
    coupon_name      VARCHAR(64)   NOT NULL COMMENT '优惠券名称',
    coupon_type      VARCHAR(16)   NOT NULL COMMENT '券类型：满减券/折扣券/无门槛券',
    face_value       DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '面值(元)',
    threshold_amount DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '使用门槛',
    valid_from       DATE          NOT NULL COMMENT '生效日期',
    valid_to         DATE          NOT NULL COMMENT '失效日期',
    status           VARCHAR(16)   NOT NULL DEFAULT '生效中' COMMENT '券状态',
    PRIMARY KEY (coupon_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='优惠券表';

-- ---------------------------------------------------------------------------
-- 11. 订单主表（OLTP 核心，写入最频繁）
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    order_id               VARCHAR(24)   NOT NULL COMMENT '订单号 SO+日期+流水',
    customer_id            VARCHAR(12)       NULL COMMENT '客户编号',
    store_id               VARCHAR(10)   NOT NULL COMMENT '履约门店',
    channel_id             VARCHAR(10)   NOT NULL COMMENT '销售渠道',
    order_time             DATETIME      NOT NULL COMMENT '下单时间',
    pay_time               DATETIME          NULL COMMENT '支付时间',
    status                 VARCHAR(16)   NOT NULL COMMENT '订单状态：已付款/制作中/待配送/已完成/已退款/已取消',
    delivery_type          VARCHAR(16)   NOT NULL COMMENT '配送方式：自提/同城配送/快递',
    expected_delivery_time DATETIME          NULL COMMENT '承诺送达时间',
    delivery_time          DATETIME          NULL COMMENT '实际送达时间',
    total_amount           DECIMAL(12,2) NOT NULL COMMENT '商品原价合计',
    discount_amount        DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '优惠合计(促销+券+会员)',
    freight_amount         DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '运费',
    payable_amount         DECIMAL(12,2) NOT NULL COMMENT '实付金额',
    cost_amount            DECIMAL(12,2) NOT NULL COMMENT '履约成本(商品+配送+包装)',
    promotion_id           VARCHAR(12)       NULL COMMENT '命中的促销活动',
    coupon_id              VARCHAR(10)       NULL COMMENT '使用的优惠券',
    is_first_order         TINYINT       NOT NULL DEFAULT 0 COMMENT '是否首单',
    remark                 VARCHAR(255)      NULL COMMENT '订单备注',
    PRIMARY KEY (order_id),
    KEY idx_order_time (order_time),
    KEY idx_order_customer (customer_id),
    KEY idx_order_store_channel (store_id, channel_id),
    KEY idx_order_status (status),
    CONSTRAINT fk_order_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT fk_order_store FOREIGN KEY (store_id) REFERENCES stores (store_id),
    CONSTRAINT fk_order_channel FOREIGN KEY (channel_id) REFERENCES channels (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单主表';

-- ---------------------------------------------------------------------------
-- 12. 订单明细
-- ---------------------------------------------------------------------------
CREATE TABLE order_items (
    item_id         VARCHAR(32)   NOT NULL COMMENT '明细编号 订单号-行号',
    order_id        VARCHAR(24)   NOT NULL COMMENT '订单号',
    product_type    VARCHAR(8)    NOT NULL COMMENT '商品类型：SKU/BUNDLE',
    sku_id          VARCHAR(10)       NULL COMMENT '花材编码（散卖时）',
    bundle_id       VARCHAR(10)       NULL COMMENT '花束编码（成品时）',
    quantity        INT           NOT NULL COMMENT '数量',
    unit_price      DECIMAL(10,2) NOT NULL COMMENT '成交单价',
    discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '分摊优惠',
    freight_amount  DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '分摊运费',
    line_amount     DECIMAL(12,2) NOT NULL COMMENT '行金额=数量×单价',
    cost_amount     DECIMAL(12,2) NOT NULL COMMENT '行成本',
    PRIMARY KEY (item_id),
    KEY idx_item_order (order_id),
    KEY idx_item_sku (sku_id),
    KEY idx_item_bundle (bundle_id),
    CONSTRAINT fk_item_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细表';

-- ---------------------------------------------------------------------------
-- 13. 订单-促销关联（一单可命中多个活动）
-- ---------------------------------------------------------------------------
CREATE TABLE order_promotions (
    order_id        VARCHAR(24)   NOT NULL COMMENT '订单号',
    promotion_id    VARCHAR(12)   NOT NULL COMMENT '活动编号',
    discount_amount DECIMAL(10,2) NOT NULL COMMENT '该活动贡献的优惠金额',
    PRIMARY KEY (order_id, promotion_id),
    KEY idx_op_promotion (promotion_id),
    CONSTRAINT fk_op_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单促销关联表';

-- ---------------------------------------------------------------------------
-- 14. 退款单
-- ---------------------------------------------------------------------------
CREATE TABLE refunds (
    refund_id     VARCHAR(12)   NOT NULL COMMENT '退款编号',
    order_id      VARCHAR(24)   NOT NULL COMMENT '订单号',
    refund_time   DATETIME      NOT NULL COMMENT '退款时间',
    refund_amount DECIMAL(12,2) NOT NULL COMMENT '退款金额',
    refund_reason VARCHAR(64)       NULL COMMENT '退款原因',
    refund_type   VARCHAR(16)       NULL COMMENT '退款类型：全额/部分',
    status        VARCHAR(16)   NOT NULL DEFAULT '已退款' COMMENT '退款状态',
    PRIMARY KEY (refund_id),
    KEY idx_refund_order (order_id),
    CONSTRAINT fk_refund_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退款单表';

-- ---------------------------------------------------------------------------
-- 15. 每日库存（按 SKU）
-- ---------------------------------------------------------------------------
CREATE TABLE inventory_daily (
    stat_date       DATE          NOT NULL COMMENT '统计日期',
    sku_id          VARCHAR(10)   NOT NULL COMMENT '花材编码',
    in_qty          INT           NOT NULL DEFAULT 0 COMMENT '入库数量',
    out_qty         INT           NOT NULL DEFAULT 0 COMMENT '出库(销售消耗)数量',
    waste_qty       INT           NOT NULL DEFAULT 0 COMMENT '损耗数量',
    on_hand_qty     INT           NOT NULL DEFAULT 0 COMMENT '期末库存',
    purchase_amount DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '采购金额',
    waste_amount    DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '损耗金额',
    PRIMARY KEY (stat_date, sku_id),
    KEY idx_inv_sku (sku_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日库存表';

-- ---------------------------------------------------------------------------
-- 16. 订单状态流转日志（履约时效分析的数据源）
-- ---------------------------------------------------------------------------
CREATE TABLE order_status_log (
    log_id      VARCHAR(16) NOT NULL COMMENT '日志编号',
    order_id    VARCHAR(24) NOT NULL COMMENT '订单号',
    from_status VARCHAR(16)     NULL COMMENT '变更前状态',
    to_status   VARCHAR(16) NOT NULL COMMENT '变更后状态',
    changed_at  DATETIME    NOT NULL COMMENT '变更时间',
    operator_id VARCHAR(12)     NULL COMMENT '操作人',
    PRIMARY KEY (log_id),
    KEY idx_sl_order (order_id),
    KEY idx_sl_time (changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单状态流转日志';

SET FOREIGN_KEY_CHECKS = 1;
