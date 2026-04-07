<template>
  <div class="watch-page">

    <!-- ════════════════════════════════════════
         头部
    ════════════════════════════════════════ -->
    <header class="watch-header">
      <!-- 左侧：导航 + 品种信息 -->
      <div class="header-left">
        <!-- 移动端：侧边栏收折按钮 -->
        <button class="icon-btn" @click="toggleSidebar" :title="showSidebar ? '收起侧边栏' : '展开侧边栏'">
          <el-icon><Menu /></el-icon>
        </button>

        <el-button text @click="router.push('/')" class="back-btn">
          <el-icon><ArrowLeft /></el-icon>
          <span class="back-text">监控台</span>
        </el-button>

        <div class="header-divider" />

        <!-- 当前品种 + 实时报价 -->
        <template v-if="currentContract">
          <div class="header-quote">
            <span class="hq-symbol">{{ currentContract.symbol }}</span>
            <span class="hq-name">{{ currentContract.name }}</span>
            <template v-if="curTick.last > 0">
              <span class="hq-price" :class="priceDir">{{ curTick.last }}</span>
              <span class="hq-chg" :class="priceDir">
                {{ curTick.changeRate >= 0 ? '+' : '' }}{{ curTick.changeRate?.toFixed(2) }}%
              </span>
              <span class="hq-meta">
                高{{ curTick.high }}
                &thinsp;低{{ curTick.low }}
                &thinsp;量{{ fmtVol(curTick.volume) }}
              </span>
            </template>
          </div>
        </template>
        <span v-else class="hq-empty">请选择合约</span>
      </div>

      <!-- 中间：搜索框 -->
      <div class="header-center">
        <div class="search-trigger" @click="searchOpen = true">
          <el-icon class="search-icon"><Search /></el-icon>
          <span class="search-placeholder">搜索合约代码或名称…</span>
          <kbd class="search-kbd">Ctrl K</kbd>
        </div>
      </div>

      <!-- 右侧：工具 -->
      <div class="header-right">
        <!-- WS 状态 -->
        <el-tooltip
          :content="wsConnected ? '行情已连接' : wsConnecting ? '连接中…' : '行情已断开'"
          placement="bottom"
        >
          <div class="ws-status" @click="!wsConnected && watchWs.reconnect()">
            <span class="ws-dot" :class="wsConnected ? 'on' : wsConnecting ? 'blink' : 'off'" />
            <span class="ws-label">{{ wsConnected ? '已连接' : wsConnecting ? '连接中' : '断开' }}</span>
          </div>
        </el-tooltip>

        <!-- 告警铃铛 -->
        <el-popover
          placement="bottom-end"
          :width="400"
          trigger="click"
          popper-class="alert-popper"
          @show="watchWs.markAlertsRead()"
        >
          <template #reference>
            <el-badge
              :value="watchWs.unreadCount.value > 0 ? watchWs.unreadCount.value : ''"
              :max="99"
            >
              <button class="icon-btn" :class="{ 'alert-active': watchWs.unreadCount.value > 0 }">
                <el-icon><Bell /></el-icon>
              </button>
            </el-badge>
          </template>
          <!-- 告警下拉 -->
          <div class="alert-dropdown">
            <div class="ad-header">
              <span>全部告警 ({{ watchWs.alerts.length }})</span>
              <el-button text size="small" @click="watchWs.clearAlerts()">清空</el-button>
            </div>
            <div v-if="!watchWs.alerts.length" class="ad-empty">暂无告警</div>
            <div
              v-for="a in watchWs.alerts"
              :key="a.id"
              class="ad-item"
              :class="a.level"
            >
              <div class="ad-meta">
                <span class="ad-symbol">{{ a.symbol }}</span>
                <span class="ad-time">{{ a.time }}</span>
              </div>
              <span class="ad-msg">{{ a.message }}</span>
            </div>
          </div>
        </el-popover>

        <!-- 右侧面板切换 -->
        <el-tooltip content="深度行情" placement="bottom">
          <button
            class="icon-btn"
            :class="{ active: showRightPanel }"
            @click="toggleRightPanel"
          >
            <el-icon><DataAnalysis /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </header>

    <!-- ════════════════════════════════════════
         主体区域（三列布局）
    ════════════════════════════════════════ -->
    <div class="watch-body" :class="bodyClasses">

      <!-- ── 左侧边栏 ──────────────────────────────────────────────── -->
      <transition name="slide-left">
        <aside
          v-show="showSidebar"
          class="watch-sidebar"
          :class="{ 'sidebar-overlay': isMobile && showSidebar }"
        >
          <!-- 移动端遮罩点击收起 -->
          <div
            v-if="isMobile && showSidebar"
            class="sidebar-backdrop"
            @click="showSidebar = false"
          />
          <div class="sidebar-inner">
            <WatchSidebar @select="onContractSelect" />
          </div>
        </aside>
      </transition>

      <!-- ── 主图区域 ────────────────────────────────────────────── -->
      <main class="watch-main">
        <KlineChart
          :symbol="currentContract?.symbol ?? ''"
          :name="currentContract?.name ?? ''"
          default-interval="1d"
          :default-limit="500"
        />
      </main>

      <!-- ── 右侧面板 ───────────────────────────────────────────── -->
      <transition name="slide-right">
        <aside v-show="showRightPanel && !isSmall" class="watch-right">
          <WatchRightPanel :contract="currentContract" />
        </aside>
      </transition>
    </div>

    <!-- ── 合约搜索弹窗 ──────────────────────────────────────────── -->
    <ContractSearch
      v-model="searchOpen"
      @select="onContractSelect"
    />

  </div>
