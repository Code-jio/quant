<template>
  <el-dialog
    :model-value="modelValue"
    width="700px"
    :show-close="false"
    class="cs-dialog"
    top="8vh"
    @update:model-value="emit('update:modelValue', $event)"
    @opened="onOpened"
    @closed="onClosed"
  >
    <!-- ── 顶部搜索栏 ──────────────────────────────────────────────────── -->
    <div class="cs-header">
      <el-input
        ref="inputRef"
        v-model="query"
        size="large"
        placeholder="搜索期货合约：代码、中文名称、拼音首字母…"
        clearable
        class="cs-input"
        @keydown="e => handleKeydown(e, selectContract)"
        @clear="clearSearch"
      >
        <template #prefix>
          <el-icon :size="18" class="search-icon"><Search /></el-icon>
        </template>
      </el-input>

      <el-button
        circle text
        class="cs-close"
        @click="emit('update:modelValue', false)"
      >
        <el-icon :size="16"><Close /></el-icon>
      </el-button>
    </div>

    <!-- ── 交易所筛选条 ───────────────────────────────────────────────── -->
    <div class="cs-exch-bar">
      <button
        class="exch-btn"
        :class="{ active: exchange === '' }"
        @click="exchange = ''"
      >全部</button>
      <button
        v-for="ex in EXCHANGES"
        :key="ex"
        class="exch-btn"
        :class="[`exch-${ex.toLowerCase()}`, { active: exchange === ex }]"
        @click="exchange = ex"
      >{{ ex }}</button>
    </div>

    <!-- ── 无搜索词：热门 / 最近 / 收藏 ──────────────────────────────── -->
    <div v-if="!query.trim() && !exchange" class="cs-discover">

      <!-- 热门品种 -->
      <div class="discover-section">
        <div class="ds-label">
          <span class="ds-icon">🔥</span>热门品种
        </div>
        <div class="hot-grid">
          <button
            v-for="c in HOT_CONTRACTS"
            :key="c.symbol"
            class="hot-chip"
            :style="{ '--chip-color': typeColor(c.product_type) }"
            @click="selectContract(c)"
          >
            <span class="chip-sym">{{ c.symbol }}</span>
            <span class="chip-name">{{ c.name }}</span>
          </button>
        </div>
      </div>

      <!-- 最近查看 -->
      <div v-if="recent.length" class="discover-section">
        <div class="ds-label">
          <span class="ds-icon">🕒</span>最近查看
          <button class="ds-clear" @click="recent.splice(0)">清空</button>
        </div>
        <div class="mini-list">
          <div
            v-for="c in recent"
            :key="c.symbol"
            class="mini-row"
            @click="selectContract(c)"
          >
            <span class="mr-sym">{{ c.symbol }}</span>
            <span class="mr-name">{{ c.name }}</span>
            <span class="mr-exch" :class="`ec-${c.exchange?.toLowerCase()}`">{{ c.exchange }}</span>
            <span class="mr-type" :style="{ color: typeColor(c.product_type) }">{{ c.product_type }}</span>
            <span
              class="mr-star"
              :class="{ starred: isFavorite(c.symbol) }"
              @click.stop="toggleFavorite(c)"
              title="收藏"
            >{{ isFavorite(c.symbol) ? '★' : '☆' }}</span>
            <span
              class="mr-del"
              @click.stop="removeRecent(c.symbol)"
              title="移除"
            >×</span>
          </div>
        </div>
      </div>

      <!-- 我的收藏 -->
      <div v-if="favorites.length" class="discover-section">
        <div class="ds-label">
          <span class="ds-icon">⭐</span>我的收藏
          <span class="ds-count">({{ favorites.length }})</span>
        </div>
        <div class="mini-list">
          <div
            v-for="c in favorites"
            :key="c.symbol"
            class="mini-row"
            @click="selectContract(c)"
          >
            <span class="mr-sym">{{ c.symbol }}</span>
            <span class="mr-name">{{ c.name }}</span>
            <span class="mr-exch" :class="`ec-${c.exchange?.toLowerCase()}`">{{ c.exchange }}</span>
            <span class="mr-type" :style="{ color: typeColor(c.product_type) }">{{ c.product_type }}</span>
            <span
              class="mr-star starred"
              @click.stop="toggleFavorite(c)"
              title="取消收藏"
            >★</span>
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="!recent.length && !favorites.length" class="cs-empty-tip">
        输入代码、中文名称或拼音首字母开始搜索
      </div>
    </div>

    <!-- ── 搜索结果 ──────────────────────────────────────────────────── -->
    <div v-else class="cs-results">

      <!-- 加载中 -->
      <div v-if="loading" class="cs-loading">
        <div class="spinner"></div>
        <span>搜索中…</span>
      </div>

      <!-- 错误 -->
      <div v-else-if="error" class="cs-error">
        <el-icon><WarningFilled /></el-icon> {{ error }}
      </div>

      <!-- 空结果 -->
      <div v-else-if="!results.length" class="cs-no-result">
        <el-empty description="未找到匹配合约" :image-size="72" />
      </div>

      <!-- 结果表格 -->
      <template v-else>
        <div class="result-header">
          <span class="result-count">找到 <em>{{ results.length }}</em> 个合约</span>
          <span class="result-hint">↑ ↓ 导航 &nbsp;·&nbsp; Enter 确认 &nbsp;·&nbsp; Esc 清空</span>
        </div>

        <div class="result-table-wrap" ref="tableWrapRef">
          <table class="result-table">
            <thead>
              <tr>
                <th style="width:110px">代码</th>
                <th>名称</th>
                <th style="width:80px">交易所</th>
                <th style="width:80px">品种</th>
                <th style="width:52px;text-align:center">收藏</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, idx) in results"
                :key="row.symbol"
                :ref="el => setRowRef(el, idx)"
                class="result-row"
                :class="{ 'row-active': idx === selectedIndex }"
                @click="selectContract(row)"
                @mouseenter="selectedIndex = idx"
              >
                <td>
                  <span class="rs-sym">{{ row.symbol }}</span>
                </td>
                <td>
                  <span class="rs-name">{{ row.name }}</span>
                </td>
                <td>
                  <span class="rs-exch" :class="`ec-${row.exchange?.toLowerCase()}`">
                    {{ row.exchange }}
                  </span>
                </td>
                <td>
                  <span class="rs-type" :style="{ color: typeColor(row.product_type) }">
                    {{ row.product_type }}
                  </span>
                </td>
                <td style="text-align:center">
                  <span
                    class="star-btn"
                    :class="{ starred: isFavorite(row.symbol) }"
                    @click.stop="toggleFavorite(row)"
                    :title="isFavorite(row.symbol) ? '取消收藏' : '添加收藏'"
                  >{{ isFavorite(row.symbol) ? '★' : '☆' }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { Search, Close, WarningFilled } from '@element-plus/icons-vue'
import {
  useContractSearch,
  EXCHANGES, HOT_CONTRACTS, TYPE_COLOR,
} from '../composables/useContractSearch.js'

// ── Props & Emits ────────────────────────────────────────────────────────
const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'select'])

