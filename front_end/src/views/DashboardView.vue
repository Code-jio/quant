<script setup>
import { ref, onMounted } from 'vue'
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

const strategies        = ref([])
const loadingStrategies = ref(false)
const liveDataError      = ref('')
const lastRefreshTime   = ref('')
const loggingOut        = ref(false)

// ── 数据加载 ──────────────────────────────────────────────────────────────
async function loadStrategies() {
  if (strategies.value.length === 0) loadingStrategies.value = true
  try {
    const next = await fetchStrategies()
    if (Array.isArray(next)) {
      strategies.value = next
      liveDataError.value = ''
    }
  } catch (err) {
    if (err.message?.includes('401')) return
    strategies.value = []
    liveDataError.value = '实盘数据不可用，已禁止展示模拟数据。请检查交易服务连接后再操作。'
    ElMessage.error(liveDataError.value)
  } finally {
    loadingStrategies.value = false
    lastRefreshTime.value   = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }
}

onMounted(() => {
  loadStrategies()
})

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

    <!-- ── 实盘数据故障提示 ───────────────────────────────────────────── -->
    <el-alert
      v-if="liveDataError"
      title="实盘数据不可用"
      :description="liveDataError"
      type="error"
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
      <span class="c-muted">策略手动刷新</span>
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