</template>

<script setup>
import {
  ref, computed, watch, provide, onMounted, onUnmounted,
} from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  ArrowLeft, Search, Bell, Menu, DataAnalysis,
} from '@element-plus/icons-vue'

import ContractSearch  from '@/components/ContractSearch.vue'
import WatchSidebar    from '@/components/WatchSidebar.vue'
import WatchRightPanel from '@/components/WatchRightPanel.vue'
import KlineSkeleton   from '@/components/KlineSkeleton.vue'
import { defineAsyncComponent } from 'vue'

// 懒加载 KlineChart（ECharts 体积较大，异步加载避免阻塞首屏）
const KlineChart = defineAsyncComponent({
  loader:           () => import('@/components/KlineChart.vue'),
  loadingComponent: KlineSkeleton,
  delay:            0,
  timeout:          15000,
})

import { useWatchStore }   from '@/stores/watch.js'
import { useHistoryStore } from '@/stores/history.js'
import { useWatchWs }      from '@/composables/useWatchWs.js'
import { useHotkeys }      from '@/composables/useHotkeys.js'
import { INTERVALS }       from '@/composables/useKlineData.js'

// ── 路由 ──────────────────────────────────────────────────────────────────
const router = useRouter()

// ── Stores ────────────────────────────────────────────────────────────────
const watchStore   = useWatchStore()
const historyStore = useHistoryStore()
const { currentSymbol: currentContract, currentInterval } = storeToRefs(watchStore)

// ── WS（单例，通过 provide 共享给子组件） ────────────────────────────────
const watchWs = useWatchWs()
provide('watchWs', watchWs)

const { connected: wsConnected, connecting: wsConnecting } = watchWs

// ── 搜索 ──────────────────────────────────────────────────────────────────
const searchOpen = ref(false)

// Ctrl+K — 打开搜索；Esc — 关闭搜索；[ ] — 切换侧边栏/右面板
useHotkeys([
  { key: 'k', ctrl: true, allowInInput: true, handler: () => { searchOpen.value = true } },
  { key: 'Escape', allowInInput: false, handler: () => { if (searchOpen.value) searchOpen.value = false } },
  { key: '[', handler: () => toggleSidebar() },
  { key: ']', handler: () => toggleRightPanel() },
  // W / S — 侧边栏中上/下切换品种（快速浏览）
  {
    key: 'w',
    handler() {
      if (!historyStore.recentSymbols.length) return
      const list = historyStore.recentSymbols
      const cur  = currentContract.value?.symbol
      const idx  = list.findIndex(s => s.symbol === cur)
      if (idx > 0) watchStore.setSymbol(list[idx - 1])
    },
  },
  {
    key: 's',
    handler() {
      if (!historyStore.recentSymbols.length) return
      const list = historyStore.recentSymbols
      const cur  = currentContract.value?.symbol
      const idx  = list.findIndex(s => s.symbol === cur)
      if (idx >= 0 && idx < list.length - 1) watchStore.setSymbol(list[idx + 1])
    },
  },
])

onMounted(() => {
  // 恢复上次查看的品种
  if (!currentContract.value && historyStore.recentSymbols.length) {
    watchStore.setSymbol(historyStore.recentSymbols[0])
  }
})
onUnmounted(() => {})

// ── 响应式布局 ────────────────────────────────────────────────────────────
const windowWidth  = ref(window.innerWidth)
const isMobile     = computed(() => windowWidth.value < 768)
const isSmall      = computed(() => windowWidth.value < 1024)
const isLarge      = computed(() => windowWidth.value >= 1280)

function onResize() { windowWidth.value = window.innerWidth }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

