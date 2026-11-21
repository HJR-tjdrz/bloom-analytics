# Power BI 建模说明

> 目标：用导出的 CSV 建一个星型模型，做出 3 页报表（经营总览 / 用户价值 / 商品渠道）。

---

## 一、导入数据

「获取数据 → 文本/CSV」，依次导入 `data/` 下 5 个文件（其余按需再加）：

| 表名（Power BI 里改） | 来源文件 |
| --- | --- |
| `FactSales` | `fact_sales_sample.csv` |
| `DimDate` | `dim_date.csv` |
| `KpiMonthly` | `ads_kpi_monthly.csv` |
| `ProductPerf` | `ads_product_performance.csv` |
| `ChannelPerf` | `ads_channel_efficiency.csv` |

导入时点「转换数据」进 Power Query，做三件事：

1. 把 `full_date`、`month_key` 改成日期/文本类型（Power BI 有时会猜错）；
2. 删除不需要的列（减少模型体积）；
3. 把表重命名为上面的英文名（DAX 里引用更稳）。

---

## 二、星型模型与关系

```
                ┌──────────────┐
                │   DimDate    │
                │ date_key (PK)│
                └──────┬───────┘
                       │ 1:*  date_key
┌──────────────┐  ┌────▼──────────┐  ┌──────────────────┐
│ ProductPerf  │  │  FactSales    │  │   ChannelPerf    │
│ product_name │◄─┤ product_name  ├─►│ channel_name     │
└──────────────┘  │ ...            │  └──────────────────┘
                  └───────────────┘
        KpiMonthly（独立汇总表，不与事实表建关系）
```

**关系规则**：

| 从 | 到 | 基数 | 方向 |
| --- | --- | --- | --- |
| `FactSales[date_key]` | `DimDate[date_key]` | 多对一 | 单向（DimDate → FactSales） |
| `FactSales[product_name]` | `ProductPerf[product_name]` | 多对一 | 单向 |
| `FactSales[channel_name]` | `ChannelPerf[channel_name]` | 多对一 | 单向 |

> 建议在 `DimDate` 上「标记为日期表」（右键 → 标记为日期表 → `full_date`），
> 这样时间智能函数（`TOTALYTD`、`SAMEPERIODLASTYEAR`）才能正常工作。

---

## 三、20 个 DAX 度量值

完整清单见 `03_PowerBI度量值.dax`，核心几个：

```dax
GMV = SUM ( FactSales[net_amount] ) + SUM ( FactSales[freight_amount] )

订单数 = DISTINCTCOUNT ( FactSales[order_id] )

客单价 = DIVIDE ( [GMV], [订单数] )

毛利率 = DIVIDE ( SUM ( FactSales[gross_profit] ), [GMV] )

GMV 同比 =
VAR Cur = [GMV]
VAR Prev = CALCULATE ( [GMV], SAMEPERIODLASTYEAR ( DimDate[full_date] ) )
RETURN DIVIDE ( Cur - Prev, Prev )

渠道佣金 = SUMX ( ChannelPerf, ChannelPerf[gmv] * ChannelPerf[commission_rate] )

佣金后净利率 = DIVIDE ( SUM ( ChannelPerf[net_profit] ), SUM ( ChannelPerf[gmv] ) )
```

---

## 四、三页报表布局

### 第 1 页：经营总览

- 顶部 6 张卡片：GMV、订单数、客单价、毛利率、成交客户、退款率
- 中部：折线柱状组合图（X = `DimDate[year_month]`，列 = GMV，线 = GMV 同比）
- 下部左：渠道环形图（`ChannelPerf`）；下部右：门店条形图
- 切片器：日期范围（`DimDate[full_date]`）、渠道（`ChannelPerf[channel_name]`）

### 第 2 页：用户价值

- 簇状条形图：八分群的客户数与消费额（双轴）
- 卡片：核心客户占比、核心客户 GMV 占比
- 表格：分群、客户数、平均 R/F/M

### 第 3 页：商品与渠道

- 散点图：X = `sales_amt`，Y = `growth_rate`，大小 = `sales_amt`，图例 = `bcg_class`
- 帕累托：柱 = 销售额，线 = 累计占比（DAX 用 `RANKX` + `SUMX` 实现）
- 渠道表：`channel_name`、GMV、佣金、净利率（条件格式：净利率 < 55% 标红）

---

## 五、帕累托的 DAX 写法（难点）

```dax
累计销售额 =
VAR CurAmount = [GMV]
RETURN
    SUMX (
        FILTER (
            ALLSELECTED ( ProductPerf[product_name] ),
            [GMV] >= CurAmount
        ),
        [GMV]
    )

累计占比 =
DIVIDE ( [累计销售额], CALCULATE ( [GMV], ALLSELECTED ( ProductPerf ) ) )
```

> `ALLSELECTED` 保证切片器过滤后仍按当前可见商品计算累计占比。

---

## 六、发布与交付

1. 「文件 → 发布 → 我的工作区」（需要 Power BI 账号）；
2. 或「文件 → 导出 → Power BI 模板（.pbit）」发给同事，他们填数据源路径即可复用；
3. 报表页可以「导出为 PDF/PPT」，用于周报。

---

## 七、与自建看板的一致性校验

三个数字必须对得上（否则说明口径不同）：

| 指标 | Web 看板 | Power BI 应得 |
| --- | ---: | ---: |
| GMV（全量） | 24,541,075 | 24,541,075 |
| 订单数 | 97,933 | 97,933 |
| 毛利率 | 61.30% | 61.30% |

> 对不上时先查：是否把「已取消」订单算进去了、是否重复计数了订单（`COUNT` vs `DISTINCTCOUNT`）。
