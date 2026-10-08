<script setup>
import { ref, computed, watch, provide, defineAsyncComponent, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AccountStrip from '@/components/AccountStrip.vue'
import TradingWatchlist from '@/components/TradingWatchlist.vue'
import TradingMarket from '@/components/TradingMarket.vue'
import WatchRightPanel from '@/components/WatchRightPanel.vue'
import StrategyPanel from '@/components/StrategyPanel.vue'
import OrderBook from '@/components/OrderBook.vue'
import TradingPanel from '@/components/TradingPanel.vue'
import ContractSearch from '@/components/ContractSearch.vue'
import { useAuthStore } from '@/stores/auth.js'
import { useWatchStore } from '@/stores/watch.js'
import { useHotkeys } from '@/composables/useHotkeys.js'
import { useWatchWs } from '@/composables/useWatchWs.js'
import { fetchStrategies, logout } from '@/api/index.js'

const GlobalDashboard = defineAsyncComponent(() => import('@/components/GlobalDashboard.vue'))
const router = useRouter()
const authStore = useAuthStore()
const watchStore = useWatchStore()
provide('watchWs', useWatchWs())
const contract = computed(() => watchStore.currentSymbol)
const searchOpen = ref(false)
const activePanel = ref('')
const panelOpen = computed({ get: () => Boolean(activePanel.value), set: value => { if (!value) activePanel.value = '' } })
const strategies = ref([])
const loadingStrategies = ref(false)
const strategyError = ref('')
const loggingOut = ref(false)
let refreshTimer
let disposed = false

function selectContract(value) {
  watchStore.setSymbol(value)
  searchOpen.value = false
}
async function loadStrategies() {
  if (loadingStrategies.value) return
  loadingStrategies.value = true
  try {
    const result = await fetchStrategies()
    if (!disposed) { strategies.value = result; strategyError.value = '' }
  } catch (error) {
    if (!disposed) strategyError.value = `策略数据暂不可用：${error.message}`
  } finally {
    if (!disposed) loadingStrategies.value = false
  }
}
watch(activePanel, value => { if (value === 'strategies') loadStrategies() })
onMounted(() => { refreshTimer = setInterval(() => { if (activePanel.value === 'strategies') loadStrategies() }, 5000) })
onUnmounted(() => { disposed = true; clearInterval(refreshTimer) })
useHotkeys([{ key: 'k', ctrl: true, handler: () => { searchOpen.value = true } }])

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确认断开 CTP 连接并退出登录？', '退出', {
      confirmButtonText: '确认断开', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  loggingOut.value = true
  try { await logout() }
  catch { /* Session cleanup still applies if the connection has closed. */ }
  finally {
    authStore.clearAuth()
    ElMessage.success('已断开连接')
    router.push({ name: 'Login' })
  }
}
</script>

<template>
  <div class="dashboard trading-desk">
    <header class="desk-header">
      <a class="desk-brand" href="/" aria-label="Quant 交易台首页"><span class="brand-mark">Q</span><span>QUANT<span class="brand-sub">交易工作台</span></span></a>
      <nav class="desk-nav" aria-label="工作区导航">
        <span class="nav-current" aria-current="page">交易台</span>
        <button @click="router.push('/watch')">专注盯盘</button>
        <button @click="activePanel = 'strategies'">策略管理</button>
        <button @click="activePanel = 'account'">资金分析</button>
        <button @click="router.push('/backtest')">回测</button>
        <button @click="router.push('/system')">系统</button>
      </nav>
      <button class="desk-search" @click="searchOpen = true"><el-icon><Search /></el-icon><span>搜索合约</span><kbd>Ctrl K</kbd></button>
      <el-button class="desk-logout" size="small" plain :loading="loggingOut" @click="handleLogout">断开退出</el-button>
    </header>
    <AccountStrip @details="activePanel = 'account'" />
    <main class="desk-grid">
      <aside class="desk-watchlist desk-surface" aria-label="自选合约与行情提醒"><TradingWatchlist @select="selectContract" @search="searchOpen = true" /></aside>
      <section class="desk-market desk-surface" aria-label="当前合约行情"><TradingMarket :contract="contract" @search="searchOpen = true" /></section>
      <aside class="desk-depth desk-surface" aria-label="盘口与合约统计"><div class="depth-heading">盘口与合约统计</div><WatchRightPanel :contract="contract" /></aside>
      <aside class="desk-ticket" aria-label="下单与快捷平仓"><TradingPanel compact :contract="contract" @select-contract="selectContract" /></aside>
      <section class="desk-orders" aria-label="委托成交与持仓"><OrderBook compact /></section>
    </main>
    <footer class="desk-footer"><span>交易台 <span class="footer-sep">/</span> {{ contract?.symbol || '未选择合约' }}</span><span>合约联动 · 限价委托 · 发送前确认</span></footer>
    <ContractSearch v-model="searchOpen" @select="selectContract" />
    <el-drawer v-model="panelOpen" :title="activePanel === 'strategies' ? '策略管理' : '资金分析'" size="min(1080px, 100vw)" destroy-on-close class="desk-detail-drawer">
      <template v-if="activePanel === 'strategies'">
        <el-alert v-if="strategyError" :title="strategyError" description="保留上次成功读取的列表；请重试，未提供模拟策略。" type="warning" :closable="false" />
        <StrategyPanel :strategies="strategies" :loading="loadingStrategies" @refresh="loadStrategies" />
      </template>
      <GlobalDashboard v-else-if="activePanel === 'account'" />
    </el-drawer>
  </div>
</template>

