/* ============================================================
   BloomAnalytics 看板前端逻辑
   纯原生 JS + ECharts（离线），零构建。结构：
     1) 工具函数与主题
     2) 通用图表配置
     3) 6 个页面的渲染函数
     4) 路由 / 筛选 / 初始化
   ============================================================ */

const C = {
  blue: '#4c6fff', green: '#2bc48a', orange: '#f5a524', red: '#f04e4e',
  purple: '#8b5cf6', cyan: '#22d3ee', gold: '#facc15', gray: '#8a94a6',
  text: '#e8eefb', muted: '#8b98b3', faint: '#5c6a85', line: '#24304a',
};
const PALETTE = [C.blue, C.green, C.orange, C.purple, C.cyan, C.gold, C.red, C.gray];

const state = { page: 'overview', charts: {}, rendered: new Set(), meta: null, filters: {} };

/* ---------------- 1. 工具 ---------------- */
const $ = (s) => document.querySelector(s);
const n0 = (v) => (v == null ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 }));
const n1 = (v) => (v == null ? '—' : Number(v).toFixed(1));
const n2 = (v) => (v == null ? '—' : Number(v).toFixed(2));
const pct = (v, d = 1) => (v == null ? '—' : (v * 100).toFixed(d) + '%');
const money = (v) => {
  if (v == null) return '—';
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(2) + ' 亿';
  if (a >= 1e4) return (v / 1e4).toFixed(1) + ' 万';
  return n0(v);
};
const wan = (v) => (v == null ? 0 : +(v / 1e4).toFixed(2));

async function api(path, params) {
  const qs = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => { if (v !== '' && v != null) qs.append(k, v); });
  const url = path + (qs.toString() ? '?' + qs.toString() : '');
  const res = await fetch(url);
  if (!res.ok) throw new Error('接口错误 ' + res.status);
  return res.json();
}

function loading(on) { $('#loading').classList.toggle('is-on', on); }

/* ---------------- 2. 图表通用配置 ---------------- */
const tooltipBase = {
  backgroundColor: 'rgba(15,22,38,.96)', borderColor: C.line, borderWidth: 1,
  textStyle: { color: C.text, fontSize: 12 }, extraCssText: 'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.4)',
};
const axisBase = {
  axisLine: { lineStyle: { color: C.line } },
  axisTick: { show: false },
  axisLabel: { color: C.muted, fontSize: 11 },
  splitLine: { lineStyle: { color: 'rgba(36,48,74,.55)', type: 'dashed' } },
};
const gridBase = { left: 10, right: 18, top: 42, bottom: 10, containLabel: true };
const legendBase = { textStyle: { color: C.muted, fontSize: 11 }, top: 4, right: 6, itemWidth: 10, itemHeight: 8 };
const toolboxBase = {
  right: 6, top: 0, iconStyle: { borderColor: C.faint },
  feature: { saveAsImage: { title: '保存图片', pixelRatio: 2, backgroundColor: '#0e1421' } },
};

function chartOf(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  let inst = state.charts[id];
  if (!inst) { inst = echarts.init(el, null, { renderer: 'canvas' }); state.charts[id] = inst; }
  return inst;
}

function resizePage(page) {
  const sec = document.getElementById('page-' + page);
  if (!sec) return;
  sec.querySelectorAll('.chart').forEach((el) => {
    const inst = echarts.getInstanceByDom(el);
    if (inst) inst.resize();
  });
}

/* ---------------- 3. 页面渲染 ---------------- */

/* ---- 3.1 经营总览 ---- */
function kpiCard(label, value, sub, delta) {
  return `<div class="kpi">
    <div class="k-label">${label}</div>
    <div class="k-value">${value}</div>
    ${sub ? `<div class="k-sub">${sub}</div>` : ''}
    ${delta ? `<div class="k-delta ${delta.cls}">${delta.text}</div>` : ''}
  </div>`;
}

