<template>
  <div class="bt-page">
    <!-- ── 顶部导航栏 ───────────────────────────────────────────── -->
    <header class="bt-topbar">
      <div class="bt-topbar-left">
        <el-button text @click="router.push('/')">
          <el-icon><ArrowLeft /></el-icon> 返回监控台
        </el-button>
        <span class="bt-title">回测与分析</span>
      </div>
      <div class="bt-topbar-right">
        <span v-if="lastRunTime" class="run-time">上次运行：{{ lastRunTime }}</span>
      </div>
    </header>

    <BacktestConfigForm
      v-model:form="form"
      :strategy-catalog="strategyCatalog"
      :editable-strategy-params="editableStrategyParams"
      :running="running"
      @strategy-change="onStrategyChange"
      @run="doRunBacktest"
    />

    <!-- ── 进度提示 ─────────────────────────────────────────────── -->
    <el-alert
      v-if="running"
      title="正在运行回测，请稍候（通常需要 5~30 秒）…"
      type="info"
      show-icon
      :closable="false"
      class="bt-running-tip"
    />

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      show-icon
      closable
      @close="errorMsg = ''"
      class="bt-running-tip"
    />

    <!-- ── 结果区域（回测完成后显示） ──────────────────────────── -->
    <template v-if="result">
      <!-- KPI 概览 -->
      <div class="kpi-grid">
        <div
          v-for="kpi in kpiCards"
          :key="kpi.key"
          class="kpi-card"
          :class="kpi.colorClass"
        >
          <div class="kpi-label">{{ kpi.label }}</div>
          <div class="kpi-value">{{ kpi.value }}</div>
          <div class="kpi-sub">{{ kpi.sub }}</div>
        </div>
      </div>

      <!-- 资金曲线 + 买卖点 -->
      <el-card class="bt-chart-card" shadow="never">
        <template #header>
          <div class="chart-header">
            <span class="card-title">资金曲线 &amp; 交易标记</span>
            <div class="chart-legend">
              <span class="legend-dot buy">▲ 买入/平空</span>
              <span class="legend-dot sell">▼ 卖出/开空</span>
            </div>
          </div>
        </template>
        <div ref="equityChartRef" class="chart-equity"></div>
      </el-card>

      <!-- 收益分布 + 月度热力图 -->
      <div class="two-col-charts">
        <el-card class="bt-chart-card half" shadow="never">
          <template #header>
            <span class="card-title">日收益率分布</span>
          </template>
          <div ref="distChartRef" class="chart-half"></div>
        </el-card>

        <el-card class="bt-chart-card half" shadow="never">
          <template #header>
            <span class="card-title">月度收益热力图</span>
          </template>
          <div ref="heatChartRef" class="chart-half"></div>
        </el-card>
      </div>

      <!-- 风险指标表 -->
      <el-card class="bt-chart-card" shadow="never">
        <template #header>
          <span class="card-title">风险与绩效指标</span>
        </template>
        <el-table :data="riskRows" size="small" :border="false" class="risk-table">
          <el-table-column prop="category" label="类别" width="90" />
          <el-table-column prop="name"     label="指标"  width="160" />
          <el-table-column prop="value"    label="数值"  width="130">
            <template #default="{ row }">
              <span :class="row.valueClass">{{ row.value }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="rating"   label="评级"  width="100">
            <template #default="{ row }">
              <el-tag v-if="row.rating" :type="row.ratingType" size="small">{{ row.rating }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="desc"     label="说明" />
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { fetchBacktestStrategies, runBacktest } from '@/api/index.js'
import { COMMON_PARAM_KEYS, DEFAULT_BACKTEST_FORM } from '@/config/backtest.js'
import BacktestConfigForm from '@/components/BacktestConfigForm.vue'

const router = useRouter()

// ── 状态 ────────────────────────────────────────────────────────────────
const strategyCatalog = ref([])
const running         = ref(false)
const errorMsg        = ref('')
const result          = ref(null)
const lastRunTime     = ref('')

// ECharts 实例
const equityChartRef = ref(null)
const distChartRef   = ref(null)
const heatChartRef   = ref(null)
let equityChart, distChart, heatChart

// ── 表单 ────────────────────────────────────────────────────────────────
const form = ref(structuredClone(DEFAULT_BACKTEST_FORM))

// 仅展示策略专属参数（排除 symbol 和通用字段）
const editableStrategyParams = computed(() => {
  const params = form.value.strategy_params
  return Object.fromEntries(
    Object.entries(params).filter(([k]) => !COMMON_PARAM_KEYS.has(k))
  )
})

// ── 策略切换：重置参数 ────────────────────────────────────────────────
function onStrategyChange(name) {
  const catalog = strategyCatalog.value.find(s => s.name === name)
  if (!catalog) return
  form.value.strategy_params = { ...catalog.default_params }
}

// ── 运行回测 ──────────────────────────────────────────────────────────
async function doRunBacktest() {
  errorMsg.value = ''
  running.value  = true
  result.value   = null

  try {
    const data = await runBacktest({ ...form.value })
    if (!data.success) throw new Error(data.error ?? '回测失败')
    result.value  = data
    lastRunTime.value = new Date().toLocaleTimeString('zh-CN')
    ElMessage.success('回测完成')
    await nextTick()
    renderCharts()
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    running.value = false
  }
}

// ── KPI 卡片 ──────────────────────────────────────────────────────────
const kpiCards = computed(() => {
  if (!result.value) return []
  const m = result.value.metrics
  const sign = v => v > 0 ? '+' : ''

  return [
    {
      key: 'total_return', label: '总收益率',
      value: `${sign(m.total_return)}${m.total_return.toFixed(2)}%`,
      sub: `年化 ${sign(m.annual_return)}${m.annual_return.toFixed(2)}%`,
      colorClass: m.total_return >= 0 ? 'kpi-green' : 'kpi-red',
    },
    {
      key: 'sharpe_ratio', label: '夏普比率',
      value: m.sharpe_ratio.toFixed(3),
      sub: m.sharpe_ratio >= 2 ? '优秀' : m.sharpe_ratio >= 1 ? '良好' : '一般',
      colorClass: m.sharpe_ratio >= 1 ? 'kpi-green' : 'kpi-yellow',
    },
    {
      key: 'max_drawdown_pct', label: '最大回撤',
      value: `-${m.max_drawdown_pct.toFixed(2)}%`,
      sub: `Calmar ${m.calmar_ratio.toFixed(2)}`,
      colorClass: m.max_drawdown_pct <= 15 ? 'kpi-yellow' : 'kpi-red',
    },
    {
      key: 'win_rate', label: '胜率',
      value: `${m.win_rate.toFixed(1)}%`,
      sub: `盈亏比 ${m.profit_loss_ratio.toFixed(2)}`,
      colorClass: m.win_rate >= 50 ? 'kpi-green' : 'kpi-yellow',
    },
    {
      key: 'total_trades', label: '总交易次数',
      value: m.total_trades,
      sub: `盈 ${m.winning_trades}  亏 ${m.losing_trades}`,
      colorClass: '',
    },
    {
      key: 'volatility', label: '年化波动率',
      value: `${m.volatility.toFixed(2)}%`,
      sub: `Sortino ${m.sortino_ratio.toFixed(2)}`,
      colorClass: m.volatility <= 20 ? 'kpi-green' : 'kpi-yellow',
    },
    {
      key: 'var_95', label: 'VaR (95%)',
      value: `${m.var_95.toFixed(2)}%`,
      sub: `CVaR ${m.cvar_95.toFixed(2)}%`,
      colorClass: 'kpi-neutral',
    },
    {
      key: 'avg_win', label: '平均盈利',
      value: `¥${m.avg_win.toFixed(0)}`,
      sub: `平均亏损 ¥${Math.abs(m.avg_loss).toFixed(0)}`,
      colorClass: m.avg_win > Math.abs(m.avg_loss) ? 'kpi-green' : 'kpi-red',
    },
  ]
})

// ── 风险指标表 ────────────────────────────────────────────────────────
const riskRows = computed(() => {
  if (!result.value) return []
  const m = result.value.metrics
  const sign = v => v > 0 ? '+' : ''

  function rateGrade(val, good, ok) {
    if (val >= good) return { rating: '优秀', ratingType: 'success' }
    if (val >= ok)   return { rating: '良好', ratingType: '' }
    return              { rating: '一般', ratingType: 'warning' }
  }

  return [
    // 收益类
    {
      category: '收益',
      name: '总收益率', value: `${sign(m.total_return)}${m.total_return.toFixed(3)}%`,
      valueClass: m.total_return >= 0 ? 'pos' : 'neg',
      desc: '回测期间整体涨跌幅',
      ...rateGrade(m.total_return, 20, 5),
    },
    {
      category: '收益',
      name: '年化收益率', value: `${sign(m.annual_return)}${m.annual_return.toFixed(3)}%`,
      valueClass: m.annual_return >= 0 ? 'pos' : 'neg',
      desc: '以 252 交易日折算的年化收益',
      ...rateGrade(m.annual_return, 15, 8),
    },
    // 风险类
    {
      category: '风险',
      name: '年化波动率', value: `${m.volatility.toFixed(3)}%`,
      valueClass: '',
      desc: '日收益率标准差 × √252',
      ...rateGrade(30 - m.volatility, 10, 0),
    },
    {
      category: '风险',
      name: '最大回撤', value: `-${m.max_drawdown_pct.toFixed(3)}%`,
      valueClass: 'neg',
      desc: '历史最大峰谷回撤幅度',
      ...rateGrade(25 - m.max_drawdown_pct, 15, 5),
    },
    {
      category: '风险',
      name: 'VaR (95%)', value: `${m.var_95.toFixed(3)}%`,
      valueClass: m.var_95 < 0 ? 'neg' : '',
      desc: '95% 置信区间单日最大亏损',
      rating: '', ratingType: '',
    },
    {
      category: '风险',
      name: 'CVaR (95%)', value: `${m.cvar_95.toFixed(3)}%`,
      valueClass: m.cvar_95 < 0 ? 'neg' : '',
      desc: '超出 VaR 部分的期望损失（条件VaR）',
      rating: '', ratingType: '',
    },
    {
      category: '风险',
      name: '下行波动率', value: `${m.downside_vol.toFixed(3)}%`,
      valueClass: '',
      desc: '仅考虑负收益的波动率',
      rating: '', ratingType: '',
    },
    // 比率类
    {
      category: '比率',
      name: '夏普比率', value: m.sharpe_ratio.toFixed(4),
      valueClass: m.sharpe_ratio > 0 ? 'pos' : 'neg',
      desc: '超额收益与波动率之比（越高越好）',
      ...rateGrade(m.sharpe_ratio, 2, 1),
    },
    {
      category: '比率',
      name: 'Sortino 比率', value: m.sortino_ratio.toFixed(4),
      valueClass: m.sortino_ratio > 0 ? 'pos' : 'neg',
      desc: '超额收益与下行波动率之比',
      ...rateGrade(m.sortino_ratio, 2, 1),
    },
    {
      category: '比率',
      name: 'Calmar 比率', value: m.calmar_ratio.toFixed(4),
      valueClass: m.calmar_ratio > 0 ? 'pos' : 'neg',
      desc: '年化收益与最大回撤之比',
      ...rateGrade(m.calmar_ratio, 1, 0.5),
    },
    // 交易统计
    {
      category: '交易',
      name: '胜率', value: `${m.win_rate.toFixed(2)}%`,
      valueClass: m.win_rate >= 50 ? 'pos' : 'neg',
      desc: '盈利交易次数占比',
      ...rateGrade(m.win_rate, 60, 50),
    },
    {
      category: '交易',
      name: '盈亏比', value: m.profit_loss_ratio.toFixed(4),
      valueClass: m.profit_loss_ratio >= 1 ? 'pos' : 'neg',
      desc: '平均盈利额 / 平均亏损额',
      ...rateGrade(m.profit_loss_ratio, 1.5, 1),
    },
    {
      category: '交易',
      name: '总交易次数', value: m.total_trades,
      valueClass: '',
      desc: `盈利 ${m.winning_trades} 次，亏损 ${m.losing_trades} 次`,
      rating: '', ratingType: '',
    },
    {
      category: '交易',
      name: '最大连续盈利', value: m.max_consecutive_wins,
      valueClass: 'pos',
      desc: '最多连续盈利次数',
      rating: '', ratingType: '',
    },
    {
      category: '交易',
      name: '最大连续亏损', value: m.max_consecutive_losses,
      valueClass: 'neg',
      desc: '最多连续亏损次数',
      rating: '', ratingType: '',
    },
    // 分布特征
    {
      category: '分布',
      name: '偏度', value: m.skewness.toFixed(4),
      valueClass: m.skewness > 0 ? 'pos' : 'neg',
      desc: '收益分布偏态（>0 右偏，有利）',
      rating: '', ratingType: '',
    },
    {
      category: '分布',
      name: '峰度', value: m.kurtosis.toFixed(4),
      valueClass: '',
      desc: '收益分布尖峰程度（>3 肥尾风险）',
      rating: '', ratingType: '',
    },
  ]
})

// ── ECharts 渲染 ──────────────────────────────────────────────────────
function renderCharts() {
  renderEquityChart()
  renderDistChart()
  renderHeatChart()
}

// 资金曲线 + 回撤 + 买卖标记
function renderEquityChart() {
  if (!equityChartRef.value) return
  if (equityChart) equityChart.dispose()
  equityChart = echarts.init(equityChartRef.value, 'dark')

  const ec = result.value.equity_curve
  const markers = result.value.trade_markers

  const dates     = ec.map(d => d.date)
  const capitals  = ec.map(d => d.capital)
  const drawdowns = ec.map(d => d.dd_pct)

  // 建立日期→资金映射，供标记点定位
  const capMap = new Map(ec.map(d => [d.date, d.capital]))

  // 买入标记（buy_open & cover_close → 绿三角向上）
  const buyMarkers = markers
    .filter(m => m.type === 'buy_open' || m.type === 'cover_close')
    .map(m => ({
      value: [m.date, capMap.get(m.date) ?? m.capital],
      tradeInfo: m,
    }))

  // 卖出标记（sell_close & short_open → 红三角向下）
  const sellMarkers = markers
    .filter(m => m.type === 'sell_close' || m.type === 'short_open')
    .map(m => ({
      value: [m.date, capMap.get(m.date) ?? m.capital],
      tradeInfo: m,
    }))

  const typeLabel = {
    buy_open:    '开多  ▲',
    sell_close:  '平多  ▼',
    short_open:  '开空  ▼',
    cover_close: '平空  ▲',
  }

  const fmt = v => v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const cfg = result.value.config

  equityChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    grid: [
      { top: 50, right: 80, bottom: 140, left: 80 },
      { top: '72%', right: 80, bottom: 50, left: 80 },
    ],
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    tooltip: {
      trigger: 'item',
      backgroundColor: '#1a1f2e',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 12 },
      formatter(params) {
        const ti = params.data?.tradeInfo
        if (!ti) return ''
        const pnlStr = ti.pnl !== 0
          ? `<br/>盈亏: <span style="color:${ti.pnl >= 0 ? '#3fb950' : '#f85149'}">${ti.pnl >= 0 ? '+' : ''}¥${fmt(ti.pnl)}</span>`
          : ''
        return (
          `<b>${ti.date}</b>&nbsp;<span style="color:${ti.type.startsWith('buy') || ti.type === 'cover_close' ? '#3fb950' : '#f85149'}">${typeLabel[ti.type] ?? ti.type}</span><br/>` +
          `合约: ${ti.symbol}&nbsp;数量: ${ti.volume} 手<br/>` +
          `成交价: <b>¥${ti.trade_price.toLocaleString()}</b>` +
          pnlStr +
          `<br/>手续费: ¥${ti.commission}`
        )
      },
    },
    xAxis: [
      {
        type: 'category', data: dates, gridIndex: 0, boundaryGap: false,
        axisLabel: { color: '#6e7681', fontSize: 11 },
        axisLine: { lineStyle: { color: '#30363d' } },
        splitLine: { show: false },
      },
      {
        type: 'category', data: dates, gridIndex: 1, boundaryGap: false,
        axisLabel: { color: '#6e7681', fontSize: 10, interval: 'auto' },
        axisLine: { lineStyle: { color: '#30363d' } },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value', gridIndex: 0, scale: true,
        axisLabel: {
          color: '#6e7681', fontSize: 11,
          formatter: v => '¥' + (v / 10000).toFixed(0) + 'w',
        },
        splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
      },
      {
        type: 'value', gridIndex: 1, max: 0,
        axisLabel: { color: '#6e7681', fontSize: 10, formatter: v => v.toFixed(1) + '%' },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      {
        type: 'slider', xAxisIndex: [0, 1],
        bottom: 10, height: 24,
        backgroundColor: '#161b22',
        dataBackground: { areaStyle: { color: '#30363d' } },
        selectedDataBackground: { areaStyle: { color: '#58a6ff40' } },
        handleStyle: { color: '#58a6ff' },
        textStyle: { color: '#6e7681' },
        borderColor: '#30363d',
      },
    ],
    legend: {
      top: 8, right: 16,
      textStyle: { color: '#8b949e', fontSize: 12 },
      itemStyle: { borderWidth: 0 },
    },
    series: [
      {
        name: `资金曲线（初始 ¥${(cfg.initial_capital / 10000).toFixed(0)}w）`,
        type: 'line', data: capitals,
        xAxisIndex: 0, yAxisIndex: 0,
        smooth: false, symbol: 'none',
        lineStyle: { color: '#58a6ff', width: 1.5 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(88,166,255,0.18)' },
            { offset: 1, color: 'rgba(88,166,255,0)' },
          ]),
        },
      },
      {
        name: '最大回撤',
        type: 'line', data: drawdowns,
        xAxisIndex: 1, yAxisIndex: 1,
        smooth: false, symbol: 'none',
        lineStyle: { color: '#f85149', width: 1 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(248,81,73,0.35)' },
            { offset: 1, color: 'rgba(248,81,73,0.05)' },
          ]),
        },
      },
      {
        name: '买入/平空',
        type: 'scatter',
        data: buyMarkers,
        xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'triangle', symbolSize: 10,
        itemStyle: { color: '#3fb950' },
        emphasis: { itemStyle: { color: '#3fb950', borderColor: '#fff', borderWidth: 2, shadowBlur: 6 } },
      },
      {
        name: '卖出/开空',
        type: 'scatter',
        data: sellMarkers,
        xAxisIndex: 0, yAxisIndex: 0,
        symbol: (_, params) => 'triangle',
        symbolRotate: 180,
        symbolSize: 10,
        itemStyle: { color: '#f85149' },
        emphasis: { itemStyle: { color: '#f85149', borderColor: '#fff', borderWidth: 2, shadowBlur: 6 } },
      },
    ],
  })
}

