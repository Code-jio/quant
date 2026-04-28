<template>
  <div class="log-viewer">

    <!-- ── 工具栏 ──────────────────────────────────────────────────── -->
    <div class="log-toolbar">
      <div class="toolbar-left">
        <!-- 级别过滤 -->
        <el-select
          v-model="levelFilter"
          size="small"
          style="width: 110px"
          @change="applyFilter"
        >
          <el-option label="全部级别" value="" />
          <el-option label="ERROR"    value="ERROR" />
          <el-option label="WARNING"  value="WARNING" />
          <el-option label="INFO"     value="INFO" />
          <el-option label="DEBUG"    value="DEBUG" />
        </el-select>

        <!-- 关键词搜索 -->
        <el-input
          v-model="keyword"
          size="small"
          placeholder="搜索关键词…"
          clearable
          style="width: 220px"
          @input="applyFilter"
          @clear="applyFilter"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>

        <!-- 条数统计 -->
        <span class="log-count c-muted">
          显示 {{ filteredLogs.length }} /
          {{ logs.length }} 条
          <span v-if="logs.length >= 500" class="warn-text">（已达上限）</span>
        </span>
      </div>

      <div class="toolbar-right">
        <!-- 自动滚动 -->
        <label class="switch-label c-muted">
          <el-switch v-model="autoScroll" size="small" />
          自动滚动
        </label>

        <!-- 暂停 -->
        <el-button
          :type="paused ? 'warning' : ''"
          size="small"
          plain
          @click="togglePause"
        >
          <el-icon>
            <VideoPause v-if="!paused" />
            <VideoPlay  v-else />
          </el-icon>
          {{ paused ? '恢复' : '暂停' }}
        </el-button>

        <!-- 清空 -->
        <el-button size="small" plain @click="clearLogs">
          <el-icon><Delete /></el-icon>
          清空
        </el-button>

        <!-- WS 状态 -->
        <span class="ws-status">
          <span :class="['ws-dot', wsConnected ? 'conn-ok' : 'conn-off']"></span>
          <span class="c-muted">{{ wsConnected ? '实时' : '断开' }}</span>
        </span>
      </div>
    </div>

    <!-- ── 级别快速筛选标签 ────────────────────────────────────────── -->
    <div class="level-tabs">
      <button
        v-for="tab in LEVEL_TABS"
        :key="tab.value"
        :class="['level-tab', levelFilter === tab.value && 'active', `tab-${tab.value || 'all'}`]"
        @click="levelFilter = tab.value; applyFilter()"
      >
        {{ tab.label }}
        <span class="tab-count">{{ levelCounts[tab.value] ?? levelCounts[''] }}</span>
      </button>
    </div>

    <!-- ── 日志列表 ───────────────────────────────────────────────── -->
    <div
      ref="listRef"
      class="log-list"
      @scroll="onScroll"
    >
      <div
        v-for="(entry, idx) in filteredLogs"
        :key="idx"
        :class="['log-entry', `level-${entry.level}`]"
      >
        <span class="log-ts">{{ entry.ts?.substring(11, 23) ?? '' }}</span>
        <span :class="['log-level', `lv-${entry.level}`]">{{ entry.level.padEnd(8) }}</span>
        <span class="log-name">{{ shortName(entry.name) }}</span>
        <span class="log-msg">{{ entry.message }}</span>
      </div>

      <!-- 空状态 -->
      <div v-if="filteredLogs.length === 0" class="log-empty">
        <el-icon size="32"><Document /></el-icon>
        <p>{{ wsConnected ? '暂无日志' : '等待连接…' }}</p>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { Search, Delete, VideoPause, VideoPlay, Document } from '@element-plus/icons-vue'
import { useLogsWs } from '@/composables/useLogsWs.js'

// ── WebSocket 数据 ─────────────────────────────────────────────────────
const { connected: wsConnected, logs, paused, pause, resume, clear } = useLogsWs()

// ── 本地过滤状态 ──────────────────────────────────────────────────────
const levelFilter  = ref('')
const keyword      = ref('')
const autoScroll   = ref(true)
const listRef      = ref(null)

const LEVEL_TABS = [
  { label: '全部',    value: '' },
  { label: 'ERROR',   value: 'ERROR' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'INFO',    value: 'INFO' },
  { label: 'DEBUG',   value: 'DEBUG' },
]

// ── 计算过滤后日志 ────────────────────────────────────────────────────
const filteredLogs = computed(() => {
  let list = logs.value
  if (levelFilter.value) {
    list = list.filter(e => e.level === levelFilter.value)
  }
  if (keyword.value) {
    const kl = keyword.value.toLowerCase()
    list = list.filter(e =>
      e.message?.toLowerCase().includes(kl) ||
      e.name?.toLowerCase().includes(kl)
    )
  }
  return list
})

// 各级别条数（用于标签统计）
const levelCounts = computed(() => {
  const counts = { '': logs.value.length }
  for (const e of logs.value) {
    counts[e.level] = (counts[e.level] ?? 0) + 1
  }
  return counts
})

function applyFilter() { /* filteredLogs 是 computed，自动更新 */ }

function shortName(name) {
  if (!name) return ''
  const parts = name.split('.')
  // 最多显示后两级：src.api → src.api，uvicorn.access → uvicorn.access
  return parts.slice(-2).join('.')
}

// ── 自动滚动 ──────────────────────────────────────────────────────────
let userScrolled = false

