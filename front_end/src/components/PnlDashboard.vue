<script setup>
/**
 * PnlDashboard.vue — 实时盈亏仪表盘
 *
 * Props:
 *   wsUrl: String — WebSocket 地址，默认指向后端 /ws/system
 *
 * 功能：
 *  - 通过 useSystemWs 可组合函数连接 WebSocket
 *  - 数字变化时触发 requestAnimationFrame 平滑动画
 *  - 盈亏正负自动着色；上涨/下跌时短暂闪烁
 *  - CPU / 内存进度条随数值着色
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { useSystemWs } from '@/composables/useSystemWs.js'
import { buildWsUrl } from '@/config/network.js'

// ── Props ─────────────────────────────────────────────────────────────────
const props = defineProps({
  wsUrl: {
    type: String,
    default: () => buildWsUrl('/ws/system'),
  },
})

// ── WebSocket 数据 ────────────────────────────────────────────────────────
const { connected, data } = useSystemWs(props.wsUrl)

// ── 数字动画（P&L 主数字）────────────────────────────────────────────────
const displayPnl = ref(0)
let rafId = null

function animateTo(target) {
  if (rafId) cancelAnimationFrame(rafId)
  const start     = displayPnl.value
  const diff      = target - start
  const duration  = 450                // ms
  const startTime = performance.now()

  function step(now) {
    const elapsed  = Math.min(now - startTime, duration)
    const t        = elapsed / duration
    const ease     = 1 - (1 - t) ** 3  // cubic ease-out
    displayPnl.value = start + diff * ease
    if (elapsed < duration) rafId = requestAnimationFrame(step)
    else displayPnl.value = target
  }
  rafId = requestAnimationFrame(step)
}
onUnmounted(() => { if (rafId) cancelAnimationFrame(rafId) })

// ── 闪烁动画 ─────────────────────────────────────────────────────────────
const flashClass = ref('')  // '' | 'flash-up' | 'flash-down'
let flashTimer = null

watch(() => data.totalPnl, (newVal, oldVal) => {
  animateTo(newVal)
  if (flashTimer) clearTimeout(flashTimer)
  flashClass.value = newVal > oldVal ? 'flash-up' : newVal < oldVal ? 'flash-down' : ''
  flashTimer = setTimeout(() => { flashClass.value = '' }, 700)
})

// ── 格式化工具 ────────────────────────────────────────────────────────────
function fmtMoney(val, decimals = 2) {
  const n = Number(val) || 0
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function fmtPnlSign(val) {
  const n = Number(val) || 0
  return (n >= 0 ? '+' : '') + fmtMoney(n)
}

function fmtRate(val) {
  const n = Number(val) || 0
  return (n >= 0 ? '+' : '') + n.toFixed(4) + '%'
}

// ── 颜色计算 ──────────────────────────────────────────────────────────────
const pnlColor = computed(() => {
  const n = Number(data.totalPnl) || 0
  return n > 0 ? 'var(--q-green)' : n < 0 ? 'var(--q-red)' : 'var(--q-muted)'
})

const rateColor = computed(() => {
  const n = Number(data.returnRate) || 0
  return n > 0 ? 'var(--q-green)' : n < 0 ? 'var(--q-red)' : 'var(--q-muted)'
})

function cpuColor(v)  {
  if (v > 80) return 'var(--q-red)'
  if (v > 60) return 'var(--q-yellow)'
  return 'var(--q-blue)'
}
function memColor(v) {
  if (v > 85) return 'var(--q-red)'
  if (v > 70) return 'var(--q-yellow)'
  return 'var(--q-blue)'
}

// 格式化时间戳
const lastUpdate = computed(() => {
  if (!data.timestamp) return '--'
  try { return new Date(data.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) }
  catch { return data.timestamp }
})
</script>

<template>
  <div class="pnl-dashboard">

    <!-- ── 顶部状态栏 ───────────────────────────────────────────────── -->
    <div class="status-bar">
      <span class="status-item">
        <span class="dot" :class="connected ? 'dot-on' : 'dot-off'"></span>
        <span :class="connected ? 'c-green' : 'c-muted'">
          {{ connected ? 'WebSocket 已连接' : '正在连接…' }}
        </span>
      </span>
      <span class="status-item">
        网关：
        <span class="c-text fw-600">{{ data.gatewayName }}</span>
      </span>
      <span class="status-item">
        行情：
        <el-tag
          :type="data.marketConnected ? 'success' : 'danger'"
          size="small"
          effect="dark"
        >
          {{ data.marketConnected ? '已接入' : '断开' }}
        </el-tag>
      </span>
      <span class="status-item c-muted">
        更新：{{ lastUpdate }}
      </span>
    </div>

    <!-- ── 主要指标网格 ──────────────────────────────────────────────── -->
    <div class="stat-grid">

      <!-- 总盈亏（主角） -->
      <div class="stat-card stat-card--large">
        <div class="stat-label">总浮动盈亏（¥）</div>
        <div
          class="stat-value stat-value--xl mono"
          :class="flashClass"
          :style="{ color: pnlColor }"
        >
          {{ fmtPnlSign(displayPnl) }}
        </div>
        <div class="stat-sub">
          账户余额 <span class="mono c-text">{{ fmtMoney(data.balance, 0) }}</span>
        </div>
      </div>

      <!-- 收益率 -->
      <div class="stat-card stat-card--large">
        <div class="stat-label">收益率</div>
        <div
          class="stat-value stat-value--xl mono"
          :style="{ color: rateColor }"
        >
          {{ fmtRate(data.returnRate) }}
        </div>
        <div class="stat-sub">
          活跃策略
          <span class="mono" style="color: var(--q-blue)">
            {{ data.activeStrategies }}
          </span> 个
        </div>
      </div>

      <!-- CPU 使用率 -->
      <div class="stat-card">
        <div class="stat-label">CPU 使用率</div>
        <div class="stat-value mono" :style="{ color: cpuColor(data.cpuPercent) }">
          {{ data.cpuPercent.toFixed(1) }}%
        </div>
        <el-progress
          :percentage="data.cpuPercent"
          :color="cpuColor(data.cpuPercent)"
          :stroke-width="6"
          :show-text="false"
          class="stat-bar"
        />
      </div>

      <!-- 内存使用率 -->
      <div class="stat-card">
        <div class="stat-label">内存使用率</div>
        <div class="stat-value mono" :style="{ color: memColor(data.memoryPercent) }">
          {{ data.memoryPercent.toFixed(1) }}%
        </div>
        <el-progress
          :percentage="data.memoryPercent"
          :color="memColor(data.memoryPercent)"
          :stroke-width="6"
          :show-text="false"
          class="stat-bar"
        />
      </div>

    </div>
    <!-- /stat-grid -->

  </div>
</template>

<style scoped>
/* ── 容器 ── */
.pnl-dashboard {
  background: var(--q-panel);
  border: 1px solid var(--q-border);
  border-radius: 10px;
  overflow: hidden;
}

