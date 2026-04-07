<template>
  <div class="sys-monitor">

    <!-- ── 连接状态条 ──────────────────────────────────────────────── -->
    <div class="status-bar">
      <div class="status-item">
        <span :class="['conn-dot', data.tdConnected ? 'conn-ok' : 'conn-off']"></span>
        <span class="status-label">交易通道 (TD)</span>
        <el-tag
          :type="data.tdConnected ? 'success' : 'danger'"
          size="small" effect="dark"
        >{{ tdStatusText }}</el-tag>
      </div>

      <div class="status-item">
        <span :class="['conn-dot', data.mdConnected ? 'conn-ok' : 'conn-off']"></span>
        <span class="status-label">行情通道 (MD)</span>
        <el-tag
          :type="data.mdConnected ? 'success' : 'danger'"
          size="small" effect="dark"
        >{{ data.mdConnected ? '已连接' : '断开' }}</el-tag>
      </div>

      <div class="status-item">
        <el-icon class="status-icon"><Timer /></el-icon>
        <span class="status-label">网关延迟</span>
        <span :class="['latency-val', latencyClass]">{{ latencyText }}</span>
      </div>

      <div class="status-item">
        <el-icon class="status-icon"><Connection /></el-icon>
        <span class="status-label">网关</span>
        <span class="gw-name">{{ data.gatewayName }}</span>
      </div>

      <div class="status-item ms-auto">
        <span :class="['ws-dot', wsConnected ? 'conn-ok' : 'conn-off']"></span>
        <span class="status-label c-muted">WS {{ wsConnected ? '实时' : '已断开' }}</span>
        <span class="status-label c-muted" v-if="data.timestamp">
          {{ new Date(data.timestamp).toLocaleTimeString('zh-CN') }}
        </span>
      </div>
    </div>

    <!-- ── 资源 KPI 行 ──────────────────────────────────────────────── -->
    <div class="kpi-row">
      <div class="kpi-block">
        <div class="kpi-label">CPU 使用率</div>
        <div class="kpi-value">{{ data.cpuPercent.toFixed(1) }}%</div>
        <el-progress
          :percentage="data.cpuPercent"
          :status="data.cpuPercent > 85 ? 'exception' : data.cpuPercent > 65 ? 'warning' : ''"
          :stroke-width="6"
          :show-text="false"
          class="kpi-bar"
        />
      </div>

      <div class="kpi-block">
        <div class="kpi-label">内存使用率</div>
        <div class="kpi-value">{{ data.memoryPercent.toFixed(1) }}%</div>
        <el-progress
          :percentage="data.memoryPercent"
          :status="data.memoryPercent > 90 ? 'exception' : data.memoryPercent > 75 ? 'warning' : ''"
          :stroke-width="6"
          :show-text="false"
          class="kpi-bar"
        />
      </div>

      <div class="kpi-block">
        <div class="kpi-label">网络上行</div>
        <div class="kpi-value net-up">{{ formatBytes(data.networkSendBps) }}/s</div>
        <div class="kpi-sub c-muted">↑ 发送</div>
      </div>

      <div class="kpi-block">
        <div class="kpi-label">网络下行</div>
        <div class="kpi-value net-down">{{ formatBytes(data.networkRecvBps) }}/s</div>
        <div class="kpi-sub c-muted">↓ 接收</div>
      </div>

      <div class="kpi-block">
        <div class="kpi-label">活跃策略</div>
        <div class="kpi-value">{{ data.activeStrategies }}</div>
        <div class="kpi-sub c-muted">个策略运行中</div>
      </div>
    </div>

    <!-- ── ECharts 双联图 ───────────────────────────────────────────── -->
    <div class="charts-row">
      <div class="chart-wrap">
        <div class="chart-label">CPU & 内存历史（最近 60s）</div>
        <div ref="cpuChartRef" class="chart-body"></div>
      </div>

      <div class="chart-wrap">
        <div class="chart-label">网络吞吐历史（最近 60s）</div>
        <div ref="netChartRef" class="chart-body"></div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { Timer, Connection } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { useSystemWs } from '@/composables/useSystemWs.js'

// ── WebSocket 数据 ─────────────────────────────────────────────────────
const WS_URL = (() => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const host  = import.meta.env.VITE_WS_HOST ?? location.host
  return `${proto}://${host}/ws/system`
})()