function onScroll() {
  if (!listRef.value) return
  const { scrollTop, scrollHeight, clientHeight } = listRef.value
  // 距底部 40px 以内认为用户回到底部
  userScrolled = scrollHeight - scrollTop - clientHeight > 40
}

watch(filteredLogs, async () => {
  if (!autoScroll.value || userScrolled) return
  await nextTick()
  if (listRef.value) {
    listRef.value.scrollTop = listRef.value.scrollHeight
  }
})

// ── 控制 ─────────────────────────────────────────────────────────────
function togglePause() {
  paused.value ? resume() : pause()
}

function clearLogs() {
  clear()
  userScrolled = false
}

// 初始时滚到底
onMounted(async () => {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
})
</script>

<style scoped>
.log-viewer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
}

/* ── 工具栏 ──────────────────────────────────────────────────────── */
.log-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px;
  background: var(--bg-base, #0d1117);
  border: 1px solid var(--border-color, #30363d);
  border-radius: 8px;
}

.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.log-count   { font-size: 12px; white-space: nowrap; }
.warn-text   { color: #e3b341; }
.switch-label { display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }

.ws-status   { display: flex; align-items: center; gap: 4px; font-size: 12px; }
.ws-dot      { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.conn-ok     { background: #3fb950; box-shadow: 0 0 5px #3fb95066; }
.conn-off    { background: #f85149; }

/* ── 级别 Tabs ─────────────────────────────────────────────────── */
.level-tabs {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.level-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  border-radius: 6px;
  border: 1px solid var(--border-color, #30363d);
  background: var(--bg-surface, #161b22);
  color: var(--text-muted, #6e7681);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.level-tab:hover          { border-color: #58a6ff66; color: #c9d1d9; }
.level-tab.active         { background: #1f2937; color: #c9d1d9; border-color: #58a6ff; }
.level-tab.tab-ERROR      { border-color: #f8514966; }
.level-tab.tab-ERROR.active{ background: #2d1117; border-color: #f85149; color: #f85149; }
.level-tab.tab-WARNING.active { background: #2d2011; border-color: #e3b341; color: #e3b341; }
.level-tab.tab-INFO.active    { background: #0d2137; border-color: #58a6ff; color: #58a6ff; }
.level-tab.tab-DEBUG.active   { background: #1a1f2e; border-color: #6e7681; color: #8b949e; }

.tab-count {
  background: #21262d;
  border-radius: 10px;
  padding: 0 5px;
  font-size: 10px;
  color: var(--text-muted, #6e7681);
  min-width: 16px;
  text-align: center;
}

/* ── 日志列表 ──────────────────────────────────────────────────── */
.log-list {
  flex: 1;
  min-height: 320px;
  max-height: 480px;
  overflow-y: auto;
  background: #0a0e15;
  border: 1px solid var(--border-color, #30363d);
  border-radius: 8px;
  padding: 8px 0;
  font-family: 'JetBrains Mono', 'Cascadia Code', 'Fira Code', Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
}

.log-entry {
  display: flex;
  gap: 10px;
  padding: 2px 14px;
  border-left: 2px solid transparent;
  transition: background 0.1s;
}
.log-entry:hover             { background: #161b22; }
.log-entry.level-ERROR       { border-left-color: #f85149; background: rgba(248,81,73,0.04); }
.log-entry.level-WARNING     { border-left-color: #e3b341; background: rgba(227,179,65,0.03); }
.log-entry.level-CRITICAL    { border-left-color: #f85149; background: rgba(248,81,73,0.08); }

.log-ts   { color: #4a5568; white-space: nowrap; width: 88px; flex-shrink: 0; }
.log-level {
  white-space: pre;
  font-weight: 700;
  width: 60px;
  flex-shrink: 0;
}

.lv-ERROR    { color: #f85149; }
.lv-CRITICAL { color: #f85149; }
.lv-WARNING  { color: #e3b341; }
.lv-INFO     { color: #58a6ff; }
.lv-DEBUG    { color: #6e7681; }

.log-name {
  color: #6e7681;
  white-space: nowrap;
  width: 120px;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.log-msg {
  color: #c9d1d9;
  word-break: break-all;
  flex: 1;
}

/* ERROR / CRITICAL 消息加亮 */
.log-entry.level-ERROR .log-msg,
.log-entry.level-CRITICAL .log-msg { color: #ff7b72; }
.log-entry.level-WARNING .log-msg  { color: #d4a72c; }

/* 空状态 */
.log-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--text-muted, #6e7681);
  gap: 8px;
}

/* 滚动条 */
.log-list::-webkit-scrollbar       { width: 5px; }
.log-list::-webkit-scrollbar-track { background: #0a0e15; }
.log-list::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
.log-list::-webkit-scrollbar-thumb:hover { background: #484f58; }

/* Element Plus 覆盖 */
:deep(.el-input__wrapper) {
  background-color: var(--bg-base, #0d1117) !important;
  box-shadow: 0 0 0 1px var(--border-color, #30363d) inset !important;
}
:deep(.el-input__inner) { color: var(--text-primary, #c9d1d9); }
:deep(.el-select__wrapper) {
  background-color: var(--bg-base, #0d1117) !important;
  box-shadow: 0 0 0 1px var(--border-color, #30363d) inset !important;
  color: var(--text-primary, #c9d1d9);
}

.c-muted { color: var(--text-muted, #6e7681); }
</style>
