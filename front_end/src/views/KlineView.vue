<template>
  <div class="kline-page">
    <!-- ── 顶部导航栏 ──────────────────────────────────────────────────── -->
    <header class="kv-topbar">
      <div class="kv-topbar-left">
        <el-button text @click="router.push('/')">
          <el-icon><ArrowLeft /></el-icon> 返回监控台
        </el-button>
        <span class="kv-title">K 线行情</span>

        <!-- 当前 Tick 实时报价 -->
        <div v-if="curTick.last > 0" class="tick-strip">
          <span class="tick-last" :class="priceDir">{{ curTick.last }}</span>
          <span class="tick-chg" :class="priceDir">
            {{ curTick.change >= 0 ? '+' : '' }}{{ curTick.change?.toFixed(2) }}
            ({{ curTick.changeRate >= 0 ? '+' : '' }}{{ curTick.changeRate?.toFixed(2) }}%)
          </span>
          <span class="tick-meta">
            量 {{ curTick.volume }}
            &nbsp;买 {{ curTick.bid1 }} / 卖 {{ curTick.ask1 }}
          </span>
        </div>
      </div>

      <div class="kv-topbar-right">
        <!-- 连接状态指示 -->
        <el-tooltip :content="wsConnected ? '行情已连接' : (wsConnecting ? '连接中…' : '未连接')" placement="bottom">
          <span class="ws-dot" :class="wsConnected ? 'on' : wsConnecting ? 'blink' : 'off'" />
        </el-tooltip>

        <!-- 告警铃铛 -->
        <el-popover placement="bottom-end" :width="380" trigger="click" @show="watchWs.markAlertsRead()">
          <template #reference>
            <el-badge :value="watchWs.unreadCount.value || ''" :max="99" class="alert-badge">
              <button class="icon-btn" :class="{ 'has-alert': watchWs.unreadCount.value > 0 }">
                <el-icon><Bell /></el-icon>
              </button>
            </el-badge>
          </template>
          <div class="alert-panel">
            <div class="alert-panel-header">
              <span>实时告警</span>
              <el-button text size="small" @click="watchWs.clearAlerts()">清空</el-button>
            </div>
            <div v-if="!watchWs.alerts.length" class="alert-empty">暂无告警</div>
            <div
              v-for="a in watchWs.alerts"
              :key="a.id"
              class="alert-item"
              :class="a.level"
            >
              <span class="alert-time">{{ a.time }}</span>
              <span class="alert-msg">{{ a.message }}</span>
            </div>
          </div>
        </el-popover>

        <el-button size="small" @click="searchOpen = true">
          <el-icon><Search /></el-icon>
          {{ currentContract ? currentContract.name + ' ' + currentContract.symbol : '选择合约' }}
        </el-button>

        <!-- 快速跳转热门品种 -->
        <div class="hot-pills">
          <span
            v-for="c in HOT_QUICK"
            :key="c.symbol"
            class="hot-pill"
            :class="{ active: currentContract?.symbol === c.symbol }"
            @click="selectContract(c)"
          >{{ c.symbol }}</span>
        </div>
      </div>
    </header>

    <!-- ── K 线图表 ────────────────────────────────────────────────────── -->
    <div class="kv-chart-wrap">
      <KlineChart
        :symbol="currentContract?.symbol ?? ''"
        :name="currentContract ? currentContract.name : ''"
        default-interval="1d"
        :default-limit="500"
      />
    </div>

    <!-- ── 合约搜索弹窗 ─────────────────────────────────────────────────── -->
    <ContractSearch
      v-model="searchOpen"
      @select="selectContract"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ArrowLeft, Search, Bell } from '@element-plus/icons-vue'
import ContractSearch from '@/components/ContractSearch.vue'
import KlineChart from '@/components/KlineChart.vue'
import { HOT_CONTRACTS } from '@/composables/useContractSearch.js'
import { useWatchStore, useHistoryStore } from '@/stores/index.js'
import { useWatchWs } from '@/composables/useWatchWs.js'

const router       = useRouter()
const watchStore   = useWatchStore()
const historyStore = useHistoryStore()

// ── Stores ────────────────────────────────────────────────────────────────
const { currentSymbol: currentContract, currentInterval } = storeToRefs(watchStore)

const searchOpen = ref(false)
const HOT_QUICK  = HOT_CONTRACTS.slice(0, 8)

// ── WebSocket 实时行情 ────────────────────────────────────────────────────
const watchWs = useWatchWs()

const { connected: wsConnected, connecting: wsConnecting } = watchWs

/** 当前品种的 tick 数据（响应式代理） */
const curTick = computed(() =>
  currentContract.value ? watchWs.getTick(currentContract.value.symbol) : { last: 0 }
)

