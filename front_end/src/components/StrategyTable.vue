<script setup>
/**
 * StrategyTable.vue — 策略状态表格
 *
 * Props:
 *   strategies: Array  — 策略列表（来自 GET /strategies）
 *   loading: Boolean   — 外部加载状态
 *
 * Emits:
 *   refresh            — 操作完成后通知父组件刷新数据
 */
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { sendStrategyAction } from '@/api/index.js'

// ── Props & Emits ─────────────────────────────────────────────────────────
const props = defineProps({
  strategies: { type: Array,   default: () => [] },
  loading:    { type: Boolean, default: false     },
})
const emit = defineEmits(['refresh'])

// ── 状态配置 ──────────────────────────────────────────────────────────────
const STATUS_MAP = {
  running:    { type: 'success', label: '运行中' },
  stopped:    { type: 'info',    label: '已停止' },
  connecting: { type: 'warning', label: '连接中' },
  error:      { type: 'danger',  label: '错误'   },
}
const getStatus = (s) => STATUS_MAP[s] ?? STATUS_MAP.stopped

// ── 每行按钮的独立加载状态 ────────────────────────────────────────────────
const actionLoading = ref({})

// ── 操作处理 ──────────────────────────────────────────────────────────────
async function handleAction(row) {
  const isRunning = row.status === 'running'
  const action    = isRunning ? 'stop' : 'start'

  // 停止前弹确认框
  if (isRunning) {
    try {
      await ElMessageBox.confirm(
        `确认要停止策略「${row.name}」（${row.strategy_id}）吗？`,
        '停止策略',
        {
          confirmButtonText: '确认停止',
          cancelButtonText:  '取消',
          type:              'warning',
          confirmButtonClass: 'el-button--danger',
        },
      )
    } catch {
      return  // 用户取消
    }
  }

  actionLoading.value[row.strategy_id] = true
  try {
    const res = await sendStrategyAction(row.strategy_id, action)
    ElMessage.success(res.message ?? `策略已${action === 'start' ? '启动' : '停止'}`)
    emit('refresh')
  } catch (err) {
    ElMessage.error(`操作失败：${err.message}`)
  } finally {
    actionLoading.value[row.strategy_id] = false
  }
}

// ── 数字格式化 ────────────────────────────────────────────────────────────
function fmtPnl(val) {
  const n = Number(val) || 0
  const abs = n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (n >= 0 ? '+' : '−') + abs.replace('-', '')
}
function pnlClass(val) {
  const n = Number(val) || 0
  return n > 0 ? 'c-green' : n < 0 ? 'c-red' : 'c-muted'
}

// 行级 className（错误行加红色高亮）
function rowClassName({ row }) {
  return row.status === 'error' ? 'row-error' : ''
}
</script>

<template>
  <div class="strategy-table">
    <el-table
      :data="strategies"
      v-loading="loading"
      :row-class-name="rowClassName"
      stripe
      style="width: 100%"
    >
      <!-- 策略 ID -->
      <el-table-column label="策略 ID" width="150">
        <template #default="{ row }">
          <span class="mono c-muted">{{ row.strategy_id }}</span>
        </template>
      </el-table-column>

      <!-- 策略名 / 合约 -->
      <el-table-column label="策略 / 合约" min-width="130">
        <template #default="{ row }">
          <div class="name-cell">
            <span class="strategy-name">{{ row.name }}</span>
            <el-tag v-if="row.symbol" type="warning" size="small" effect="plain" class="symbol-tag">
              {{ row.symbol }}
            </el-tag>
          </div>
        </template>
      </el-table-column>

      <!-- 状态 -->
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag
            :type="getStatus(row.status).type"
            size="small"
            effect="dark"
          >
            {{ getStatus(row.status).label }}
          </el-tag>
        </template>
      </el-table-column>

      <!-- 实时盈亏 -->
      <el-table-column label="实时盈亏 (¥)" width="155" align="right">
        <template #default="{ row }">
          <span :class="['pnl-val', 'mono', pnlClass(row.pnl)]">
            {{ fmtPnl(row.pnl) }}
          </span>
        </template>
      </el-table-column>

      <!-- 成交次数 -->
      <el-table-column label="成交" width="72" align="center">
        <template #default="{ row }">
          <span class="mono c-blue">{{ row.trade_count }}</span>
        </template>
      </el-table-column>

      <!-- 持仓摘要 -->
      <el-table-column label="持仓" min-width="180">
        <template #default="{ row }">
          <template v-if="row.positions && row.positions.length">
            <el-tag
              v-for="pos in row.positions"
              :key="pos.symbol + pos.direction"
              :type="pos.direction === 'long' ? 'success' : 'danger'"
              size="small"
              effect="plain"
              class="pos-tag"
            >
              {{ pos.symbol }}
              {{ pos.direction === 'long' ? '多' : '空' }}
              {{ Math.abs(pos.volume) }}手
              <span v-if="pos.pnl !== 0" :class="pnlClass(pos.pnl)">
                {{ pos.pnl >= 0 ? '+' : '' }}{{ (pos.pnl).toFixed(0) }}
              </span>
            </el-tag>
          </template>
          <span v-else class="c-muted empty-pos">— 空仓 —</span>
        </template>
      </el-table-column>

      <!-- 操作 -->
      <el-table-column label="操作" width="110" align="center" fixed="right">
        <template #default="{ row }">
          <el-button
            :type="row.status === 'running' ? 'danger' : 'primary'"
            :loading="!!actionLoading[row.strategy_id]"
            :disabled="row.status === 'connecting'"
            size="small"
            round
            @click="handleAction(row)"
          >
            <template v-if="!actionLoading[row.strategy_id]">
              <el-icon v-if="row.status === 'running'"><VideoPause /></el-icon>
              <el-icon v-else><VideoPlay /></el-icon>
              {{ row.status === 'running' ? '停止' : '启动' }}
            </template>
          </el-button>
        </template>
      </el-table-column>

      <!-- 空状态插槽 -->
      <template #empty>
        <div class="empty-state">
          <el-icon size="40" class="c-muted"><DataLine /></el-icon>
          <p class="c-muted" style="margin: 8px 0 0">暂无策略数据</p>
        </div>
      </template>
    </el-table>
  </div>
</template>

<style scoped>
.strategy-table {
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--q-border);
}

/* 字体 */
.mono { font-family: var(--q-font-mono); font-variant-numeric: tabular-nums; }

/* 颜色工具类 */
.c-green { color: var(--q-green); }
.c-red   { color: var(--q-red);   }
.c-blue  { color: var(--q-blue);  }
.c-muted { color: var(--q-muted); }

/* P&L 数字 */
.pnl-val { font-size: 13px; font-weight: 700; }

/* 策略名称单元格 */
.name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.strategy-name { font-weight: 600; }
.symbol-tag    { font-family: var(--q-font-mono); font-size: 11px; }

/* 持仓标签 */
.pos-tag { margin: 2px 3px; font-family: var(--q-font-mono); font-size: 11px; }
.empty-pos { font-size: 12px; }

/* 错误行高亮 */
:deep(.row-error td) {
  background: rgba(248, 81, 73, 0.06) !important;
}
:deep(.row-error:hover td) {
  background: rgba(248, 81, 73, 0.1) !important;
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 0;
}
</style>
