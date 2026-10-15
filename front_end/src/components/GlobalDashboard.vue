<script setup>
/**
 * GlobalDashboard.vue — 全局仪表盘
 *
 * 布局：
 *   顶部状态栏 → 6 张 KPI 卡片 → 权益曲线 + 仓位分布 → 仓位明细表
 *
 * 数据源：/ws/dashboard（每 2 秒推送）
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useDashboardWs } from '@/composables/useDashboardWs.js'
import { formatAmount, formatSigned, formatPercent } from '@/utils/financialData.js'
import { loadEcharts } from '@/utils/asyncEcharts.js'

// ── WebSocket ───────────────────────────────────────────────────────────────
const { connected, data } = useDashboardWs()

// ── 柜台持仓盈亏 ─────────────────────────────────────────────────────────────
const displayPnl = computed(() => data.positionPnl)

// ── 闪烁 ────────────────────────────────────────────────────────────────────
const flashCls = ref('')
let flashTimer = null

watch(() => data.positionPnl, (nv, ov) => {
  if (flashTimer) clearTimeout(flashTimer)
  flashCls.value = nv > ov ? 'flash-up' : nv < ov ? 'flash-down' : ''
  flashTimer = setTimeout(() => { flashCls.value = '' }, 700)
})

onUnmounted(() => clearTimeout(flashTimer))

// ── 格式化工具 ────────────────────────────────────────────────────────────────
const fmt = {
  money: formatAmount, signed: formatSigned, rate: formatPercent, pct: formatPercent,
  num: (value, digits = 3) => formatAmount(value, digits),
}


const lastUpdate = computed(() => {
  if (!data.timestamp) return '--'
  try { return new Date(data.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) }
  catch { return data.timestamp }
})

// ── 颜色辅助 ─────────────────────────────────────────────────────────────────
function pnlCss(v) {
  const n = Number(v) || 0
  return n > 0 ? 'var(--q-green)' : n < 0 ? 'var(--q-red)' : 'var(--q-muted)'
}

function sharpeLevel(v) {
  if (v==null) return {cls:'',label:'需日终权益样本'}
  if (v >= 2)  return { cls: 'level-excellent', label: '优秀' }
  if (v >= 1)  return { cls: 'level-good',      label: '良好' }
  if (v >= 0)  return { cls: 'level-fair',       label: '一般' }
  return             { cls: 'level-poor',        label: '较差' }
}

function ddLevel(v) {
  return {cls: '', label: v == null ? '有效样本不足' : '监控窗口样本'}
}

// ── ECharts 权益曲线 ──────────────────────────────────────────────────────────
const lineRef  = ref(null)
let lineChart  = null
let echarts    = null
let disposed = false

function buildLineOption(curve) {
  const xs = curve.map(p => p.ts)
  const ys = curve.map(p => p.v)
  const lastVal = ys[ys.length - 1] ?? 0
  const lineColor = lastVal >= 0 ? '#3fb950' : '#f85149'
  const areaColor = lastVal >= 0
    ? [{ offset: 0, color: 'rgba(63,185,80,0.25)' },  { offset: 1, color: 'rgba(63,185,80,0)' }]
    : [{ offset: 0, color: 'rgba(248,81,73,0.25)' },  { offset: 1, color: 'rgba(248,81,73,0)' }]

  return {
    backgroundColor: 'transparent',
    title: {text: curve.length ? '' : '等待有效账户样本', left: 'center', top: 'middle', textStyle: {color: '#6e7681', fontSize: 14}},
    grid:   { top: 14, right: 12, bottom: 28, left: 52 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1a1f2e',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 12 },
      formatter: (params) => {
        const p = params[0]
        return `${p.axisValue}<br/><b style="color:${lineColor}">${(p.data || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</b>`
      },
    },
    xAxis: {
      type: 'category', data: xs,
      axisLine:  { lineStyle: { color: '#30363d' } },
      axisLabel: { color: '#6e7681', fontSize: 10, interval: Math.floor(xs.length / 5) || 0 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLine:  { show: false },
      axisLabel: {
        color: '#6e7681', fontSize: 10,
        formatter: v => v.toLocaleString(),
      },
      splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
    },
    series: [{
      type: 'line', data: ys, smooth: false, symbol: 'none',
      lineStyle: { color: lineColor, width: 2 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, areaColor),
      },
    }],
  }
}

async function initLineChart() {
  if (!lineRef.value) return
  echarts = await loadEcharts()
  if (disposed || !lineRef.value) return
  lineChart = echarts.init(lineRef.value, null, { renderer: 'canvas' })
  lineChart.setOption(buildLineOption(data.equityCurve))
}

watch(() => data.equityCurve, (curve) => {
  if (!lineChart) return
  lineChart.setOption(buildLineOption(curve), { notMerge: false })
}, { deep: true })

// ── ECharts 仓位分布 ──────────────────────────────────────────────────────────
const donutRef  = ref(null)
let donutChart  = null

function buildDonutOption(positions) {
  if (!data.positionsKnown || positions.some(p => p.notional == null) || !positions.length) {
    return {
      backgroundColor: 'transparent',
      title: {
        text: !data.positionsKnown ? '持仓快照未就绪' : positions.length ? '合约乘数或成本缺失' : '暂无持仓', left: 'center', top: 'middle',
        textStyle: { color: '#6e7681', fontSize: 14 },
      },
    }
  }

  const seriesData = positions.map(p => ({
    name:  `${p.symbol} ${p.direction === 'long' ? '多' : '空'}`,
    value: p.notional,
    itemStyle: {
      color: p.direction === 'long' ? '#3fb950' : '#f85149',
      borderColor: '#0d1117', borderWidth: 2,
    },
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#1a1f2e',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 12 },
      formatter: p => `${p.name}<br/><b>¥${(p.value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</b><br/>${p.percent.toFixed(1)}%`,
    },
    legend: {
      orient: 'vertical', right: '2%', top: 'center',
      textStyle: { color: '#8b949e', fontSize: 11 },
      icon: 'circle',
    },
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      center: ['38%', '50%'],
      avoidLabelOverlap: true,
      label: {
        show: true, position: 'outside',
        color: '#8b949e', fontSize: 11,
        formatter: '{b}\n{d}%',
      },
      labelLine: { lineStyle: { color: '#30363d' } },
      data: seriesData,
    }],
  }
}

async function initDonutChart() {
  if (!donutRef.value) return
  echarts = echarts || await loadEcharts()
  if (disposed || !donutRef.value) return
  donutChart = echarts.init(donutRef.value, null, { renderer: 'canvas' })
  donutChart.setOption(buildDonutOption(data.positions))
}

watch(() => data.positions, (pos) => {
  if (!donutChart) return
  donutChart.setOption(buildDonutOption(pos), { notMerge: true })
}, { deep: true })

// ── 图表响应式缩放 ────────────────────────────────────────────────────────────
function resizeCharts() {
  lineChart?.resize()
  donutChart?.resize()
}
const resizeObs = typeof ResizeObserver !== 'undefined'
  ? new ResizeObserver(() => resizeCharts())
  : null

onMounted(async () => {
  await nextTick()
  await Promise.all([initLineChart(), initDonutChart()])
  if (disposed) return
  if (resizeObs && lineRef.value)  resizeObs.observe(lineRef.value)
  if (resizeObs && donutRef.value) resizeObs.observe(donutRef.value)
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  disposed = true
  lineChart?.dispose()
  donutChart?.dispose()
  resizeObs?.disconnect()
  window.removeEventListener('resize', resizeCharts)
})

// ── 仓位方向标签 ─────────────────────────────────────────────────────────────
function dirLabel(dir) { return dir === 'long' ? '多' : '空' }
function dirType(dir)  { return dir === 'long' ? 'success' : 'danger' }
</script>

<template>
  <div class="gd-wrap">

    <!-- ── 顶部状态栏 ─────────────────────────────────────────────────────── -->
    <div class="gd-statusbar">
      <span class="sb-item">
        <span class="ws-dot" :class="connected && data.accountKnown ? 'ws-on' : 'ws-off'"></span>
        <span :class="connected && data.accountKnown ? 'c-green' : 'c-muted'">
          {{ connected ? (data.accountKnown ? '柜台账户回报' : '柜台数据未就绪') : '推送已断开' }}
        </span>
      </span>
      <span class="sb-item">账户 <b class="c-text">{{ data.accountId || '--' }}</b></span>
      <span class="sb-item c-muted">活跃策略 <b class="mono" style="color:var(--q-blue)">{{ data.activeStrategies }}</b></span>
      <span class="sb-sep"></span>
      <span class="sb-item c-muted">更新 {{ lastUpdate }}</span>
    </div>

    <div class="data-note">{{ data.performanceNote || '等待带来源信息的柜台快照；未知指标显示为 —' }}</div>

    <!-- ── KPI 卡片网格 ───────────────────────────────────────────────────── -->
    <div class="kpi-grid">

      <!-- 实时浮盈 -->
      <div class="kpi-card kpi-main">
        <div class="kpi-label">持仓盈亏 ¥</div>
        <div class="kpi-val mono" :class="flashCls" :style="{ color: pnlCss(displayPnl) }">
          {{ fmt.signed(displayPnl) }}
        </div>
        <div class="kpi-sub">账户余额 <span class="mono c-text">{{ fmt.money(data.balance, 0) }}</span></div>
      </div>

      <!-- 会话权益变动 -->
      <div class="kpi-card">
        <div class="kpi-label">会话权益变动</div>
        <div class="kpi-val mono" :style="{ color: pnlCss(data.sessionBalanceChangePct) }">
          {{ fmt.rate(data.sessionBalanceChangePct) }}
        </div>
        <div class="kpi-sub">可用 <span class="mono">{{ fmt.money(data.available, 0) }}</span></div>
      </div>

      <!-- 持仓 + 平仓盈亏 -->
      <div class="kpi-card">
        <div class="kpi-label">持仓 + 平仓盈亏</div>
        <div class="kpi-val mono" :style="{ color: pnlCss(data.totalPnl) }">
          {{ fmt.signed(data.totalPnl) }}
        </div>
        <div class="kpi-sub">未扣手续费 · 手续费 <span class="mono c-text">{{ fmt.money(data.commission) }}</span></div>
      </div>

      <!-- 夏普比率 -->
      <div class="kpi-card">
        <div class="kpi-label">夏普比率（日终）</div>
        <div class="kpi-val mono" :class="sharpeLevel(data.sharpeRatio).cls">
          {{ fmt.num(data.sharpeRatio) }}
        </div>
        <div class="kpi-sub">
          <el-tag :type="data.sharpeRatio >= 1 ? 'success' : data.sharpeRatio >= 0 ? 'warning' : 'danger'"
            size="small" effect="dark">
            {{ sharpeLevel(data.sharpeRatio).label }}
          </el-tag>
        </div>
      </div>

      <!-- 最大回撤 -->
      <div class="kpi-card">
        <div class="kpi-label">窗口权益回撤</div>
        <div class="kpi-val mono" :class="ddLevel(data.maxDrawdownPct).cls">
          {{ fmt.pct(data.maxDrawdownPct) }}
        </div>
        <div class="kpi-sub">
          <el-tag type="info"
            size="small" effect="dark">
            {{ ddLevel(data.maxDrawdownPct).label }}
          </el-tag>
        </div>
      </div>

      <!-- 仓位暴露 -->
      <div class="kpi-card">
        <div class="kpi-label">成本敞口 / 权益</div>
        <div class="kpi-val mono" >
          {{ fmt.pct(data.exposurePct) }}
        </div>
        <div class="kpi-sub">占用保证金 <span class="mono">{{ fmt.money(data.margin, 0) }}</span></div>
      </div>

    </div>

    <!-- ── 图表区域 ────────────────────────────────────────────────────────── -->
    <div class="charts-row">

      <!-- 权益曲线 -->
      <div class="chart-card chart-card--line">
        <div class="chart-title">
          <span>账户权益曲线（监控窗口）</span>
          <span class="chart-hint c-muted">柜台余额 · 含出入金</span>
        </div>
        <div ref="lineRef" class="chart-canvas"></div>
      </div>

      <!-- 仓位分布 -->
      <div class="chart-card chart-card--donut">
        <div class="chart-title">
          <span>仓位分布</span>
          <span class="chart-hint c-muted">按成本名义金额占比</span>
        </div>
        <div ref="donutRef" class="chart-canvas"></div>
      </div>

    </div>

    <!-- ── 仓位明细表 ─────────────────────────────────────────────────────── -->
    <div class="pos-table-wrap">
      <div class="chart-title" style="padding: 14px 20px 0">
        <span>持仓明细</span>
        <span class="chart-hint c-muted">{{ data.positions.length }} 个品种</span>
      </div>

      <el-table
        :data="data.positions"
        size="small"
        class="pos-table"
        :empty-text="data.positionsKnown ? '暂无持仓' : '持仓快照未就绪'"
        :row-class-name="r => r.row.direction === 'long' ? 'pos-row-long' : 'pos-row-short'"
      >
        <el-table-column prop="symbol"    label="品种"   width="110" />
        <el-table-column prop="direction" label="方向"   width="70">
          <template #default="{ row }">
            <el-tag :type="dirType(row.direction)" size="small" effect="dark">
              {{ dirLabel(row.direction) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="volume" label="手数" width="70" align="right" />
        <el-table-column prop="cost"   label="开仓均价" align="right">
          <template #default="{ row }">
            <span class="mono">{{ fmt.money(row.cost) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="pnl"    label="浮动盈亏" align="right">
          <template #default="{ row }">
            <span class="mono fw-600" :style="{ color: pnlCss(row.pnl) }">
              {{ fmt.signed(row.pnl) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="成本名义金额" align="right">
          <template #default="{ row }">
            <span class="mono c-muted">{{ fmt.money(row.notional, 0) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

  </div>
</template>

<style scoped>
.data-note {padding: 10px 20px; color: var(--q-muted); font-size: 12px; line-height: 1.6; border-bottom: 1px solid var(--q-border);}
/* ── 外层容器 ── */
.gd-wrap {
  background: var(--q-panel);
  border: 1px solid var(--q-border);
  border-radius: 10px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ── 状态栏 ── */
.gd-statusbar {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding: 9px 20px;
  border-bottom: 1px solid var(--q-border);
  background: rgba(0, 0, 0, 0.2);
  font-size: 12px;
}
.sb-item   { display: flex; align-items: center; gap: 5px; }
.sb-sep    { flex: 1; }
.ws-dot    { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.ws-on     { background: var(--q-green); box-shadow: 0 0 5px var(--q-green); animation: pulse 2s infinite; }
.ws-off    { background: var(--q-muted); }
@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .45 } }