async function renderOverview() {
  const f = state.filters;
  const d = await api('/api/overview', f);
  const k = d.kpis;

  $('#kpi-grid').innerHTML = [
    kpiCard('GMV（实付）', money(k.gmv), `商品实收 + 运费 · ${n0(k.orders)} 单`),
    kpiCard('订单数', n0(k.orders), `件数 ${n0(k.qty)} 件`),
    kpiCard('客单价', n2(k.aov) + ' 元', `件单价 ${n2(k.gmv / (k.qty || 1))} 元`),
    kpiCard('成交客户', n0(k.customers), `新客占比 ${pct(k.new_customer_ratio)}`),
    kpiCard('毛利额', money(k.profit), `毛利率 ${pct(k.margin)}`),
    kpiCard('退款率', pct(k.refund_rate), '按退款订单数 / 总订单'),
  ].join('');

  // 月度 GMV + 同比
  const years = Object.keys(d.monthly.series).sort();
  const barColors = ['#33507f', C.blue];
  const option = {
    tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' },
      valueFormatter: (v) => (v == null ? '—' : v.toFixed(1)),
    },
    legend: { ...legendBase, data: years.concat(['同比']) },
    toolbox: toolboxBase,
    grid: gridBase,
    xAxis: { type: 'category', data: d.monthly.months, ...axisBase, splitLine: { show: false } },
    yAxis: [
      { type: 'value', name: 'GMV / 万元', nameTextStyle: { color: C.faint, fontSize: 11 }, ...axisBase },
      { type: 'value', name: '同比', nameTextStyle: { color: C.faint, fontSize: 11 }, ...axisBase,
        axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: years.map((y, i) => ({
      name: y, type: 'bar', barMaxWidth: 18,
      data: d.monthly.series[y].map((v) => wan(v)),
      itemStyle: { color: barColors[i % barColors.length], borderRadius: [3, 3, 0, 0] },
    })).concat([{
      name: '同比', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 5,
      data: d.monthly.yoy.map((v) => (v == null ? null : +(v * 100).toFixed(1))),
      itemStyle: { color: C.orange }, lineStyle: { width: 2 },
      connectNulls: true,
    }]),
  };
  chartOf('chart-monthly').setOption(option, true);

  // 渠道环图
  chartOf('chart-channel').setOption({
    tooltip: { ...tooltipBase, trigger: 'item', formatter: (p) => `${p.name}<br/>GMV ${money(p.value)} 元（${p.percent}%）` },
    legend: { ...legendBase, orient: 'vertical', right: 4, top: 'middle', itemGap: 8 },
    color: PALETTE,
    series: [{
      type: 'pie', radius: ['46%', '68%'], center: ['38%', '52%'],
      avoidLabelOverlap: true, label: { show: false }, labelLine: { show: false },
      itemStyle: { borderColor: '#121a2b', borderWidth: 2 },
      data: d.channel.values,
    }],
  }, true);

  // 节日效应
  chartOf('chart-festival').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => `${ps[0].name}<br/>日均 GMV ${money(ps[0].value * 1e4)} 元` },
    grid: { ...gridBase, top: 18 },
    xAxis: { type: 'value', ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => v + ' 万' } },
    yAxis: { type: 'category', data: d.festival.names.slice().reverse(), ...axisBase, splitLine: { show: false } },
    series: [{
      type: 'bar', data: d.festival.daily.slice().reverse().map(wan), barMaxWidth: 14,
      itemStyle: {
        borderRadius: [0, 4, 4, 0],
        color: (p) => (p.value >= 4 ? C.red : p.value >= 2.5 ? C.orange : C.blue),
      },
      label: { show: true, position: 'right', color: C.muted, fontSize: 11, formatter: (p) => p.value.toFixed(1) + ' 万' },
    }],
  }, true);

  // 门店
  chartOf('chart-store').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { ...gridBase, top: 18 },
    xAxis: { type: 'value', ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => v + ' 万' } },
    yAxis: { type: 'category', data: d.store.names.slice().reverse(), ...axisBase, splitLine: { show: false } },
    series: [{
      type: 'bar', data: d.store.gmv.slice().reverse().map(wan), barMaxWidth: 14,
      itemStyle: { color: C.green, borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', color: C.muted, fontSize: 11, formatter: (p) => p.value.toFixed(1) + ' 万' },
    }],
  }, true);
}