// 侧边栏默认：大屏展开，小屏收起
const showSidebar    = ref(!isMobile.value)
// 右侧面板默认：大屏展开，中屏隐藏
const showRightPanel = ref(isLarge.value)

// 监听屏幕尺寸变化自动调整
watch(isMobile, (mobile) => {
  if (mobile) showSidebar.value = false
  else if (windowWidth.value >= 768) showSidebar.value = true
})

watch(isLarge, (large) => {
  showRightPanel.value = large
})

function toggleSidebar()    { showSidebar.value    = !showSidebar.value }
function toggleRightPanel() { showRightPanel.value = !showRightPanel.value }

const bodyClasses = computed(() => ({
  'has-sidebar':  showSidebar.value && !isMobile.value,
  'has-right':    showRightPanel.value && !isSmall.value,
  'mobile':       isMobile.value,
}))

// ── 实时行情 ──────────────────────────────────────────────────────────────
const curTick = computed(() =>
  currentContract.value ? watchWs.getTick(currentContract.value.symbol) : { last: 0 }
)

const priceDir = computed(() => {
  const r = curTick.value.changeRate ?? 0
  return r > 0 ? 'up' : r < 0 ? 'down' : ''
})

// ── 订阅管理 ──────────────────────────────────────────────────────────────
watch(
  [currentContract, currentInterval],
  ([newC, newIv], [oldC]) => {
    if (oldC?.symbol && oldC.symbol !== newC?.symbol) {
      watchWs.unsubscribe(oldC.symbol)
    }
    if (newC?.symbol) {
      watchWs.subscribe(newC.symbol, ['tick', `kline_${newIv ?? '1d'}`])
    }
  },
  { immediate: true }
)

// ── 合约选择 ──────────────────────────────────────────────────────────────
function onContractSelect(contract) {
  watchStore.setSymbol(contract)
  searchOpen.value = false
  // 移动端选中后自动收起侧边栏
  if (isMobile.value) showSidebar.value = false
}

// ── 格式化 ────────────────────────────────────────────────────────────────
function fmtVol(v) {
  if (!v) return '--'
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return String(v)
}
</script>

<style scoped>
/* ════════════════════════════════════════════════════════════
   页面整体
════════════════════════════════════════════════════════════ */
.watch-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0a0a14;
  color: #ccc;
  overflow: hidden;
  font-size: 13px;
}

/* ════════════════════════════════════════════════════════════
   头部
════════════════════════════════════════════════════════════ */
.watch-header {
  display: flex;
  align-items: center;
  height: 48px;
  padding: 0 12px;
  background: #0d0d1a;
  border-bottom: 1px solid #1e1e2e;
  flex-shrink: 0;
  gap: 12px;
  z-index: 20;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.header-center {
  flex: 1;
  min-width: 0;
  max-width: 480px;
  margin: 0 auto;
}

/* 搜索触发器 */
.search-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #13131f;
  border: 1px solid #2a2a3e;
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s;
  width: 100%;
}

