<template>
  <div class="wrp">

    <!-- ── 合约信息头 ──────────────────────────────────────────────────── -->
    <div class="section contract-header" v-if="contract">
      <div class="ch-main">
        <span class="ch-symbol">{{ contract.symbol }}</span>
        <el-tag size="small" :type="exchTagType" class="ch-exch">{{ contract.exchange }}</el-tag>
      </div>
      <span class="ch-name">{{ contract.name }}</span>
      <div v-if="tick.last > 0" class="ch-price" :class="priceDir">
        <span class="price-main">{{ tick.last }}</span>
        <span class="price-chg">
          {{ tick.change >= 0 ? '+' : '' }}{{ tick.change?.toFixed(2) }}
          &nbsp;({{ tick.changeRate >= 0 ? '+' : '' }}{{ tick.changeRate?.toFixed(2) }}%)
        </span>
      </div>
      <div v-else class="ch-price-empty">暂无行情</div>
    </div>
    <div v-else class="section empty-section">
      <el-icon><TrendCharts /></el-icon>
      <span>请选择合约</span>
    </div>

    <!-- ── 价格统计 ────────────────────────────────────────────────────── -->
    <div class="section" v-if="tick.last > 0">
      <div class="section-title">今日统计</div>
      <div class="stats-grid">
        <div class="stat-item">
          <span class="stat-label">今开</span>
          <span class="stat-val">{{ tick.open || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">昨收</span>
          <span class="stat-val">{{ tick.preClose || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">最高</span>
          <span class="stat-val up">{{ tick.high || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">最低</span>
          <span class="stat-val down">{{ tick.low || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">成交量</span>
          <span class="stat-val">{{ fmtVol(tick.volume) }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">持仓量</span>
          <span class="stat-val">{{ fmtVol(tick.openInterest) }}</span>
        </div>
        <div class="stat-item full">
          <span class="stat-label">成交额</span>
          <span class="stat-val">{{ fmtTurnover(tick.turnover) }}</span>
        </div>
      </div>
    </div>

    <!-- ── 5 档深度行情 ────────────────────────────────────────────────── -->
    <div class="section depth-section" v-if="tick.last > 0">
      <div class="section-title">深度行情</div>

      <!-- 卖盘（从高到低） -->
      <div class="depth-side ask-side">
        <div
          v-for="(level, i) in askLevels"
          :key="`ask${i}`"
          class="depth-row ask"
        >
          <span class="depth-label">卖{{ i + 1 }}</span>
          <span class="depth-price down">{{ level.price || '--' }}</span>
          <span class="depth-vol">{{ level.vol != null ? fmtVol(level.vol) : '--' }}</span>
          <div
            class="depth-bar ask-bar"
            :style="{ width: level.barPct + '%' }"
          />
        </div>
      </div>

      <!-- 中间：最新价 -->
      <div class="depth-mid">
        <span :class="priceDir" class="depth-last">{{ tick.last }}</span>
        <span class="depth-spread" v-if="spreadPct">价差 {{ spreadPct }}</span>
      </div>

      <!-- 买盘（从高到低） -->
      <div class="depth-side bid-side">
        <div
          v-for="(level, i) in bidLevels"
          :key="`bid${i}`"
          class="depth-row bid"
        >
          <span class="depth-label">买{{ i + 1 }}</span>
          <span class="depth-price up">{{ level.price || '--' }}</span>
          <span class="depth-vol">{{ level.vol != null ? fmtVol(level.vol) : '--' }}</span>
          <div
            class="depth-bar bid-bar"
            :style="{ width: level.barPct + '%' }"
          />
        </div>
      </div>
    </div>

    <!-- ── 实时告警 ────────────────────────────────────────────────────── -->
    <div class="section alerts-section">
      <div class="section-title">
        <span>告警</span>
        <span v-if="symbolAlerts.length" class="alert-count">{{ symbolAlerts.length }}</span>
        <el-button v-if="symbolAlerts.length" text size="small"
          @click="watchWs.clearAlerts()">清空</el-button>
      </div>
      <div v-if="!symbolAlerts.length" class="empty-hint-sm">暂无告警</div>
      <div
        v-for="a in symbolAlerts"
        :key="a.id"
        class="alert-row"
        :class="a.level"
      >
        <span class="alert-time">{{ a.time }}</span>
        <span class="alert-msg">{{ a.message }}</span>
      </div>
    </div>

  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { TrendCharts } from '@element-plus/icons-vue'

// ── Props ─────────────────────────────────────────────────────────────────
const props = defineProps({
  contract: { type: Object, default: null },
})

// ── WS（从父组件注入） ─────────────────────────────────────────────────────
const watchWs = inject('watchWs')

// ── Tick 数据 ─────────────────────────────────────────────────────────────
const tick = computed(() =>
  props.contract ? watchWs?.getTick(props.contract.symbol) ?? { last: 0 } : { last: 0 }
)

// ── 价格方向 ──────────────────────────────────────────────────────────────
const priceDir = computed(() => {
  const r = tick.value.changeRate ?? 0
  return r > 0 ? 'up' : r < 0 ? 'down' : ''
})

// ── 交易所标签类型 ────────────────────────────────────────────────────────
const EXCH_TAG_MAP = { SHFE: 'danger', DCE: 'warning', CZCE: 'success', CFFEX: 'primary', INE: 'info', GFEX: 'info' }
const exchTagType = computed(() => EXCH_TAG_MAP[props.contract?.exchange] ?? 'info')

// ── 5 档深度计算 ──────────────────────────────────────────────────────────
function buildLevels(priceKeys, volKeys) {
  const t = tick.value
  const levels = priceKeys.map((pk, i) => ({
    price: t[pk] ?? null,
    vol:   t[volKeys[i]] ?? null,
  }))

  // 计算最大量用于 bar 宽度
  const maxVol = Math.max(...levels.map(l => l.vol ?? 0), 1)
  return levels.map(l => ({
    ...l,
    barPct: l.vol != null ? Math.round((l.vol / maxVol) * 100) : 0,
  }))
}

const askLevels = computed(() =>
  // 卖盘：price 从高到低展示（卖5→卖1）
  buildLevels(
    ['ask5','ask4','ask3','ask2','ask1'],
    ['ask5Vol','ask4Vol','ask3Vol','ask2Vol','ask1Vol'],
  )
)

const bidLevels = computed(() =>
  buildLevels(
    ['bid1','bid2','bid3','bid4','bid5'],
    ['bid1Vol','bid2Vol','bid3Vol','bid4Vol','bid5Vol'],
  )
)

const spreadPct = computed(() => {
  const a = tick.value.ask1
  const b = tick.value.bid1
  if (!a || !b || b <= 0) return ''
  const sp = ((a - b) / b * 100).toFixed(3)
  return `${sp}%`
})

// ── 当前品种的告警 ────────────────────────────────────────────────────────
const symbolAlerts = computed(() => {
  if (!props.contract || !watchWs) return []
  return (watchWs.alerts ?? [])
    .filter(a => a.symbol === props.contract.symbol)
    .slice(0, 20)
})

// ── 格式化函数 ────────────────────────────────────────────────────────────
function fmtVol(v) {
  if (v == null || v === 0) return '--'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return String(v)
}

function fmtTurnover(v) {
  if (v == null || v === 0) return '--'
  if (v >= 1e12) return (v / 1e12).toFixed(2) + '万亿'
  if (v >= 1e8)  return (v / 1e8).toFixed(2)  + '亿'
  if (v >= 1e4)  return (v / 1e4).toFixed(2)  + '万'
  return String(v)
}
</script>

<style scoped>
.wrp {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  background: #0d0d1a;
  border-left: 1px solid #1e1e2e;
  scrollbar-width: thin;
  scrollbar-color: #2a2a3e transparent;
}

.wrp::-webkit-scrollbar { width: 4px; }
.wrp::-webkit-scrollbar-thumb { background: #2a2a3e; border-radius: 2px; }

/* ── 通用区块 ────────────────────────────────────────────────────────────── */
.section {
  padding: 12px 14px;
  border-bottom: 1px solid #1a1a2a;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
}

.alert-count {
  background: #ef4444;
  color: #fff;
  border-radius: 8px;
  padding: 0 5px;
  font-size: 10px;
  line-height: 16px;
}

/* ── 合约信息头 ──────────────────────────────────────────────────────────── */
.contract-header { background: #111120; }

.ch-main {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
}

.ch-symbol {
  font-size: 18px;
  font-weight: 700;
  color: #e2e2e2;
}

.ch-name {
  font-size: 12px;
  color: #666;
  margin-bottom: 8px;
  display: block;
}

.ch-price {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.price-main {
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

.price-chg {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.ch-price.up .price-main, .ch-price.up .price-chg { color: #ef4444; }
.ch-price.down .price-main, .ch-price.down .price-chg { color: #22c55e; }

.ch-price-empty, .empty-section {
  color: #444;
  font-size: 12px;
  text-align: center;
  padding: 8px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.empty-section { padding: 32px 0; }
.empty-section .el-icon { font-size: 26px; color: #333; }

/* ── 价格统计 ────────────────────────────────────────────────────────────── */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 8px;
}

.stat-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stat-item.full { grid-column: span 2; }

.stat-label {
  font-size: 11px;
  color: #555;
}

.stat-val {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: #ccc;
}

.stat-val.up   { color: #ef4444; }
.stat-val.down { color: #22c55e; }

/* ── 深度行情 ────────────────────────────────────────────────────────────── */
.depth-section { padding: 12px 14px 8px; }

.depth-side { display: flex; flex-direction: column; gap: 2px; }

.depth-row {
  display: grid;
  grid-template-columns: 28px 1fr 1fr;
  align-items: center;
  gap: 4px;
  padding: 3px 0;
  position: relative;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.depth-label {
  font-size: 10px;
  color: #444;
}

.depth-price { font-weight: 600; }
.depth-price.up   { color: #ef4444; }
.depth-price.down { color: #22c55e; }

.depth-vol {
  text-align: right;
  color: #777;
  font-size: 11px;
}

.depth-bar {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  opacity: 0.1;
  border-radius: 1px;
  transition: width 0.3s ease;
  pointer-events: none;
}

.bid-bar { background: #ef4444; }
.ask-bar { background: #22c55e; }

/* 卖盘从低到高显示 */
.ask-side { flex-direction: column-reverse; }

.depth-mid {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 0;
  border-top: 1px solid #1a1a2a;
  border-bottom: 1px solid #1a1a2a;
  margin: 4px 0;
}

.depth-last {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.depth-last.up   { color: #ef4444; }
.depth-last.down { color: #22c55e; }

.depth-spread {
  font-size: 10px;
  color: #555;
}

/* ── 告警 ───────────────────────────────────────────────────────────────── */
.alerts-section { flex: 1; }

.empty-hint-sm {
  color: #444;
  font-size: 12px;
  padding: 10px 0;
  text-align: center;
}

.alert-row {
  display: flex;
  gap: 6px;
  padding: 5px 0;
  border-bottom: 1px solid #131320;
  font-size: 11px;
  line-height: 1.5;
}

.alert-time {
  flex-shrink: 0;
  color: #444;
  font-variant-numeric: tabular-nums;
}

.alert-msg { color: #888; word-break: break-all; }
.alert-row.warning .alert-msg { color: #f59e0b; }
.alert-row.danger  .alert-msg { color: #ef4444; }
</style>