/* ---- 3.2 RFM ---- */
async function renderRfm() {
  const d = await api('/api/rfm');
  const total = d.customers.reduce((a, b) => a + b, 0);
  const coreIdx = d.segments.map((s, i) => (s === '重要价值客户' || s === '重要保持客户' ? i : -1)).filter((i) => i >= 0);
  const coreCust = coreIdx.reduce((a, i) => a + d.cust_share[i], 0);
  const coreGmv = coreIdx.reduce((a, i) => a + d.gmv_share[i], 0);
  const churnIdx = d.segments.map((s, i) => (s.includes('挽留') ? i : -1)).filter((i) => i >= 0);
  const churnGmv = churnIdx.reduce((a, i) => a + d.gmv_share[i], 0);

  $('#rfm-kpi').innerHTML = [
    kpiCard('客户总数', n0(total), '有成交记录的客户'),
    kpiCard('核心客户占比', pct(coreCust), '重要价值 + 重要保持'),
    kpiCard('核心客户 GMV 占比', pct(coreGmv), '二八结构验证'),
    kpiCard('挽留类 GMV 占比', pct(churnGmv), `${n0(churnIdx.reduce((a, i) => a + d.customers[i], 0))} 人待触达`),
  ].join('');

  chartOf('chart-rfm-seg').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' },
      valueFormatter: (v) => v.toFixed(1) + '%' },
    legend: { ...legendBase, data: ['人数占比', '消费额占比'] },
    toolbox: toolboxBase,
    grid: { ...gridBase, bottom: 42 },
    xAxis: { type: 'category', data: d.segments, ...axisBase,
      axisLabel: { color: C.muted, fontSize: 10.5, interval: 0, rotate: 22 } },
    yAxis: { type: 'value', ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' } },
    series: [
      { name: '人数占比', type: 'bar', barMaxWidth: 16, data: d.cust_share.map((v) => +(v * 100).toFixed(1)),
        itemStyle: { color: '#3b4a68', borderRadius: [3, 3, 0, 0] } },
      { name: '消费额占比', type: 'bar', barMaxWidth: 16, data: d.gmv_share.map((v) => +(v * 100).toFixed(1)),
        itemStyle: { color: C.blue, borderRadius: [3, 3, 0, 0] } },
    ],
  }, true);

  const segColor = {};
  d.segments.forEach((s, i) => { segColor[s] = PALETTE[i % PALETTE.length]; });
  const series = d.segments.map((s) => ({
    name: s, type: 'scatter', symbolSize: (v) => Math.min(22, 4 + Math.sqrt(v[2]) / 9),
    data: d.scatter.filter((p) => p[3] === s).map((p) => [p[1], p[0], p[2]]),
    itemStyle: { color: segColor[s], opacity: 0.72 }, emphasis: { focus: 'series' },
  }));
  chartOf('chart-rfm-scatter').setOption({
    tooltip: { ...tooltipBase, formatter: (p) => `${p.seriesName}<br/>频次 ${p.value[0]} 次<br/>最近 ${p.value[1]} 天<br/>消费 ${money(p.value[2])} 元` },
    legend: { ...legendBase, type: 'scroll', bottom: 0, top: 'auto' },
    grid: { ...gridBase, bottom: 54 },
    xAxis: { type: 'value', name: '购买频次 F', nameTextStyle: { color: C.faint, fontSize: 11 },
      ...axisBase, scale: true },
    yAxis: { type: 'value', name: '最近购买 R（天）', nameTextStyle: { color: C.faint, fontSize: 11 },
      ...axisBase, inverse: true, scale: true },
    series,
  }, true);

  const rows = d.segments.map((s, i) => `<tr>
      <td>${s}</td>
      <td class="num">${n0(d.customers[i])}</td>
      <td class="num">${pct(d.cust_share[i])}</td>
      <td class="num">${pct(d.gmv_share[i])}</td>
      <td class="num">${n0(d.avg_r[i])}</td>
      <td class="num">${n1(d.avg_f[i])}</td>
      <td class="num">${n0(d.avg_m[i])}</td>
    </tr>`).join('');
  $('#table-rfm').innerHTML = `<thead><tr>
      <th>分群</th><th class="num">客户数</th><th class="num">人数占比</th>
      <th class="num">消费额占比</th><th class="num">平均R(天)</th><th class="num">平均F(次)</th><th class="num">平均消费(元)</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

/* ---- 3.3 商品 ---- */
async function renderProduct() {
  const d = await api('/api/product');
  const abc = d.abc;
  const aCnt = abc.products[abc.classes.indexOf('A')] || 0;
  const total = abc.products.reduce((x, y) => x + y, 0);

  $('#abc-kpi').innerHTML = [
    kpiCard('在售商品', n0(total), `A ${abc.products[0] || 0} / B ${abc.products[1] || 0} / C ${abc.products[2] || 0}`),
    kpiCard('A 类商品数', n0(aCnt), '累计销售额前 80%'),
    kpiCard('TOP1 单品占比', pct(d.pareto.values[0] / d.pareto.values.reduce((x, y) => x + y, 0)),
      d.table[0] ? d.table[0].name : ''),
  ].join('');

  chartOf('chart-pareto').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const p = ps[0];
        return `第 ${+p.dataIndex + 1} 名<br/>销售额 ${money(p.value * 1e4)} 元`;
      } },
    legend: { ...legendBase, data: ['销售额', '累计占比'] },
    toolbox: toolboxBase,
    grid: { ...gridBase, bottom: 30 },
    xAxis: { type: 'category', data: d.pareto.names.map((v, i) => i + 1), ...axisBase,
      axisLabel: { color: C.muted, fontSize: 10.5, interval: 4 }, name: '商品排名',
      nameTextStyle: { color: C.faint, fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '万元', nameTextStyle: { color: C.faint, fontSize: 11 }, ...axisBase },
      { type: 'value', name: '累计', max: 100, nameTextStyle: { color: C.faint, fontSize: 11 },
        ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '销售额', type: 'bar', data: d.pareto.values.map(wan), barMaxWidth: 12,
        itemStyle: { color: C.blue, borderRadius: [2, 2, 0, 0] } },
      { name: '累计占比', type: 'line', yAxisIndex: 1, data: d.pareto.cum.map((v) => +(v * 100).toFixed(1)),
        smooth: true, symbol: 'none', lineStyle: { color: C.orange, width: 2 },
        markLine: { silent: true, symbol: 'none', label: { color: C.faint, fontSize: 10, formatter: '{b}' },
          data: [{ yAxis: 80, name: '80%', lineStyle: { color: C.faint, type: 'dashed' } }] } },
    ],
  }, true);

  const cls = d.bcg_summary.classes;
  const colorOf = { 明星: C.red, 金牛: C.blue, 问题: C.orange, 瘦狗: C.gray };
  const bcgSeries = cls.map((k) => ({
    name: k, type: 'scatter',
    data: d.bcg.filter((p) => p.class === k).map((p) => [p.x, +(p.y * 100).toFixed(1), p.size, p.name]),
    symbolSize: (v) => Math.min(38, 8 + Math.sqrt(v[2]) / 16),
    itemStyle: { color: colorOf[k], opacity: 0.78, borderColor: '#0e1421' },
  }));
  chartOf('chart-bcg').setOption({
    tooltip: { ...tooltipBase, formatter: (p) => `${p.value[3]}<br/>分类：${p.seriesName}<br/>销售额 ${money(p.value[0])} 元<br/>同比 ${p.value[1]}%` },
    legend: { ...legendBase },
    grid: { ...gridBase, bottom: 28 },
    xAxis: { type: 'value', name: '销售额（万元）', nameTextStyle: { color: C.faint, fontSize: 11 },
      ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => wan(v) } },
    yAxis: { type: 'value', name: '同比（%）', nameTextStyle: { color: C.faint, fontSize: 11 },
      ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' } },
    series: bcgSeries,
  }, true);

  const rows = d.table.map((r) => `<tr>
      <td>${r.rank}</td>
      <td>${r.name}</td>
      <td><span class="tag ${r.type === 'BUNDLE' ? 'tag-blue' : 'tag-green'}">${r.type === 'BUNDLE' ? '花束' : 'SKU'}</span></td>
      <td>${r.category}</td>
      <td class="num">${n0(r.qty)}</td>
      <td class="num">${n0(r.amt)}</td>
      <td class="num">${pct(r.margin)}</td>
      <td class="num ${r.growth >= 0 ? 'up' : 'down'}">${pct(r.growth)}</td>
      <td><span class="tag tag-gray">${r.abc}</span></td>
      <td><span class="tag ${r.bcg === '明星' ? 'tag-red' : r.bcg === '金牛' ? 'tag-blue' : r.bcg === '问题' ? 'tag-orange' : 'tag-gray'}">${r.bcg}</span></td>
    </tr>`).join('');
  $('#table-product').innerHTML = `<thead><tr>
      <th>#</th><th>商品</th><th>类型</th><th>品类</th><th class="num">销量</th>
      <th class="num">销售额</th><th class="num">毛利率</th><th class="num">同比</th><th>ABC</th><th>BCG</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

