<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import GlobalDashboard from '@/components/GlobalDashboard.vue'
import StrategyPanel    from '@/components/StrategyPanel.vue'
import OrderBook        from '@/components/OrderBook.vue'
import TradingPanel     from '@/components/TradingPanel.vue'
import { useAuthStore } from '@/stores/auth.js'
import { fetchStrategies, logout } from '@/api/index.js'

const router    = useRouter()
const authStore = useAuthStore()

// ── Mock 数据（后端不可用时展示）─────────────────────────────────────────
const MOCK_STRATEGIES = [
  { strategy_id: 'ma_cross_01', name: 'MA双均线',   status: 'running', symbol: 'rb2505',
    pnl: 3250.00,  positions: [{ symbol: 'rb2505', direction: 'long', volume: 10, cost_price: 3520, pnl: 3250 }],
    trade_count: 18, error_count: 0 },
  { strategy_id: 'rsi_01',      name: 'RSI均值回归', status: 'stopped', symbol: 'IF2505',
    pnl: -850.00,  positions: [], trade_count: 6,  error_count: 1 },
  { strategy_id: 'breakout_01', name: '突破策略',    status: 'error',   symbol: 'au2506',
    pnl: 0,        positions: [], trade_count: 0,  error_count: 3 },
]

const strategies        = ref([])
const loadingStrategies = ref(false)
const isMockMode        = ref(false)
const lastRefreshTime   = ref('')
const loggingOut        = ref(false)

// ── 数据加载 ──────────────────────────────────────────────────────────────
async function loadStrategies() {
  loadingStrategies.value = true
  try {
    strategies.value = await fetchStrategies()
    isMockMode.value = false
  } catch (err) {
    if (err.message?.includes('401')) return
    ElMessage.warning('无法连接后端服务，当前显示模拟数据')
    strategies.value = MOCK_STRATEGIES
    isMockMode.value = true
  } finally {
    loadingStrategies.value = false
    lastRefreshTime.value   = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }
}

// ── 定时自动刷新（每 5s）──────────────────────────────────────────────────
let refreshTimer = null
onMounted(() => {
  loadStrategies()
  refreshTimer = setInterval(loadStrategies, 5_000)
})
onUnmounted(() => clearInterval(refreshTimer))

// ── 登出 ──────────────────────────────────────────────────────────────────
async function handleLogout() {
  try {
    await ElMessageBox.confirm('确认断开 CTP 连接并退出登录？', '退出', {
      confirmButtonText: '确认断开',
      cancelButtonText:  '取消',
      type:              'warning',
    })
  } catch { return }

  loggingOut.value = true
  try {
    await logout()
  } catch { /* 静默，本地清除即可 */ } finally {
    authStore.clearAuth()
    ElMessage.success('已断开连接')
    router.push({ name: 'Login' })
  }
}
</script>