/* ── KPI 网格 ── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 1px;
  background: var(--q-border);
}
@media (max-width: 1100px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 680px)  { .kpi-grid { grid-template-columns: repeat(2, 1fr); } }

.kpi-card {
  background: var(--q-panel);
  padding: 18px 20px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kpi-main { padding: 22px 24px 16px; }

.kpi-label {
  font-size: 10px;
  color: var(--q-muted);
  letter-spacing: .6px;
  text-transform: uppercase;
}
.kpi-val {
  font-size: 26px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -.5px;
  transition: color .3s;
}
.kpi-main .kpi-val { font-size: 34px; }

.kpi-sub  { font-size: 11px; color: var(--q-muted); }
.kpi-bar  { margin-top: 4px; }

/* ── 健康等级颜色 ── */
.level-excellent { color: var(--q-green);  }
.level-good      { color: #58a6ff;         }
.level-fair      { color: var(--q-yellow); }
.level-poor      { color: var(--q-red);    }

/* ── 图表行 ── */
.charts-row {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 1px;
  background: var(--q-border);
}
@media (max-width: 800px) { .charts-row { grid-template-columns: 1fr; } }

.chart-card {
  background: var(--q-panel);
  display: flex;
  flex-direction: column;
  padding: 0 0 8px;
}

.chart-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--q-text);
}
.chart-hint { font-weight: 400; font-size: 11px; }