/* ---- 3.4 促销与渠道 ---- */
async function renderPromo() {
  const [p, ch] = await Promise.all([api('/api/promo'), api('/api/channel')]);

  const top = p.roi.slice(0, 10).slice().reverse();
  chartOf('chart-promo').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const r = top[ps[0].dataIndex];
        return `${r.name}<br/>GMV ${money(r.gmv)} 元<br/>让利 ${money(r.discount)} 元<br/>毛利 ${money(r.profit)} 元<br/>让利效率 ${n2(r.roi)}`;
      } },
    grid: { ...gridBase, left: 10, right: 70 },
    xAxis: { type: 'value', ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => v + ' 万' } },
    yAxis: { type: 'category', data: top.map((r) => r.name.length > 14 ? r.name.slice(0, 14) + '…' : r.name),
      ...axisBase, axisLabel: { color: C.muted, fontSize: 10.5 }, splitLine: { show: false } },
    series: [{
      type: 'bar', data: top.map((r) => wan(r.gmv)), barMaxWidth: 13,
      itemStyle: { color: C.purple, borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', color: C.muted, fontSize: 10.5,
        formatter: (pp) => '效率 ' + n2(top[pp.dataIndex].roi) },
    }],
  }, true);

  chartOf('chart-discount').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis' },
    legend: { ...legendBase, data: ['订单占比', '毛利率'] },
    grid: { ...gridBase, bottom: 30 },
    xAxis: { type: 'category', data: p.buckets.names, ...axisBase, axisLabel: { color: C.muted, fontSize: 10.5, rotate: 20 } },
    yAxis: [
      { type: 'value', ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' } },
      { type: 'value', ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '订单占比', type: 'bar', barMaxWidth: 22,
        data: p.buckets.order_share.map((v) => +(v * 100).toFixed(1)),
        itemStyle: { color: C.blue, borderRadius: [3, 3, 0, 0] } },
      { name: '毛利率', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
        data: p.buckets.margin.map((v) => (v == null ? null : +(v * 100).toFixed(1))),
        itemStyle: { color: C.red }, lineStyle: { width: 2 } },
    ],
  }, true);

  chartOf('chart-channel-scatter').setOption({
    tooltip: { ...tooltipBase, formatter: (pp) => `${pp.value[3]}<br/>客单价 ${n0(pp.value[0])} 元<br/>佣金后净利率 ${n2(pp.value[1])}%<br/>GMV ${money(pp.value[2])} 元` },
    grid: { ...gridBase, bottom: 34 },
    xAxis: { type: 'value', name: '客单价（元）', nameTextStyle: { color: C.faint, fontSize: 11 }, ...axisBase },
    yAxis: { type: 'value', name: '净利率（%）', nameTextStyle: { color: C.faint, fontSize: 11 },
      ...axisBase, axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' } },
    series: [{
      type: 'scatter',
      data: ch.scatter.map((r) => [Math.round(r.aov), +(r.margin * 100).toFixed(2), r.gmv, r.name]),
      symbolSize: (v) => Math.min(46, 12 + Math.sqrt(v[2]) / 900),
      itemStyle: { color: (pp) => (ch.scatter[pp.dataIndex].type === '线上' ? C.blue : C.green), opacity: .8 },
      label: { show: true, position: 'top', color: C.muted, fontSize: 10.5, formatter: (pp) => pp.value[3] },
    }],
  }, true);

  const rows = ch.table.map((r) => `<tr>
      <td>${r.name}</td>
      <td><span class="tag ${r.type === '线上' ? 'tag-blue' : 'tag-green'}">${r.type}</span></td>
      <td class="num">${n0(r.orders)}</td>
      <td class="num">${n0(r.gmv)}</td>
      <td class="num">${pct(r.share)}</td>
      <td class="num">${n0(r.aov)}</td>
      <td class="num">${n0(r.commission)}</td>
      <td class="num">${pct(r.margin)}</td>
    </tr>`).join('');
  $('#table-channel').innerHTML = `<thead><tr>
      <th>渠道</th><th>类型</th><th class="num">订单</th><th class="num">GMV</th>
      <th class="num">占比</th><th class="num">客单价</th><th class="num">佣金</th><th class="num">净利率</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

/* ---- 3.5 库存 ---- */
async function renderInventory() {
  const d = await api('/api/inventory');
  chartOf('chart-inventory').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { ...legendBase, data: ['周转天数', '损耗率'] },
    toolbox: toolboxBase,
    grid: { ...gridBase, bottom: 34 },
    xAxis: { type: 'category', data: d.categories, ...axisBase, axisLabel: { color: C.muted, fontSize: 10.5, rotate: 22 } },
    yAxis: [
      { type: 'value', name: '天', nameTextStyle: { color: C.faint, fontSize: 11 }, ...axisBase },
      { type: 'value', name: '损耗率', nameTextStyle: { color: C.faint, fontSize: 11 }, ...axisBase,
        axisLabel: { color: C.muted, fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '周转天数', type: 'bar', barMaxWidth: 20, data: d.turnover.map((v) => n1(v)),
        itemStyle: { color: C.blue, borderRadius: [3, 3, 0, 0] } },
      { name: '损耗率', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
        data: d.waste_rate.map((v) => +(v * 100).toFixed(2)),
        itemStyle: { color: C.red }, lineStyle: { width: 2 } },
    ],
  }, true);

  const rows = d.slow.map((r, i) => `<tr>
      <td>${i + 1}</td><td>${r.name}</td><td>${r.category}</td>
      <td class="num">${n0(r.qty)}</td><td class="num">${n0(r.amt)}</td>
    </tr>`).join('');
  $('#table-slow').innerHTML = `<thead><tr><th>#</th><th>商品</th><th>品类</th>
      <th class="num">销量</th><th class="num">销售额</th></tr></thead><tbody>${rows}</tbody>`;
}

/* ---- 3.6 预测 ---- */
async function renderForecast() {
  const d = await api('/api/forecast');
  const m = d.metrics;
  const peakIdx = d.forecast.values.indexOf(Math.max(...d.forecast.values));

  $('#fc-kpi').innerHTML = [
    kpiCard('回测 MAPE', n2(m.mape) + '%', '留出最后 30 天'),
    kpiCard('未来 30 天预测', money(m.sum), 'SARIMAX + 外生变量'),
    kpiCard('预测峰值日', d.forecast.dates[peakIdx] || '—', '预计 ' + money(d.forecast.values[peakIdx])),
  ].join('');

  const histDates = d.history.dates;
  const fcDates = d.forecast.dates;
  const dates = histDates.concat(fcDates);
  const padH = new Array(histDates.length).fill(null);
  const padF = new Array(fcDates.length).fill(null);

  chartOf('chart-forecast').setOption({
    tooltip: { ...tooltipBase, trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : money(v) + ' 元') },
    legend: { ...legendBase, data: ['实际 GMV', '预测 GMV', '区间上界', '区间下界'] },
    toolbox: toolboxBase,
    grid: { ...gridBase, bottom: 28 },
    xAxis: { type: 'category', data: dates, ...axisBase,
      axisLabel: { color: C.muted, fontSize: 10.5, interval: 11, rotate: 0 } },
    yAxis: { type: 'value', name: 'GMV / 万元', nameTextStyle: { color: C.faint, fontSize: 11 }, ...axisBase },
    series: [
      { name: '实际 GMV', type: 'line', smooth: true, symbol: 'none', lineStyle: { width: 1.8, color: C.blue },
        data: d.history.values.map(wan).concat(padF) },
      { name: '预测 GMV', type: 'line', smooth: true, symbol: 'none', lineStyle: { width: 2.2, color: C.red, type: 'solid' },
        data: padH.concat(d.forecast.values.map(wan)) },
      { name: '区间上界', type: 'line', smooth: true, symbol: 'none',
        lineStyle: { width: 1, color: 'rgba(240,78,78,.35)', type: 'dashed' },
        data: padH.concat(d.forecast.upper.map(wan)) },
      { name: '区间下界', type: 'line', smooth: true, symbol: 'none',
        lineStyle: { width: 1, color: 'rgba(240,78,78,.35)', type: 'dashed' },
        data: padH.concat(d.forecast.lower.map(wan)) },
    ],
  }, true);

  const rows = fcDates.slice(0, 14).map((dt, i) => `<tr>
      <td>${dt}</td>
      <td>${d.forecast.festivals[i] || '—'}</td>
      <td class="num">${n0(d.forecast.values[i])}</td>
      <td class="num">${n0(d.forecast.lower[i])}</td>
      <td class="num">${n0(d.forecast.upper[i])}</td>
    </tr>`).join('');
  $('#table-forecast').innerHTML = `<thead><tr><th>日期</th><th>节日</th>
      <th class="num">预测 GMV（元）</th><th class="num">下界</th><th class="num">上界</th></tr></thead>
    <tbody>${rows}</tbody>`;
}

/* ---------------- 4. 路由与初始化 ---------------- */
const PAGES = {
  overview: { title: '经营总览', sub: '全渠道 GMV、订单与毛利表现', render: renderOverview, filterable: true },
  rfm: { title: '用户价值', sub: 'RFM 八分群 · 谁最值钱、谁快走了', render: renderRfm, filterable: false },
  product: { title: '商品结构', sub: 'ABC 帕累托 · 波士顿矩阵', render: renderProduct, filterable: false },
  promo: { title: '促销与渠道', sub: '让利效率 · 折扣敏感度 · 佣金后净利', render: renderPromo, filterable: false },
  inventory: { title: '库存损耗', sub: '周转天数 · 损耗率 · 滞销识别', render: renderInventory, filterable: false },
  forecast: { title: '销售预测', sub: 'STL 分解 + SARIMAX 未来 30 天', render: renderForecast, filterable: false },
};

async function switchPage(name) {
  const cfg = PAGES[name];
  if (!cfg) return;
  state.page = name;

  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.page === name));
  document.querySelectorAll('.page').forEach((s) => s.classList.toggle('is-active', s.id === 'page-' + name));
  $('#page-title').textContent = cfg.title;
  $('#page-sub').textContent = cfg.sub;

  // 非总览页锁定筛选（这些页读的是预计算结果，全量口径）
  const bar = $('#filter-bar');
  bar.style.opacity = cfg.filterable ? '1' : '.42';
  bar.style.pointerEvents = cfg.filterable ? 'auto' : 'none';
  $('#scope-badge').hidden = cfg.filterable;

  if (!state.rendered.has(name)) {
    loading(true);
    try {
      await cfg.render();
      state.rendered.add(name);
    } catch (e) {
      console.error(e);
      alert('加载失败：' + e.message);
    } finally {
      loading(false);
    }
  }
  resizePage(name);
}

function readFilters() {
  return {
    start: $('#f-start').value || '',
    end: $('#f-end').value || '',
    channel: $('#f-channel').value || '',
    store: $('#f-store').value || '',
  };
}

async function applyFilters() {
  state.filters = readFilters();
  loading(true);
  try {
    await renderOverview();
    state.rendered.add('overview');
  } finally { loading(false); }
}

async function init() {
  loading(true);
  try {
    const meta = await api('/api/meta');
    state.meta = meta;
    $('#f-start').value = meta.date_start;
    $('#f-end').value = meta.date_end;
    meta.channels.forEach((c) => $('#f-channel').insertAdjacentHTML('beforeend', `<option value="${c}">${c}</option>`));
    meta.stores.forEach((s) => $('#f-store').insertAdjacentHTML('beforeend', `<option value="${s}">${s}</option>`));

    document.querySelectorAll('.nav-item').forEach((b) => {
      b.addEventListener('click', () => switchPage(b.dataset.page));
    });
    $('#btn-apply').addEventListener('click', applyFilters);
    $('#btn-reset').addEventListener('click', async () => {
      $('#f-start').value = meta.date_start;
      $('#f-end').value = meta.date_end;
      $('#f-channel').value = '';
      $('#f-store').value = '';
      await applyFilters();
    });
    window.addEventListener('resize', () => resizePage(state.page));

    state.filters = readFilters();
    await switchPage('overview');
  } catch (e) {
    console.error(e);
    alert('初始化失败：' + e.message);
  } finally {
    loading(false);
  }
}

document.addEventListener('DOMContentLoaded', init);
