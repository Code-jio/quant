<template>
  <div class="ws-sidebar">

    <!-- ── 标签页 ──────────────────────────────────────────────────────── -->
    <div class="tab-bar">
      <button
        v-for="tab in TABS" :key="tab.key"
        class="tab-btn" :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >{{ tab.label }}</button>
    </div>

    <!-- ══════════════════════════════════════════
         自选 Tab
    ══════════════════════════════════════════ -->
    <div v-show="activeTab === 'watch'" class="tab-content">
      <div v-if="!watchList.length" class="empty-hint">
        <el-icon><Star /></el-icon>
        <span>暂无自选，<br>搜索合约后点击 ☆ 添加</span>
      </div>

      <div
        v-for="c in watchList"
        :key="c.symbol"
        class="contract-row"
        :class="{ active: currentSymbol?.symbol === c.symbol }"
        @click="emit('select', c)"
      >
        <div class="row-left">
          <span class="row-symbol">{{ c.symbol }}</span>
          <span class="row-name">{{ c.name }}</span>
        </div>
        <div class="row-right">
          <template v-if="getTick(c.symbol).last > 0">
            <span class="row-price" :class="tickDir(c.symbol)">
              {{ getTick(c.symbol).last }}
            </span>
            <span class="row-chg" :class="tickDir(c.symbol)">
              {{ fmtChg(getTick(c.symbol).changeRate) }}
            </span>
          </template>
          <span v-else class="row-price dim">--</span>
          <button class="star-btn" @click.stop="watchStore.removeFromWatchList(c.symbol)">
            <el-icon><StarFilled /></el-icon>
          </button>
        </div>
      </div>
    </div>

    <!-- ══════════════════════════════════════════
         最近 Tab
    ══════════════════════════════════════════ -->
    <div v-show="activeTab === 'recent'" class="tab-content">
      <div v-if="!recentSymbols.length" class="empty-hint">
        <el-icon><Clock /></el-icon>
        <span>暂无浏览历史</span>
      </div>

      <div
        v-for="c in recentSymbols"
        :key="c.symbol"
        class="contract-row"
        :class="{ active: currentSymbol?.symbol === c.symbol }"
        @click="emit('select', c)"
      >
        <div class="row-left">
          <span class="row-symbol">{{ c.symbol }}</span>
          <span class="row-name">{{ c.name }}</span>
          <span class="row-count">{{ visitCounts[c.symbol] ?? 1 }} 次</span>
        </div>
        <div class="row-right">
          <template v-if="getTick(c.symbol).last > 0">
            <span class="row-price" :class="tickDir(c.symbol)">
              {{ getTick(c.symbol).last }}
            </span>
            <span class="row-chg" :class="tickDir(c.symbol)">
              {{ fmtChg(getTick(c.symbol).changeRate) }}
            </span>
          </template>
          <span v-else class="row-price dim">--</span>
          <button class="star-btn" :class="{ starred: watchStore.isWatched(c.symbol) }"
            @click.stop="watchStore.toggleWatchList(c)">
            <el-icon><StarFilled v-if="watchStore.isWatched(c.symbol)" /><Star v-else /></el-icon>
          </button>
        </div>
      </div>
    </div>

    <!-- ══════════════════════════════════════════
         板块 Tab
    ══════════════════════════════════════════ -->
    <div v-show="activeTab === 'sector'" class="tab-content">
      <!-- 板块分类按钮 -->
      <div class="sector-chips">
        <button
          v-for="s in SECTORS"
          :key="s"
          class="sector-chip"
          :class="{ active: activeSector === s }"
          @click="activeSector = s"
        >{{ s }}</button>
      </div>

      <div
        v-for="c in sectorContracts"
        :key="c.symbol"
        class="contract-row"
        :class="{ active: currentSymbol?.symbol === c.symbol }"
        @click="emit('select', c)"
      >
        <div class="row-left">
          <span class="row-symbol">{{ c.symbol }}</span>
          <span class="row-name">{{ c.name }}</span>
          <el-tag size="small" :type="exchType(c.exchange)" style="font-size:10px;padding:0 4px">
            {{ c.exchange }}
          </el-tag>
        </div>
        <div class="row-right">
          <template v-if="getTick(c.symbol).last > 0">
            <span class="row-price" :class="tickDir(c.symbol)">
              {{ getTick(c.symbol).last }}
            </span>
            <span class="row-chg" :class="tickDir(c.symbol)">
              {{ fmtChg(getTick(c.symbol).changeRate) }}
            </span>
          </template>
          <span v-else class="row-price dim">--</span>
          <button class="star-btn" :class="{ starred: watchStore.isWatched(c.symbol) }"
            @click.stop="watchStore.toggleWatchList(c)">
            <el-icon><StarFilled v-if="watchStore.isWatched(c.symbol)" /><Star v-else /></el-icon>
          </button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, inject } from 'vue'
import { storeToRefs } from 'pinia'
import { Star, StarFilled, Clock } from '@element-plus/icons-vue'
import { useWatchStore } from '@/stores/watch.js'
import { useHistoryStore } from '@/stores/history.js'
import { HOT_CONTRACTS, EXCH_COLOR } from '@/composables/useContractSearch.js'