const { connected: wsConnected, data } = useSystemWs(WS_URL)

// ── 60s 滚动历史 ──────────────────────────────────────────────────────
const MAX_HISTORY = 60
const history = {
  labels:  Array.from({ length: MAX_HISTORY }, (_, i) => `-${MAX_HISTORY - i}s`),
  cpu:     new Array(MAX_HISTORY).fill(0),
  mem:     new Array(MAX_HISTORY).fill(0),
  sendBps: new Array(MAX_HISTORY).fill(0),
  recvBps: new Array(MAX_HISTORY).fill(0),
}

function pushHistory(cpu, mem, send, recv) {
  history.cpu.push(cpu);     history.cpu.shift()
  history.mem.push(mem);     history.mem.shift()
  history.sendBps.push(send); history.sendBps.shift()
  history.recvBps.push(recv); history.recvBps.shift()
}

// ── 计算属性 ─────────────────────────────────────────────────────────
const tdStatusText = computed(() => {
  const s = data.gatewayStatus
  if (s === 'trading')    return '交易中'
  if (s === 'connected')  return '已连接'
  if (s === 'connecting') return '连接中'
  return '未连接'
})

const latencyText = computed(() => {
  const ms = data.gatewayLatencyMs
  if (ms < 0)     return '—'
  if (ms < 1000)  return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
})

const latencyClass = computed(() => {
  const ms = data.gatewayLatencyMs
  if (ms < 0)    return 'lat-unknown'
  if (ms < 50)   return 'lat-good'
  if (ms < 200)  return 'lat-warn'
  return 'lat-bad'
})

function formatBytes(bps) {
  if (bps >= 1_048_576) return (bps / 1_048_576).toFixed(1) + ' MB'
  if (bps >= 1_024)     return (bps / 1_024).toFixed(1) + ' KB'
  return bps.toFixed(0) + ' B'
}

// ── ECharts ───────────────────────────────────────────────────────────
const cpuChartRef = ref(null)
const netChartRef = ref(null)
let cpuChart, netChart

const AXIS_STYLE = {
  axisLabel:  { color: '#6e7681', fontSize: 11 },
  axisLine:   { lineStyle: { color: '#30363d' } },
  splitLine:  { lineStyle: { color: '#21262d', type: 'dashed' } },
}

function buildCpuOption() {
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 10, right: 12, bottom: 28, left: 44 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1a1f2e',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 11 },
    },
    legend: {
      bottom: 0, textStyle: { color: '#8b949e', fontSize: 10 },
      itemStyle: { borderWidth: 0 },
    },
    xAxis: { type: 'category', data: history.labels, boundaryGap: false, ...AXIS_STYLE, splitLine: { show: false } },
    yAxis: { type: 'value', min: 0, max: 100, ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: v => v + '%' } },
    series: [
      {
        name: 'CPU', type: 'line', data: [...history.cpu],
        smooth: true, symbol: 'none',
        lineStyle: { color: '#58a6ff', width: 1.5 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(88,166,255,0.3)' },
          { offset: 1, color: 'rgba(88,166,255,0)' },
        ]) },
      },
      {
        name: '内存', type: 'line', data: [...history.mem],
        smooth: true, symbol: 'none',
        lineStyle: { color: '#e3b341', width: 1.5 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(227,179,65,0.2)' },
          { offset: 1, color: 'rgba(227,179,65,0)' },
        ]) },
      },
    ],
  }
}

function buildNetOption() {
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 10, right: 12, bottom: 28, left: 56 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1a1f2e',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 11 },
      formatter(params) {
        return params.map(p =>
          `${p.marker}${p.seriesName}: ${formatBytes(p.value)}/s`
        ).join('<br/>')
      },
    },
    legend: {
      bottom: 0, textStyle: { color: '#8b949e', fontSize: 10 },
      itemStyle: { borderWidth: 0 },
    },
    xAxis: { type: 'category', data: history.labels, boundaryGap: false, ...AXIS_STYLE, splitLine: { show: false } },
    yAxis: {
      type: 'value', min: 0, ...AXIS_STYLE,
      axisLabel: { ...AXIS_STYLE.axisLabel, formatter: v => formatBytes(v) },
    },
    series: [
      {
        name: '上行', type: 'line', data: [...history.sendBps],
        smooth: true, symbol: 'none',
        lineStyle: { color: '#3fb950', width: 1.5 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(63,185,80,0.25)' },
          { offset: 1, color: 'rgba(63,185,80,0)' },
        ]) },
      },
      {
        name: '下行', type: 'line', data: [...history.recvBps],
        smooth: true, symbol: 'none',
        lineStyle: { color: '#f85149', width: 1.5 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(248,81,73,0.2)' },
          { offset: 1, color: 'rgba(248,81,73,0)' },
        ]) },
      },
    ],
  }
}