// 日收益率分布直方图
function renderDistChart() {
  if (!distChartRef.value) return
  if (distChart) distChart.dispose()
  distChart = echarts.init(distChartRef.value, 'dark')

  const daily = result.value.daily_returns

  // 生成分箱
  const BIN_COUNT = 40
  const min = Math.floor(Math.min(...daily) * 2) / 2
  const max = Math.ceil(Math.max(...daily) * 2) / 2
  const step = (max - min) / BIN_COUNT

  const bins = Array.from({ length: BIN_COUNT }, (_, i) => ({
    lo: min + i * step,
    hi: min + (i + 1) * step,
    count: 0,
  }))
  daily.forEach(r => {
    const idx = Math.min(Math.floor((r - min) / step), BIN_COUNT - 1)
    if (idx >= 0) bins[idx].count++
  })

  const xData  = bins.map(b => `${b.lo.toFixed(2)}%`)
  const counts = bins.map(b => b.count)
  const colors = bins.map(b => (b.lo + b.hi) / 2 >= 0 ? '#3fb950' : '#f85149')

  // 正态分布拟合曲线
  const mean = daily.reduce((a, b) => a + b, 0) / daily.length
  const std  = Math.sqrt(daily.reduce((a, r) => a + (r - mean) ** 2, 0) / daily.length)
  const totalArea = daily.length * step
  const normalCurve = bins.map(b => {
    const x = (b.lo + b.hi) / 2
    return +(totalArea * (1 / (std * Math.sqrt(2 * Math.PI))) * Math.exp(-0.5 * ((x - mean) / std) ** 2)).toFixed(1)
  })

  distChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 30, right: 20, bottom: 50, left: 50 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1a1f2e',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9' },
      formatter(params) {
        const b = bins[params[0].dataIndex]
        return `区间 [${b.lo.toFixed(2)}%, ${b.hi.toFixed(2)}%]<br/>频次: ${b.count} 天`
      },
    },
    xAxis: {
      type: 'category', data: xData,
      axisLabel: { color: '#6e7681', fontSize: 10, interval: 7, rotate: 30 },
      axisLine: { lineStyle: { color: '#30363d' } },
    },
    yAxis: [
      {
        type: 'value',
        axisLabel: { color: '#6e7681', fontSize: 11 },
        splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
      },
    ],
    series: [
      {
        name: '频次',
        type: 'bar', data: counts,
        itemStyle: { color: (p) => colors[p.dataIndex] },
        barMaxWidth: 16,
      },
      {
        name: '正态拟合',
        type: 'line', data: normalCurve,
        smooth: true, symbol: 'none',
        lineStyle: { color: '#e3b341', width: 1.5, type: 'dashed' },
      },
    ],
  })
}