/* ── 状态栏 ── */
.status-bar {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  padding: 10px 20px;
  border-bottom: 1px solid var(--q-border);
  background: rgba(0, 0, 0, 0.25);
  font-size: 12px;
}
.status-item { display: flex; align-items: center; gap: 6px; }

.dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-on  { background: var(--q-green); box-shadow: 0 0 6px var(--q-green); animation: pulse 2s infinite; }
.dot-off { background: var(--q-muted); }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.5; }
}

/* ── 指标网格 ── */
.stat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  gap: 1px;
  background: var(--q-border);
}
@media (max-width: 900px) {
  .stat-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 500px) {
  .stat-grid { grid-template-columns: 1fr; }
}

/* ── 单张卡片 ── */
.stat-card {
  background: var(--q-panel);
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.stat-card--large {
  padding: 24px 28px;
}

.stat-label {
  font-size: 11px;
  color: var(--q-muted);
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.5px;
  transition: color 0.3s;
}
.stat-value--xl {
  font-size: 38px;
}

.stat-sub {
  font-size: 12px;
  color: var(--q-muted);
  margin-top: 4px;
}

.stat-bar {
  margin-top: 4px;
}

/* ── 工具类 ── */
.mono   { font-family: var(--q-font-mono); font-variant-numeric: tabular-nums; }
.c-green { color: var(--q-green); }
.c-muted { color: var(--q-muted); }
.c-text  { color: var(--q-text);  }
.fw-600  { font-weight: 600; }

/* ── P&L 闪烁动画 ── */
@keyframes flash-up {
  0%   { text-shadow: none; }
  30%  { text-shadow: 0 0 12px var(--q-green), 0 0 24px var(--q-green); }
  100% { text-shadow: none; }
}
@keyframes flash-down {
  0%   { text-shadow: none; }
  30%  { text-shadow: 0 0 12px var(--q-red), 0 0 24px var(--q-red); }
  100% { text-shadow: none; }
}

.flash-up   { animation: flash-up   0.7s ease; }
.flash-down { animation: flash-down 0.7s ease; }
</style>