.chart-canvas {
  flex: 1;
  min-height: 200px;
}
.chart-card--line  .chart-canvas { min-height: 220px; }
.chart-card--donut .chart-canvas { min-height: 200px; }

/* ── 仓位表 ── */
.pos-table-wrap {
  border-top: 1px solid var(--q-border);
}
.pos-table { width: 100%; }

:deep(.pos-row-long td)  { border-left: 3px solid var(--q-green) !important; }
:deep(.pos-row-short td:first-child) { border-left: 3px solid var(--q-red) !important; }

/* ── 工具类 ── */
.mono   { font-family: var(--q-font-mono); font-variant-numeric: tabular-nums; }
.c-green { color: var(--q-green); }
.c-muted { color: var(--q-muted); }
.c-text  { color: var(--q-text);  }
.fw-600  { font-weight: 600; }

/* ── P&L 闪烁 ── */
@keyframes flash-up   { 0%{text-shadow:none} 30%{text-shadow:0 0 14px var(--q-green),0 0 28px var(--q-green)} 100%{text-shadow:none} }
@keyframes flash-down { 0%{text-shadow:none} 30%{text-shadow:0 0 14px var(--q-red),0 0 28px var(--q-red)}   100%{text-shadow:none} }
.flash-up   { animation: flash-up   .7s ease; }
.flash-down { animation: flash-down .7s ease; }
</style>