// 月度收益热力图
function renderHeatChart() {
  if (!heatChartRef.value) return
  if (heatChart) heatChart.dispose()
  heatChart = echarts.init(heatChartRef.value, 'dark')

  const { years, data } = result.value.monthly_heatmap
  if (!data.length) return

  const MONTHS = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']

  const vals = data.map(d => d[2])
  const absMax = Math.max(Math.abs(Math.min(...vals)), Math.abs(Math.max(...vals)), 1)

  heatChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 20, right: 60, bottom: 30, left: 48 },
    tooltip: {
      position: 'top',
      backgroundColor: '#1a1f2e',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9' },
      formatter(p) {
        const month = MONTHS[p.data[0]]
        const year  = years[p.data[1]]
        const ret   = p.data[2]
        return `${year} ${month}<br/>月度收益: <b style="color:${ret >= 0 ? '#3fb950' : '#f85149'}">${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%</b>`
      },
    },
    xAxis: {
      type: 'category', data: MONTHS,
      splitArea: { show: true },
      axisLabel: { color: '#8b949e', fontSize: 11 },
      axisLine: { lineStyle: { color: '#30363d' } },
    },
    yAxis: {
      type: 'category', data: years,
      splitArea: { show: true },
      axisLabel: { color: '#8b949e', fontSize: 11 },
      axisLine: { lineStyle: { color: '#30363d' } },
    },
    visualMap: {
      min: -absMax, max: absMax,
      calculable: true,
      orient: 'vertical',
      right: 4, top: 'center',
      textStyle: { color: '#8b949e', fontSize: 10 },
      inRange: {
        color: ['#7b3135', '#21262d', '#1a4731'],
      },
    },
    series: [
      {
        name: '月度收益率',
        type: 'heatmap',
        data,
        label: {
          show: true,
          fontSize: 10,
          color: '#c9d1d9',
          formatter: p => p.data[2] ? (p.data[2] > 0 ? '+' : '') + p.data[2].toFixed(1) + '%' : '',
        },
        emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.5)' } },
      },
    ],
  })
}