// ── Emits ─────────────────────────────────────────────────────────────────
const emit = defineEmits(['select'])

// ── Stores ────────────────────────────────────────────────────────────────
const watchStore   = useWatchStore()
const historyStore = useHistoryStore()
const { currentSymbol, watchList } = storeToRefs(watchStore)
const { recentSymbols, visitCounts } = storeToRefs(historyStore)

// ── WS（从父组件注入） ─────────────────────────────────────────────────────
const watchWs = inject('watchWs')
function getTick(symbol) {
  return watchWs?.getTick(symbol) ?? { last: 0, changeRate: 0 }
}

// ── Tab 状态 ──────────────────────────────────────────────────────────────
const TABS = [
  { key: 'watch',  label: '自选' },
  { key: 'recent', label: '最近' },
  { key: 'sector', label: '板块' },
]
const activeTab = ref('watch')

// ── 板块 ──────────────────────────────────────────────────────────────────
const ALL_CONTRACTS = HOT_CONTRACTS
const SECTORS = ['全部', ...new Set(ALL_CONTRACTS.map(c => c.product_type))]
const activeSector = ref('全部')

const sectorContracts = computed(() =>
  activeSector.value === '全部'
    ? ALL_CONTRACTS
    : ALL_CONTRACTS.filter(c => c.product_type === activeSector.value)
)

// ── 辅助函数 ──────────────────────────────────────────────────────────────
function tickDir(symbol) {
  const r = getTick(symbol).changeRate ?? 0
  return r > 0 ? 'up' : r < 0 ? 'down' : ''
}

function fmtChg(rate) {
  if (rate == null) return '--'
  return `${rate >= 0 ? '+' : ''}${rate.toFixed(2)}%`
}

const EXCH_TAG_MAP = {
  SHFE:  'danger',
  DCE:   'warning',
  CZCE:  'success',
  CFFEX: 'primary',
  INE:   'info',
  GFEX:  'info',
}
function exchType(exch) { return EXCH_TAG_MAP[exch] ?? 'info' }
</script>

<style scoped>
.ws-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0d0d1a;
  border-right: 1px solid #1e1e2e;
  overflow: hidden;
  user-select: none;
}

/* ── Tab 栏 ──────────────────────────────────────────────────────────────── */
.tab-bar {
  display: flex;
  border-bottom: 1px solid #1e1e2e;
  flex-shrink: 0;
}

.tab-btn {
  flex: 1;
  padding: 9px 0;
  background: transparent;
  border: none;
  color: #666;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.tab-btn:hover { color: #aaa; }
.tab-btn.active {
  color: #3b82f6;
  border-bottom-color: #3b82f6;
}

/* ── Tab 内容 ────────────────────────────────────────────────────────────── */
.tab-content {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
  scrollbar-width: thin;
  scrollbar-color: #2a2a3e transparent;
}

.tab-content::-webkit-scrollbar { width: 4px; }
.tab-content::-webkit-scrollbar-thumb { background: #2a2a3e; border-radius: 2px; }

/* ── 合约行 ──────────────────────────────────────────────────────────────── */
.contract-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 10px;
  cursor: pointer;
  transition: background 0.1s;
  border-bottom: 1px solid #131320;
  gap: 6px;
}

.contract-row:hover { background: #161628; }
.contract-row.active { background: rgba(59,130,246,0.1); }

.row-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.row-symbol {
  font-size: 13px;
  font-weight: 600;
  color: #e2e2e2;
  white-space: nowrap;
}

.row-name {
  font-size: 11px;
  color: #666;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-count {
  font-size: 10px;
  color: #444;
}

.row-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  flex-shrink: 0;
}

.row-price {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #ccc;
}

.row-chg {
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  color: #666;
}

.row-price.up, .row-chg.up  { color: #ef4444; }
.row-price.down,.row-chg.down { color: #22c55e; }
.row-price.dim { color: #444; }

/* ── 收藏按钮 ────────────────────────────────────────────────────────────── */
.star-btn {
  background: transparent;
  border: none;
  color: #444;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
  line-height: 1;
  transition: color 0.15s;
  opacity: 0;
}

.contract-row:hover .star-btn { opacity: 1; }
.star-btn.starred { opacity: 1; color: #f59e0b; }
.star-btn:hover { color: #f59e0b; }

/* ── 空状态 ──────────────────────────────────────────────────────────────── */
.empty-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 40px 20px;
  color: #444;
  font-size: 12px;
  text-align: center;
  line-height: 1.8;
}

.empty-hint .el-icon { font-size: 28px; }

/* ── 板块chips ───────────────────────────────────────────────────────────── */
.sector-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 10px;
  border-bottom: 1px solid #1a1a2a;
}

.sector-chip {
  padding: 2px 8px;
  background: transparent;
  border: 1px solid #2a2a3a;
  border-radius: 10px;
  font-size: 11px;
  color: #777;
  cursor: pointer;
  transition: all 0.15s;
}

.sector-chip:hover { border-color: #3b82f6; color: #3b82f6; }
.sector-chip.active {
  background: rgba(59,130,246,0.12);
  border-color: #3b82f6;
  color: #3b82f6;
}
</style>