<style scoped>
.trading-desk {
  --q-bg: #0b111a; --q-panel: #141c27; --q-border: #293646; --q-text: #dce6f2; --q-muted: #97a8bb;
  height: 100dvh; min-height: 680px; display: flex; flex-direction: column;
  background: var(--q-bg); color: var(--q-text); font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
}
.desk-header { min-height: 58px; padding: 0 18px; display: flex; align-items: center; gap: 24px; border-bottom: 1px solid var(--q-border); flex-shrink: 0; }
.desk-brand { display: flex; align-items: center; gap: 9px; color: var(--q-text); text-decoration: none; font: 700 15px var(--q-font-mono); letter-spacing: 1.2px; }
.brand-mark { display: grid; place-items: center; width: 30px; height: 30px; border: 1px solid var(--q-blue); border-radius: 5px; color: var(--q-blue); font-size: 23px; }
.brand-sub { display: block; font: 10px 'Microsoft YaHei', sans-serif; color: var(--q-muted); letter-spacing: 2px; margin-top: 2px; }
.desk-nav { display: flex; align-self: stretch; align-items: stretch; gap: 8px; }
.desk-nav button, .nav-current { background: none; border: 0; border-bottom: 2px solid transparent; color: var(--q-muted); padding: 0 13px; display: flex; align-items: center; font: inherit; font-size: 13px; white-space: nowrap; }
.desk-nav button { cursor: pointer; }.desk-nav button:hover { color: var(--q-text); background: var(--q-panel); }
.nav-current { color: var(--q-blue); border-color: var(--q-blue); font-weight: 600; }
.desk-search { margin-left: auto; display: flex; align-items: center; gap: 10px; background: var(--q-panel); border: 1px solid var(--q-border); border-radius: 4px; padding: 8px 12px; color: var(--q-muted); cursor: pointer; white-space: nowrap; }
kbd { color: var(--q-muted); font: 10px var(--q-font-mono); border-left: 1px solid var(--q-border); padding-left: 14px; }
.desk-grid { flex: 1; min-height: 0; display: grid; padding: 10px 12px 0; gap: 10px; grid-template-columns: 206px minmax(0, 1fr) 372px; grid-template-rows: minmax(330px, 1fr) minmax(250px, .48fr); grid-template-areas: 'watch market ticket' 'watch orders ticket'; }
.desk-surface { border: 1px solid var(--q-border); border-radius: 5px; background: var(--q-panel); overflow: hidden; }
.desk-watchlist { grid-area: watch; }.desk-market { grid-area: market; }.desk-ticket { grid-area: ticket; overflow: auto; border-radius: 5px; background: var(--q-panel); }.desk-orders { grid-area: orders; }.desk-grid > * { min-width: 0; min-height: 0; }
.desk-depth { display: none; grid-area: depth; overflow: auto; }.depth-heading { font-size: 12px; font-weight: 600; padding: 15px 12px; border-bottom: 1px solid var(--q-border); }
.desk-depth :deep(.wrp) { height: auto; background: var(--q-panel); }.desk-depth :deep(.section) { border-color: var(--q-border); }
.desk-depth :deep(.section-title), .desk-depth :deep(.stat-label), .desk-depth :deep(.depth-label), .desk-depth :deep(.ch-name), .desk-depth :deep(.ch-price-empty), .desk-depth :deep(.empty-hint-sm), .desk-depth :deep(.depth-vol) { color: var(--q-muted); }
.desk-depth :deep(.contract-header) { background: var(--q-panel); }
.desk-footer { height: 28px; display: flex; align-items: center; justify-content: space-between; padding: 0 14px; color: var(--q-muted); font-size: 10px; flex-shrink: 0; }.footer-sep { margin: 0 10px; color: var(--q-border); }
button:focus-visible, a:focus-visible { outline: 2px solid var(--q-blue); outline-offset: 3px; }
@media (min-width: 1850px) { .desk-grid { grid-template-columns: 206px minmax(0, 1fr) 210px 372px; grid-template-areas: 'watch market depth ticket' 'watch orders orders ticket'; }.desk-depth { display: block; } }
@media (min-width: 2200px) { .desk-grid { grid-template-columns: 250px minmax(0, 1fr) 235px 410px; } }
@media (max-width: 1500px) { .desk-grid { grid-template-columns: 176px minmax(0, 1fr) 350px; gap: 8px; padding: 8px 8px 0; grid-template-rows: minmax(350px, 1fr) minmax(200px, .4fr); }.desk-header { gap: 14px; padding: 0 12px; }.desk-nav { gap: 0; }.desk-nav button, .nav-current { padding: 0 9px; } }
@media (max-width: 1150px) { .desk-grid { grid-template-columns: 150px minmax(0, 1fr) 340px; }.desk-search kbd, .brand-sub { display: none; }.desk-header { gap: 10px; } }
@media (max-width: 1000px) {
  .trading-desk { height: auto; min-height: 100dvh; }.desk-header { flex-wrap: wrap; padding: 10px 12px; gap: 10px; }.desk-nav { order: 3; width: 100%; height: 36px; overflow-x: auto; }
  .desk-grid { grid-template-columns: minmax(0, 1fr) 350px; grid-template-rows: 480px 360px; grid-template-areas: 'market ticket' 'orders ticket'; }.desk-watchlist { display: none; }
}
@media (max-width: 700px) {
  .desk-brand { font-size: 13px; }.desk-logout { margin-left: 0; }.desk-search { padding: 7px 9px; gap: 5px; }
  .desk-grid { display: flex; flex-direction: column; padding: 8px; }.desk-market { height: 580px; flex: none; }.desk-ticket { overflow: visible; order: 2; }.desk-orders { height: 350px; flex: none; order: 3; }
  .desk-footer { padding: 0 10px; }.desk-footer > span:last-child { display: none; }
}
</style>