function updateCharts() {
  cpuChart?.setOption({
    series: [
      { data: [...history.cpu] },
      { data: [...history.mem] },
    ],
  })
  netChart?.setOption({
    series: [
      { data: [...history.sendBps] },
      { data: [...history.recvBps] },
    ],
  })
}

// ── 监听 WS 数据更新历史并刷新图表 ──────────────────────────────────
watch(() => data.timestamp, () => {
  pushHistory(data.cpuPercent, data.memoryPercent, data.networkSendBps, data.networkRecvBps)
  updateCharts()
})

// ── resize ────────────────────────────────────────────────────────────
function handleResize() {
  cpuChart?.resize()
  netChart?.resize()
}

// ── 生命周期 ─────────────────────────────────────────────────────────
onMounted(() => {
  window.addEventListener('resize', handleResize)
  if (cpuChartRef.value) {
    cpuChart = echarts.init(cpuChartRef.value, 'dark')
    cpuChart.setOption(buildCpuOption())
  }
  if (netChartRef.value) {
    netChart = echarts.init(netChartRef.value, 'dark')
    netChart.setOption(buildNetOption())
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  cpuChart?.dispose()
  netChart?.dispose()
})
</script>

<style scoped>
.sys-monitor { display: flex; flex-direction: column; gap: 16px; }

/* ── 状态条 ────────────────────────────────────────────────────────── */
.status-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 20px;
  background: var(--bg-base, #0d1117);
  border: 1px solid var(--border-color, #30363d);
  border-radius: 8px;
  padding: 12px 18px;
}

.status-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.ms-auto { margin-left: auto; }

.conn-dot, .ws-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.conn-ok  { background: #3fb950; box-shadow: 0 0 6px #3fb95066; }
.conn-off { background: #f85149; box-shadow: 0 0 6px #f8514966; }

.status-label { color: var(--text-muted, #6e7681); }
.status-icon  { color: var(--text-muted, #6e7681); font-size: 14px; }
.gw-name      { color: var(--text-primary, #c9d1d9); font-weight: 600; }

.latency-val  { font-weight: 700; font-variant-numeric: tabular-nums; }
.lat-unknown  { color: var(--text-muted, #6e7681); }
.lat-good     { color: #3fb950; }
.lat-warn     { color: #e3b341; }
.lat-bad      { color: #f85149; }

/* ── KPI 行 ──────────────────────────────────────────────────────── */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}

.kpi-block {
  background: var(--bg-base, #0d1117);
  border: 1px solid var(--border-color, #30363d);
  border-radius: 8px;
  padding: 12px 16px;
}
.kpi-label { font-size: 11px; color: var(--text-muted, #6e7681); margin-bottom: 4px; }
.kpi-value {
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary, #c9d1d9);
  margin-bottom: 6px;
}
.kpi-sub  { font-size: 11px; }
.kpi-bar  { margin-top: 2px; }
.net-up   { color: #3fb950; }
.net-down { color: #f85149; }

/* ── 双联图 ──────────────────────────────────────────────────────── */
.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.chart-wrap {
  background: var(--bg-base, #0d1117);
  border: 1px solid var(--border-color, #30363d);
  border-radius: 8px;
  padding: 12px;
}

.chart-label {
  font-size: 12px;
  color: var(--text-muted, #6e7681);
  margin-bottom: 8px;
}

.chart-body { height: 200px; }

/* ── Element Plus 覆盖 ──────────────────────────────────────────── */
:deep(.el-progress-bar__outer) { background-color: #21262d; }

@media (max-width: 800px) {
  .charts-row { grid-template-columns: 1fr; }
  .kpi-row    { grid-template-columns: repeat(2, 1fr); }
}
</style>
