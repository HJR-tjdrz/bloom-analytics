# API 接口文档

> Base URL：`http://127.0.0.1:5050`
> 返回格式：`application/json; charset=utf-8`
> 缓存：除 `/api/health` 外，所有接口进程内缓存 **5 分钟**（同一参数命中缓存）

---

## 通用说明

### 筛选参数（仅 `/api/overview` 支持）

| 参数 | 类型 | 示例 | 说明 |
| --- | --- | --- | --- |
| `start` | date | `2026-01-01` | 起始日期（含） |
| `end` | date | `2026-06-30` | 结束日期（含） |
| `channel` | string | `线下门店` | 渠道名称，可重复传多值 |
| `store` | string | `静安旗舰店` | 门店名称，可重复传多值 |

### 错误格式

```json
{ "error": "错误信息" }
```

HTTP 状态码 500 表示服务端异常（会同时写入 `logs/` 日志）。

---

## 1. `GET /api/health`

健康检查，用于确认服务是否启动。

```json
{ "ok": true, "ts": "2026-11-21 11:20:31" }
```

---

## 2. `GET /api/meta`

筛选器选项与数据区间。

```json
{
  "channels": ["线下门店", "微信小程序", "美团外卖", "抖音直播", "淘宝旗舰店", "企业团购"],
  "stores": ["静安旗舰店", "徐汇滨江店", "..."],
  "date_start": "2024-01-01",
  "date_end": "2026-11-18",
  "calc_date": "2026-11-19"
}
```

---

## 3. `GET /api/overview`

经营总览：KPI、月度趋势、节日效应、渠道结构、门店 GMV。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kpis.gmv` | number | 实付 GMV（含运费，剔除取消单） |
| `kpis.orders` | int | 去重订单数 |
| `kpis.customers` | int | 成交客户数 |
| `kpis.aov` | number | 客单价 |
| `kpis.profit` / `margin` | number | 毛利额 / 毛利率 |
| `kpis.qty` | int | 销售件数 |
| `kpis.new_customer_ratio` | number | 新客订单占比 |
| `kpis.refund_rate` | number | 退款订单占比 |
| `monthly.months` | string[] | 月份标签（1月…12月） |
| `monthly.series` | object | `{ "2025": [...], "2026": [...] }` |
| `monthly.yoy` | number[] | 2026 对 2025 的同比（小数） |
| `festival.names` / `daily` | string[] / number[] | 节日名称与期间日均 GMV |
| `channel.values` | object[] | `[{name, value, type}]` 供环图使用 |
| `store.names` / `gmv` / `orders` | array | 门店维度 |

示例（截取）：

```json
{
  "kpis": { "gmv": 24541075.12, "orders": 97933, "aov": 250.6, "margin": 0.6126 },
  "monthly": { "months": ["1月","2月"], "series": {"2025": [..], "2026": [..]}, "yoy": [0.08, 0.12] },
  "festival": { "names": ["情人节","520"], "daily": [91000, 72000] }
}
```

---

## 4. `GET /api/rfm`

RFM 八分群与客户散点。

| 字段 | 说明 |
| --- | --- |
| `segments` | 分群名称（按价值排序） |
| `customers` / `cust_share` | 客户数 / 人数占比 |
| `gmv` / `gmv_share` | 消费额 / 消费额占比 |
| `avg_r` / `avg_f` / `avg_m` | 分群平均 R / F / M |
| `scatter` | 抽样 2,500 点，元素为 `[recency, frequency, monetary, segment]` |

---

## 5. `GET /api/product`

商品结构：帕累托、ABC、波士顿矩阵、TOP20。

| 字段 | 说明 |
| --- | --- |
| `pareto.names/values/cum` | 商品名（按销售额降序）/ 销售额 / 累计占比 |
| `abc.classes/products/revenue` | ABC 三类的商品数与销售额 |
| `bcg` | `[{name, category, type, class, x, y, size}]`，x=销售额，y=同比增长 |
| `table` | TOP20 明细（rank/name/type/category/qty/amt/margin/growth/abc/bcg） |

---

## 6. `GET /api/promo`

促销：活动让利效率 + 折扣敏感度分桶。

| 字段 | 说明 |
| --- | --- |
| `roi` | `[{name,type,festival,orders,gmv,discount,profit,margin,roi}]`，按 GMV 降序 Top15 |
| `buckets.names` | 折扣区间：无折扣 / 0-5% / … / 20%+ |
| `buckets.order_share` | 各区间订单占比 |
| `buckets.margin` | 各区间毛利率 |

> `roi` 是**让利效率**（活动毛利 / 让利金额），不是因果意义上的"活动增量 ROI"，
> 口径边界见 `07_分析与建模_20261121/05_促销效果与价格弹性.md`。

---

## 7. `GET /api/channel`

渠道与门店效率。

| 字段 | 说明 |
| --- | --- |
| `table` | 渠道明细：订单、GMV、占比、客单价、佣金、佣金后净利率 |
| `scatter` | `[{name,aov,margin,gmv,type}]` 供气泡图 |
| `store` | 门店 GMV / 坪效 / 订单数 |

---

## 8. `GET /api/inventory`

库存与损耗（按品类）。

| 字段 | 说明 |
| --- | --- |
| `categories` | 品类列表 |
| `turnover` / `waste_rate` / `waste_amt` | 周转天数 / 损耗率 / 损耗金额 |
| `sku_cnt` | 品类 SKU 数 |
| `slow` | 滞销 TOP10（C 类、销量垫底） |

---

## 9. `GET /api/forecast`

销售预测：90 天历史 + 未来 30 天。

| 字段 | 说明 |
| --- | --- |
| `history.dates` / `values` | 近 90 天实际 GMV |
| `forecast.dates/values/lower/upper` | 预测值与 80% 区间 |
| `forecast.festivals` | 预测期内节日（非节日为空串） |
| `metrics.mape` / `rmse` / `sum` | 回测精度、30 天预测合计 |

---

## 前端调用示例

```js
const res = await fetch('/api/overview?start=2026-01-01&end=2026-06-30&channel=' + encodeURIComponent('线下门店'));
const data = await res.json();
console.log(data.kpis.gmv);
```

## 复用到其他 BI 工具

Flask 接口本质是 HTTP + JSON，Tableau / Power BI 都支持 **Web 数据连接器**，
可直接指向 `http://127.0.0.1:5050/api/overview`（JSON 需先转成表格结构）。
更推荐的做法是让这些工具直连 MySQL 的 `ads_*` 表（见第 5 轮 BI 配套）。