.search-trigger:hover { border-color: #3b82f6; }

.search-icon { color: #555; font-size: 14px; }

.search-placeholder {
  flex: 1;
  color: #444;
  font-size: 12px;
  white-space: nowrap;
}

.search-kbd {
  font-size: 10px;
  color: #444;
  background: #1a1a2a;
  border: 1px solid #2a2a3a;
  border-radius: 3px;
  padding: 1px 5px;
}

/* 返回按钮 */
.back-btn { font-size: 12px; padding: 4px 6px; }
.back-text { font-size: 12px; }

.header-divider {
  width: 1px;
  height: 16px;
  background: #222;
  flex-shrink: 0;
}

/* 头部报价 */
.header-quote {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: nowrap;
}

.hq-symbol {
  font-size: 15px;
  font-weight: 700;
  color: #e2e2e2;
}

.hq-name {
  font-size: 12px;
  color: #666;
}

.hq-price {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #ccc;
}

.hq-chg {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: #888;
}

.hq-meta {
  font-size: 11px;
  color: #555;
}

.hq-empty { font-size: 12px; color: #444; }

.hq-price.up, .hq-chg.up  { color: #ef4444; }
.hq-price.down,.hq-chg.down { color: #22c55e; }

/* WS 状态 */
.ws-status {
  display: flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 4px;
  transition: background 0.15s;
}

.ws-status:hover { background: #1a1a2a; }

.ws-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.ws-dot.on   { background: #22c55e; box-shadow: 0 0 5px #22c55e80; }
.ws-dot.off  { background: #555; }
.ws-dot.blink {
  background: #f59e0b;
  animation: blink 1s ease-in-out infinite;
}

@keyframes blink {
  0%,100% { opacity: 1; }
  50%      { opacity: 0.3; }
}

.ws-label {
  font-size: 11px;
  color: #666;
}

/* 通用图标按钮 */
.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  background: transparent;
  border: 1px solid #2a2a3e;
  border-radius: 4px;
  color: #666;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.15s;
  flex-shrink: 0;
}

.icon-btn:hover  { border-color: #444; color: #ccc; }
.icon-btn.active { border-color: #3b82f6; color: #3b82f6; background: rgba(59,130,246,0.1); }
.icon-btn.alert-active { border-color: #f59e0b; color: #f59e0b; }

/* ════════════════════════════════════════════════════════════
   主体布局
════════════════════════════════════════════════════════════ */
.watch-body {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr;   /* 默认只有主图 */
  grid-template-rows: 1fr;
  min-height: 0;
  overflow: hidden;
  position: relative;
}

/* 有侧边栏时 */
.watch-body.has-sidebar {
  grid-template-columns: 200px 1fr;
}

/* 有右侧面板时 */
.watch-body.has-sidebar.has-right {
  grid-template-columns: 200px 1fr 240px;
}

.watch-body.has-right:not(.has-sidebar) {
  grid-template-columns: 1fr 240px;
}

/* ── 侧边栏 ──────────────────────────────────────────────────────────────── */
.watch-sidebar {
  grid-row: 1;
  grid-column: 1;
  overflow: hidden;
  z-index: 10;
}

.sidebar-inner {
  height: 100%;
  overflow: hidden;
}

/* 移动端浮层 */
.sidebar-overlay .sidebar-inner {
  position: fixed;
  top: 48px;
  left: 0;
  width: 220px;
  bottom: 0;
  z-index: 100;
  box-shadow: 4px 0 20px rgba(0,0,0,0.5);
}

.sidebar-backdrop {
  position: fixed;
  inset: 0;
  z-index: 99;
  background: rgba(0,0,0,0.4);
}

/* ── 主图 ───────────────────────────────────────────────────────────────── */
.watch-main {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  padding: 8px;
  box-sizing: border-box;
}

/* ── 右侧面板 ─────────────────────────────────────────────────────────────── */
.watch-right {
  min-width: 0;
  overflow: hidden;
}

/* ── 过渡动画 ─────────────────────────────────────────────────────────────── */
.slide-left-enter-active,
.slide-left-leave-active,
.slide-right-enter-active,
.slide-right-leave-active {
  transition: all 0.22s ease;
  overflow: hidden;
}

.slide-left-enter-from,
.slide-left-leave-to  { width: 0; opacity: 0; }
.slide-left-enter-to,
.slide-left-leave-from { width: 200px; opacity: 1; }

.slide-right-enter-from,
.slide-right-leave-to  { width: 0; opacity: 0; }
.slide-right-enter-to,
.slide-right-leave-from { width: 240px; opacity: 1; }

/* ════════════════════════════════════════════════════════════
   告警下拉
════════════════════════════════════════════════════════════ */
.alert-dropdown {
  max-height: 420px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: #2a2a3e transparent;
}

.ad-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid #2a2a3a;
  font-size: 13px;
  font-weight: 600;
  color: #ccc;
  margin-bottom: 4px;
}

.ad-empty {
  text-align: center;
  color: #444;
  padding: 20px;
  font-size: 12px;
}

.ad-item {
  padding: 6px 0;
  border-bottom: 1px solid #161624;
}

.ad-meta {
  display: flex;
  justify-content: space-between;
  margin-bottom: 2px;
}

.ad-symbol {
  font-size: 11px;
  font-weight: 600;
  color: #888;
}

.ad-time {
  font-size: 10px;
  color: #444;
  font-variant-numeric: tabular-nums;
}

.ad-msg {
  font-size: 12px;
  color: #888;
  line-height: 1.5;
}

.ad-item.warning .ad-msg { color: #f59e0b; }
.ad-item.danger  .ad-msg { color: #ef4444; }

/* ════════════════════════════════════════════════════════════
   响应式
════════════════════════════════════════════════════════════ */
@media (max-width: 767px) {
  .watch-header { padding: 0 8px; gap: 6px; }
  .back-text, .hq-name, .hq-meta, .search-kbd, .ws-label { display: none; }
  .header-center { max-width: 100%; }
  .watch-main { padding: 4px; }
}

@media (max-width: 1023px) {
  .hq-meta { display: none; }
}
</style>

<!-- 全局样式：告警 popover 深色主题 -->
<style>
.alert-popper.el-popover {
  background: #13131f !important;
  border-color: #2a2a3a !important;
}

.alert-popper.el-popover .el-popper__arrow::before {
  background: #13131f !important;
  border-color: #2a2a3a !important;
}
</style>