// ── Composable ───────────────────────────────────────────────────────────
const {
  query, exchange, results, loading, error, selectedIndex,
  favorites, recent,
  isFavorite, toggleFavorite, addToRecent, removeRecent,
  handleKeydown, clearSearch,
} = useContractSearch()

// ── Refs ─────────────────────────────────────────────────────────────────
const inputRef    = ref(null)
const tableWrapRef = ref(null)
const rowRefs     = []

function setRowRef(el, idx) {
  rowRefs[idx] = el
}

// ── 对话框生命周期 ────────────────────────────────────────────────────────
function onOpened() {
  nextTick(() => inputRef.value?.focus())
}
function onClosed() {
  clearSearch()
  exchange.value = ''
}

// ── 选中合约 ──────────────────────────────────────────────────────────────
function selectContract(contract) {
  addToRecent(contract)
  emit('select', contract)
  emit('update:modelValue', false)
}

// ── 颜色辅助 ─────────────────────────────────────────────────────────────
function typeColor(type) {
  return TYPE_COLOR[type] ?? '#8b949e'
}

// ── 键盘选中后自动滚动到可见 ────────────────────────────────────────────
watch(selectedIndex, (idx) => {
  if (idx < 0) return
  nextTick(() => {
    const el = rowRefs[idx]
    if (el && tableWrapRef.value) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  })
})
</script>