// ── 窗口 resize ──────────────────────────────────────────────────────
function handleResize() {
  equityChart?.resize()
  distChart?.resize()
  heatChart?.resize()
}

// ── 生命周期 ─────────────────────────────────────────────────────────
onMounted(async () => {
  window.addEventListener('resize', handleResize)
  try {
    const { strategies } = await fetchBacktestStrategies()
    strategyCatalog.value = strategies ?? []
    if (strategyCatalog.value.length) {
      form.value.strategy_name = strategyCatalog.value[0].name
      form.value.strategy_params = { ...strategyCatalog.value[0].default_params }
    }
  } catch {
    // 离线/未登录时也可以用默认参数
    strategyCatalog.value = [
      { name: 'ma_cross', label: '双均线策略', desc: '金叉/死叉', default_params: { symbol: 'IF9999', fast_period: 10, slow_period: 20, position_ratio: 0.8 } },
      { name: 'rsi',      label: 'RSI 策略',  desc: '超买/超卖', default_params: { symbol: 'IF9999', rsi_period: 14, oversold: 30, overbought: 70, position_ratio: 0.8 } },
      { name: 'breakout', label: '突破策略',  desc: 'N日突破',  default_params: { symbol: 'IF9999', lookback_period: 20, position_ratio: 0.8 } },
    ]
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  equityChart?.dispose()
  distChart?.dispose()
  heatChart?.dispose()
})
</script>

<style scoped>
/* ── 页面结构 ─────────────────────────────────────────────────────────────── */
.bt-page {
  min-height: 100vh;
  background: var(--bg-base, #0d1117);
  padding: 0 0 48px;
  color: var(--text-primary, #c9d1d9);
}

/* ── 顶部导航栏 ──────────────────────────────────────────────────────────── */
.bt-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: var(--bg-surface, #161b22);
  border-bottom: 1px solid var(--border-color, #30363d);
  position: sticky;
  top: 0;
  z-index: 100;
}
.bt-topbar-left { display: flex; align-items: center; gap: 16px; }
.bt-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--text-primary, #c9d1d9);
}
.run-time { font-size: 12px; color: var(--text-muted, #6e7681); }

/* ── 配置卡片 ────────────────────────────────────────────────────────────── */
.bt-config-card {
  margin: 20px 24px 0;
  background: var(--bg-surface, #161b22) !important;
  border: 1px solid var(--border-color, #30363d) !important;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #c9d1d9);
}

.run-col {
  display: flex;
  align-items: flex-end;
  padding-bottom: 18px;
}
.run-btn { width: 100%; font-size: 14px; font-weight: 600; }

.strategy-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

/* ── 提示条 ──────────────────────────────────────────────────────────────── */
.bt-running-tip { margin: 12px 24px 0; }

/* ── KPI 卡片网格 ─────────────────────────────────────────────────────────── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px;
  margin: 20px 24px 0;
}

.kpi-card {
  background: var(--bg-surface, #161b22);
  border: 1px solid var(--border-color, #30363d);
  border-radius: 8px;
  padding: 14px 18px;
  transition: border-color 0.2s;
}
.kpi-card:hover { border-color: #58a6ff66; }

.kpi-label { font-size: 12px; color: var(--text-muted, #6e7681); margin-bottom: 6px; }
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub   { font-size: 11px; color: var(--text-muted, #6e7681); margin-top: 4px; }

.kpi-green .kpi-value { color: #3fb950; }
.kpi-red   .kpi-value { color: #f85149; }
.kpi-yellow .kpi-value { color: #e3b341; }
.kpi-neutral .kpi-value { color: #c9d1d9; }

/* ── 图表卡片 ─────────────────────────────────────────────────────────────── */
.bt-chart-card {
  margin: 16px 24px 0;
  background: var(--bg-surface, #161b22) !important;
  border: 1px solid var(--border-color, #30363d) !important;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.chart-legend { display: flex; gap: 16px; }
.legend-dot { font-size: 12px; color: var(--text-muted, #6e7681); }
.legend-dot.buy  { color: #3fb950; }
.legend-dot.sell { color: #f85149; }

.chart-equity { height: 480px; }
.two-col-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 16px 24px 0; }
.chart-half { height: 320px; }

/* ── 风险指标表 ──────────────────────────────────────────────────────────── */
.risk-table { background: transparent; }
.pos { color: #3fb950; font-weight: 600; }
.neg { color: #f85149; font-weight: 600; }

/* ── Element Plus 深色覆盖 ───────────────────────────────────────────────── */
:deep(.el-card__header) {
  background: var(--bg-surface, #161b22);
  border-bottom: 1px solid var(--border-color, #30363d);
  padding: 12px 18px;
}
:deep(.el-card__body) { padding: 16px 18px; }
:deep(.el-form-item__label) { color: var(--text-muted, #6e7681); font-size: 12px; }
:deep(.el-input__wrapper),
:deep(.el-input-number__wrapper),
:deep(.el-select__wrapper) {
  background-color: var(--bg-base, #0d1117) !important;
  box-shadow: 0 0 0 1px var(--border-color, #30363d) inset !important;
}
:deep(.el-input__inner),
:deep(.el-input-number__inner) { color: var(--text-primary, #c9d1d9); }
:deep(.el-select-dropdown__item.is-selected) { color: #58a6ff; }
:deep(.el-table__inner-wrapper) { background: transparent; }
:deep(.el-table tr),
:deep(.el-table th.el-table__cell) { background: transparent; color: var(--text-primary, #c9d1d9); }
:deep(.el-table td.el-table__cell) { border-bottom-color: var(--border-color, #30363d); }
:deep(.el-table--enable-row-hover .el-table__body tr:hover > td) { background: #21262d; }
:deep(.el-date-editor.el-input) { width: 100%; }

@media (max-width: 900px) {
  .two-col-charts { grid-template-columns: 1fr; }
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 600px) {
  .kpi-grid { grid-template-columns: 1fr 1fr; }
  .bt-topbar, .bt-config-card, .bt-chart-card, .kpi-grid { margin-left: 12px; margin-right: 12px; }
}
</style>