<template>
  <div class="dashboard">

    <!-- ── 顶部标题栏 ─────────────────────────────────────────────────── -->
    <header class="app-header">
      <div class="header-left">
        <span class="app-icon">⚡</span>
        <span class="app-title">量化交易系统</span>
        <span class="app-version">v1.0.0</span>
      </div>
      <div class="header-center">
        <el-tag type="success" effect="dark" size="small" v-if="authStore.accountId">
          <el-icon><User /></el-icon>
          {{ authStore.accountId }}
        </el-tag>
        <span class="refresh-info c-muted">
          <el-icon><RefreshRight /></el-icon>
          最后刷新：{{ lastRefreshTime || '--' }}
        </span>
      </div>
      <div class="header-right">
        <el-button
          type="success"
          size="small"
          plain
          @click="router.push('/watch')"
        >
          <el-icon><TrendCharts /></el-icon>
          盯盘系统
        </el-button>
        <el-button
          size="small"
          plain
          @click="router.push('/system')"
        >
          <el-icon><Monitor /></el-icon>
          系统监控
        </el-button>
        <el-button
          type="primary"
          size="small"
          plain
          @click="router.push('/backtest')"
        >
          <el-icon><DataAnalysis /></el-icon>
          回测分析
        </el-button>
        <el-button
          :loading="loadingStrategies"
          size="small"
          plain
          @click="loadStrategies"
        >
          <el-icon><RefreshRight /></el-icon>
          刷新
        </el-button>
        <el-button
          type="danger"
          size="small"
          plain
          :loading="loggingOut"
          @click="handleLogout"
        >
          <el-icon><SwitchButton /></el-icon>
          断开退出
        </el-button>
      </div>
    </header>

    <!-- ── Mock 提示 ──────────────────────────────────────────────────── -->
    <el-alert
      v-if="isMockMode"
      title="当前使用模拟数据（后端未连接）"
      description="无法连接 API，展示内置 Mock 数据。后端就绪后点击「刷新」切换为真实数据。"
      type="warning"
      show-icon
      :closable="false"
      style="border-radius: 0; border-left: none; border-right: none"
    />

    <!-- ── 主体 ──────────────────────────────────────────────────────── -->
    <main class="app-main">

      <!-- ── 全局仪表盘 ──────────────────────────────────────────────── -->
      <section class="section">
        <div class="section-header">
          <h2 class="section-title">
            <el-icon><TrendCharts /></el-icon>
            全局仪表盘
            <span class="section-hint">实时 PnL · 收益率 · 夏普比率 · 最大回撤 · 仓位概览</span>
          </h2>
        </div>
        <GlobalDashboard />
      </section>

      <section class="section">
        <div class="section-header">
          <h2 class="section-title">
            <el-icon><DataLine /></el-icon>
            策略管理面板
            <span class="section-hint">启停 · 参数 · 信号 · 权重分配</span>
          </h2>
        </div>
        <StrategyPanel
          :strategies="strategies"
          :loading="loadingStrategies"
          @refresh="loadStrategies"
        />
      </section>

      <section class="section">
        <div class="section-header">
          <h2 class="section-title">
            <el-icon><Sell /></el-icon>
            手动交易
            <span class="section-hint">下单 · 撤单 · 快捷平仓</span>
          </h2>
        </div>
        <TradingPanel />
      </section>

      <section class="section">
        <div class="section-header">
          <h2 class="section-title">
            <el-icon><DocumentCopy /></el-icon>
            实时订单与持仓簿
            <span class="section-hint">委托单 · 成交记录 · 持仓明细</span>
          </h2>
        </div>
        <OrderBook />
      </section>

    </main>

    <footer class="app-footer">
      <span class="c-muted">量化交易系统 &copy; 2026</span>
      <span class="c-muted">策略自动刷新：5s</span>
    </footer>
  </div>
</template>

<style scoped>
.dashboard { min-height: 100vh; display: flex; flex-direction: column; background: var(--q-bg); }

.app-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 24px;
  background: var(--q-panel);
  border-bottom: 1px solid var(--q-border);
  position: sticky; top: 0; z-index: 100;
  gap: 12px;
}
.header-left  { display: flex; align-items: center; gap: 8px; }
.header-center{ display: flex; align-items: center; gap: 14px; flex: 1; justify-content: center; }
.header-right { display: flex; align-items: center; gap: 8px; }

.app-icon    { font-size: 20px; }
.app-title   { font-size: 16px; font-weight: 700; color: var(--q-blue); }
.app-version { font-size: 11px; color: var(--q-muted); background: var(--q-border); padding: 2px 7px; border-radius: 10px; }

.refresh-info { font-size: 12px; display: flex; align-items: center; gap: 4px; }

.app-main {
  flex: 1; padding: 20px 24px;
  display: flex; flex-direction: column; gap: 20px;
  max-width: 1600px; width: 100%; margin: 0 auto; box-sizing: border-box;
}

.section { display: flex; flex-direction: column; gap: 10px; }
.section-header { display: flex; align-items: center; }
.section-title {
  display: flex; align-items: center; gap: 8px;
  margin: 0; font-size: 14px; font-weight: 600; color: var(--q-text);
}
.section-hint {
  font-size: 11px; font-weight: 400; color: var(--q-muted);
  background: var(--q-border); padding: 2px 8px; border-radius: 10px;
}

.app-footer {
  display: flex; justify-content: space-between;
  padding: 10px 24px;
  border-top: 1px solid var(--q-border);
  font-size: 11px;
}

.c-muted { color: var(--q-muted); }
</style>