/** 价格方向 css class */
const priceDir = computed(() => {
  const c = curTick.value.changeRate ?? 0
  return c > 0 ? 'up' : c < 0 ? 'down' : ''
})

// ── 订阅管理 ──────────────────────────────────────────────────────────────
/** 当品种或周期变化时，更新 WS 订阅 */
watch(
  [currentContract, currentInterval],
  ([newContract, newInterval], [oldContract]) => {
    // 取消旧品种订阅
    if (oldContract?.symbol && oldContract.symbol !== newContract?.symbol) {
      watchWs.unsubscribe(oldContract.symbol)
    }
    // 订阅新品种
    if (newContract?.symbol) {
      const klineChannel = `kline_${newInterval ?? '1d'}`
      watchWs.subscribe(newContract.symbol, ['tick', klineChannel])
    }
  },
  { immediate: true }
)

// ── 合约选择 ──────────────────────────────────────────────────────────────
function selectContract(contract) {
  watchStore.setSymbol(contract)
  searchOpen.value = false
}

// ── 初始化 ────────────────────────────────────────────────────────────────
onMounted(() => {
  if (!currentContract.value && historyStore.recentSymbols.length) {
    watchStore.setSymbol(historyStore.recentSymbols[0])
  }
})
</script>

<style scoped>
.kline-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0a0a14;
  color: #ccc;
  overflow: hidden;
}

/* ── 顶部导航栏 ──────────────────────────────────────────────────────────── */
.kv-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  height: 48px;
  background: #0d0d1a;
  border-bottom: 1px solid #1e1e2e;
  flex-shrink: 0;
  gap: 12px;
  flex-wrap: wrap;
}

.kv-topbar-left,
.kv-topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.kv-title {
  font-size: 15px;
  font-weight: 600;
  color: #e2e2e2;
}

.hot-pills {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.hot-pill {
  padding: 2px 8px;
  background: #1a1a2e;
  border: 1px solid #2a2a3e;
  border-radius: 12px;
  font-size: 11px;
  color: #888;
  cursor: pointer;
  transition: all 0.15s;
  user-select: none;
}

.hot-pill:hover {
  border-color: #3b82f6;
  color: #3b82f6;
}

.hot-pill.active {
  border-color: #3b82f6;
  color: #3b82f6;
  background: rgba(59,130,246,0.12);
}

/* ── 图表区 ───────────────────────────────────────────────────────────────── */
.kv-chart-wrap {
  flex: 1;
  min-height: 0;
  padding: 10px 12px;
  box-sizing: border-box;
}

/* ── 实时报价条 ──────────────────────────────────────────────────────────── */
.tick-strip {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
}

.tick-last {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #e2e2e2;
}

.tick-chg {
  font-size: 13px;
  color: #888;
}

.tick-meta {
  font-size: 11px;
  color: #555;
}

.tick-last.up, .tick-chg.up  { color: #ef4444; }
.tick-last.down, .tick-chg.down { color: #22c55e; }

/* ── WS 状态点 ────────────────────────────────────────────────────────────── */
.ws-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.ws-dot.on   { background: #22c55e; box-shadow: 0 0 5px #22c55e; }
.ws-dot.off  { background: #555; }
.ws-dot.blink {
  background: #f59e0b;
  animation: blink 1s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.3; }
}

/* ── 告警按钮 & 铃铛 ─────────────────────────────────────────────────────── */
.icon-btn {
  padding: 4px 8px;
  background: transparent;
  border: 1px solid #333;
  border-radius: 3px;
  color: #888;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.15s;
  display: flex;
  align-items: center;
}

.icon-btn:hover    { border-color: #555; color: #ddd; }
.icon-btn.has-alert { border-color: #f59e0b; color: #f59e0b; }

.alert-badge :deep(.el-badge__content) {
  background: #ef4444;
}

/* ── 告警面板 ────────────────────────────────────────────────────────────── */
.alert-panel {
  max-height: 360px;
  overflow-y: auto;
}

.alert-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid #333;
  font-size: 13px;
  font-weight: 600;
  color: #ccc;
  margin-bottom: 6px;
}

.alert-empty {
  text-align: center;
  color: #555;
  padding: 20px 0;
  font-size: 13px;
}

.alert-item {
  display: flex;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid #1a1a2a;
  font-size: 12px;
  line-height: 1.5;
}

.alert-time {
  color: #555;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.alert-msg { color: #ccc; }

.alert-item.warning .alert-msg { color: #f59e0b; }
.alert-item.danger  .alert-msg { color: #ef4444; }

/* ── 移动端 ──────────────────────────────────────────────────────────────── */
@media (max-width: 767px) {
  .kv-topbar {
    height: auto;
    padding: 6px 10px;
  }

  .hot-pills, .tick-meta {
    display: none;
  }

  .kv-chart-wrap {
    padding: 4px;
  }
}
</style>