<style scoped>
/* ── Dialog 覆盖 ──────────────────────────────────────────────────────── */
:deep(.cs-dialog) {
  --el-dialog-bg-color: #0d1117;
  --el-dialog-padding-primary: 0;
  border: 1px solid #30363d;
  border-radius: 12px;
  overflow: hidden;
}
:deep(.cs-dialog .el-dialog__body) {
  padding: 0;
}

/* ── 搜索头 ──────────────────────────────────────────────────────────── */
.cs-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 16px 12px;
  border-bottom: 1px solid #21262d;
  background: #0d1117;
}
.cs-input {
  flex: 1;
}
:deep(.cs-input .el-input__wrapper) {
  background: #161b22;
  border-color: #30363d;
  border-radius: 8px;
  box-shadow: none;
}
:deep(.cs-input .el-input__wrapper:hover),
:deep(.cs-input .el-input__wrapper.is-focus) {
  border-color: #58a6ff;
  box-shadow: 0 0 0 3px rgba(88,166,255,.15);
}
:deep(.cs-input .el-input__inner) {
  color: #c9d1d9;
  font-size: 15px;
}
:deep(.cs-input .el-input__inner::placeholder) {
  color: #484f58;
}
.search-icon { color: #58a6ff; }
.cs-close {
  flex-shrink: 0;
  color: #8b949e;
  background: transparent;
  border: none;
  cursor: pointer;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background .15s;
}
.cs-close:hover { background: #21262d; color: #c9d1d9; }

/* ── 交易所筛选条 ─────────────────────────────────────────────────────── */
.cs-exch-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  background: #0d1117;
  border-bottom: 1px solid #21262d;
  flex-wrap: wrap;
}
.exch-btn {
  padding: 3px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid #30363d;
  background: transparent;
  color: #8b949e;
  transition: all .15s;
  letter-spacing: .5px;
}
.exch-btn:hover { border-color: #58a6ff; color: #58a6ff; }
.exch-btn.active { color: #0d1117; border-color: transparent; }

/* 各交易所配色 */
.exch-btn.active,
.exch-btn:not(.active):hover { }

.exch-btn.exch-shfe.active  { background: #f85149; border-color: #f85149; }
.exch-btn.exch-dce.active   { background: #d29922; border-color: #d29922; }
.exch-btn.exch-czce.active  { background: #3fb950; border-color: #3fb950; }
.exch-btn.exch-cffex.active { background: #58a6ff; border-color: #58a6ff; }
.exch-btn.exch-ine.active   { background: #8b949e; border-color: #8b949e; }
.exch-btn.exch-gfex.active  { background: #a371f7; border-color: #a371f7; }
.exch-btn:first-child.active { background: #58a6ff; }

/* ── 发现区（热门/最近/收藏）─────────────────────────────────────────── */
.cs-discover {
  max-height: 480px;
  overflow-y: auto;
  padding: 4px 0 8px;
}
.discover-section {
  padding: 12px 16px 4px;
}
.discover-section + .discover-section {
  border-top: 1px solid #21262d;
}
.ds-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: .8px;
  margin-bottom: 10px;
}
.ds-icon { font-size: 13px; }
.ds-count { color: #484f58; font-weight: 400; }
.ds-clear {
  margin-left: auto;
  font-size: 11px;
  color: #484f58;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.ds-clear:hover { color: #8b949e; }

/* 热门品种网格 */
.hot-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.hot-chip {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 7px 12px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  cursor: pointer;
  transition: all .15s;
  position: relative;
  min-width: 90px;
}
.hot-chip::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  border-radius: 8px 0 0 8px;
  background: var(--chip-color, #58a6ff);
}
.hot-chip:hover {
  border-color: var(--chip-color, #58a6ff);
  background: #1c2128;
  transform: translateY(-1px);
}
.chip-sym {
  font-size: 13px;
  font-weight: 600;
  color: #c9d1d9;
  font-family: var(--q-font-mono, monospace);
}
.chip-name {
  font-size: 11px;
  color: #8b949e;
  margin-top: 2px;
}

/* 迷你列表（最近/收藏） */
.mini-list { display: flex; flex-direction: column; gap: 2px; }
.mini-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background .12s;
}
.mini-row:hover { background: #161b22; }
.mr-sym  { font-size: 13px; font-weight: 600; color: #c9d1d9; width: 80px; flex-shrink: 0; font-family: var(--q-font-mono, monospace); }
.mr-name { font-size: 13px; color: #adbac7; flex: 1; }
.mr-exch { font-size: 11px; font-weight: 600; width: 50px; flex-shrink: 0; }
.mr-type { font-size: 11px; width: 46px; flex-shrink: 0; }
.mr-star {
  font-size: 16px;
  color: #484f58;
  cursor: pointer;
  transition: color .12s, transform .12s;
  flex-shrink: 0;
}
.mr-star:hover { color: #d29922; transform: scale(1.2); }
.mr-star.starred { color: #d29922; }
.mr-del {
  font-size: 14px;
  color: #484f58;
  cursor: pointer;
  flex-shrink: 0;
  width: 16px;
  text-align: center;
  line-height: 1;
}
.mr-del:hover { color: #f85149; }

/* 空状态提示 */
.cs-empty-tip {
  text-align: center;
  color: #484f58;
  font-size: 13px;
  padding: 40px 0;
}

/* ── 搜索结果 ──────────────────────────────────────────────────────────── */
.cs-results {
  min-height: 120px;
  max-height: 480px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.cs-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 100px;
  color: #58a6ff;
  font-size: 14px;
}
.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid #21262d;
  border-top-color: #58a6ff;
  border-radius: 50%;
  animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.cs-error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #f85149;
  font-size: 13px;
  padding: 20px 16px;
}
.cs-no-result {
  padding: 20px 0 10px;
}

/* 结果表格 header */
.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px 6px;
  background: #0d1117;
  border-bottom: 1px solid #21262d;
  flex-shrink: 0;
}
.result-count {
  font-size: 12px;
  color: #8b949e;
}
.result-count em {
  font-style: normal;
  color: #58a6ff;
  font-weight: 600;
}
.result-hint {
  font-size: 11px;
  color: #484f58;
}

/* 自定义滚动表格 */
.result-table-wrap {
  flex: 1;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: #30363d transparent;
}
.result-table-wrap::-webkit-scrollbar { width: 4px; }
.result-table-wrap::-webkit-scrollbar-track { background: transparent; }
.result-table-wrap::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }

.result-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.result-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #161b22;
}
.result-table th {
  padding: 8px 12px;
  color: #8b949e;
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .5px;
  text-align: left;
  border-bottom: 1px solid #30363d;
}
.result-table td {
  padding: 9px 12px;
  border-bottom: 1px solid #1c2128;
  vertical-align: middle;
}
.result-row {
  cursor: pointer;
  transition: background .1s;
}
.result-row:hover,
.result-row.row-active {
  background: #1c2128;
}
.result-row.row-active {
  outline: none;
}
.result-row.row-active td:first-child {
  box-shadow: inset 3px 0 0 #58a6ff;
}

/* 单元格内容 */
.rs-sym  { font-family: var(--q-font-mono, monospace); font-weight: 700; font-size: 14px; color: #e6edf3; }
.rs-name { color: #adbac7; }
.rs-type { font-size: 12px; font-weight: 500; }

/* 交易所徽标 */
.rs-exch {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .5px;
}

/* 各交易所配色（共用 mr-exch 和 rs-exch） */
.ec-shfe  { background: rgba(248,81,73,.15);  color: #f85149; }
.ec-dce   { background: rgba(210,153,34,.15); color: #d29922; }
.ec-czce  { background: rgba(63,185,80,.15);  color: #3fb950; }
.ec-cffex { background: rgba(88,166,255,.15); color: #58a6ff; }
.ec-ine   { background: rgba(139,148,158,.15);color: #8b949e; }
.ec-gfex  { background: rgba(163,113,247,.15);color: #a371f7; }

/* 星标按钮 */
.star-btn {
  font-size: 17px;
  color: #484f58;
  cursor: pointer;
  transition: color .12s, transform .12s;
  display: inline-block;
}
.star-btn:hover { color: #d29922; transform: scale(1.2); }
.star-btn.starred { color: #d29922; }

/* ── 滚动条 ─────────────────────────────────────────────────────────── */
.cs-discover::-webkit-scrollbar { width: 4px; }
.cs-discover::-webkit-scrollbar-track { background: transparent; }
.cs-discover::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }
</style>
