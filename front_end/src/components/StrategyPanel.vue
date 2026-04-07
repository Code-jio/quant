<script setup>
/**
 * StrategyPanel.vue — 策略管理面板
 *
 * 功能：
 *   - 卡片式策略列表（状态、实时信号、持仓、自定义参数）
 *   - 一键启停（带确认）
 *   - 参数编辑对话框（热更新 / 重启）
 *   - 权重分配对话框（滑块 + 归一化 + 自动均分）
 *
 * Props:
 *   strategies: Array   — 来自 GET /strategies 的列表
 *   loading:    Boolean — 外部加载状态
 *
 * Emits:
 *   refresh — 操作完成后通知父组件刷新
 */
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  sendStrategyAction,
  fetchStrategyDetail,
  updateStrategyParams,
  updateWeights,
} from '@/api/index.js'

// ── Props / Emits ────────────────────────────────────────────────────────────
const props = defineProps({
  strategies: { type: Array,   default: () => [] },
  loading:    { type: Boolean, default: false     },
})
const emit = defineEmits(['refresh'])

// ── 状态映射 ─────────────────────────────────────────────────────────────────
const STATUS_MAP = {
  running:    { type: 'success', label: '运行中', dot: 'dot-run'  },
  stopped:    { type: 'info',    label: '已停止', dot: 'dot-stop' },
  connecting: { type: 'warning', label: '连接中', dot: 'dot-warn' },
  error:      { type: 'danger',  label: '错误',   dot: 'dot-err'  },
}
const getStatus  = (s) => STATUS_MAP[s] ?? STATUS_MAP.stopped
const dirLabel   = (d) => d === 'long' ? '多' : '空'
const dirType    = (d) => d === 'long' ? 'success' : 'danger'
const commentCn  = (c) => {
  const MAP = { buy_open: '买入开多', sell_close: '卖出平多', short_open: '卖出开空', cover_close: '买入平空' }
  return MAP[c] ?? c
}

// ── 格式化 ───────────────────────────────────────────────────────────────────
const fmt = {
  money:  (v, d = 2) => (Number(v) || 0).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }),
  signed: (v) => { const n = Number(v) || 0; return (n >= 0 ? '+¥' : '-¥') + fmt.money(Math.abs(n)) },
  pct:    (v) => ((Number(v) || 0) * 100).toFixed(1) + '%',
}
const pnlCss = (v) => Number(v) > 0 ? 'c-green' : Number(v) < 0 ? 'c-red' : 'c-muted'

// ── 每张卡的详情缓存（信号列表 + 参数） ──────────────────────────────────────
const detailCache = reactive({})   // { [strategy_id]: StrategyDetailResponse }
const detailLoading = reactive({}) // { [strategy_id]: boolean }

async function loadDetail(strategyId) {
  detailLoading[strategyId] = true
  try {
    detailCache[strategyId] = await fetchStrategyDetail(strategyId)
  } catch { /* 静默 */ } finally {
    detailLoading[strategyId] = false
  }
}

// 展开卡片时按需加载详情
const expanded = reactive({}) // { [strategy_id]: boolean }
function toggleExpand(id) {
  expanded[id] = !expanded[id]
  if (expanded[id] && !detailCache[id]) loadDetail(id)
}

// 策略列表变化时刷新已展开项
watch(() => props.strategies, () => {
  for (const id in expanded) {
    if (expanded[id]) loadDetail(id)
  }
})

// ── 一键启停 ─────────────────────────────────────────────────────────────────
const actionLoading = reactive({})

async function handleAction(row) {
  const isRunning = row.status === 'running'
  const action    = isRunning ? 'stop' : 'start'

  if (isRunning) {
    try {
      await ElMessageBox.confirm(
        `确认停止策略「${row.name}」（${row.strategy_id}）？`,
        '停止确认',
        { confirmButtonText: '确认停止', cancelButtonText: '取消', type: 'warning' },
      )
    } catch { return }
  }

  actionLoading[row.strategy_id] = true
  try {
    const res = await sendStrategyAction(row.strategy_id, action)
    ElMessage.success(res.message ?? `策略已${action === 'start' ? '启动' : '停止'}`)
    emit('refresh')
    if (expanded[row.strategy_id]) loadDetail(row.strategy_id)
  } catch (err) {
    ElMessage.error(`操作失败：${err.message}`)
  } finally {
    actionLoading[row.strategy_id] = false
  }
}

// ── 参数编辑对话框 ────────────────────────────────────────────────────────────
const paramDialog = reactive({
  visible:    false,
  strategyId: '',
  name:       '',
  editParams: {},   // 副本（用于表单）
  restart:    false,
  saving:     false,
})

function openParamDialog(row) {
  const detail = detailCache[row.strategy_id]
  paramDialog.strategyId = row.strategy_id
  paramDialog.name       = row.name
  paramDialog.editParams = detail
    ? JSON.parse(JSON.stringify(detail.params))
    : JSON.parse(JSON.stringify(row.params ?? {}))
  paramDialog.restart  = false
  paramDialog.saving   = false
  paramDialog.visible  = true
}

function paramType(val) {
  if (typeof val === 'boolean') return 'boolean'
  if (typeof val === 'number')  return 'number'
  return 'string'
}

async function saveParams() {
  paramDialog.saving = true
  try {
    await updateStrategyParams(paramDialog.strategyId, paramDialog.editParams, paramDialog.restart)
    ElMessage.success('参数已更新' + (paramDialog.restart ? '，策略已重启' : ''))
    paramDialog.visible = false
    emit('refresh')
    loadDetail(paramDialog.strategyId)
  } catch (err) {
    ElMessage.error(`更新失败：${err.message}`)
  } finally {
    paramDialog.saving = false
  }
}

// ── 权重分配对话框 ────────────────────────────────────────────────────────────
const weightDialog = reactive({
  visible: false,
  weights: {},   // { [strategy_id]: number 0~100 （百分比） }
  saving:  false,
})

const weightTotal = computed(() =>
  Object.values(weightDialog.weights).reduce((s, v) => s + (Number(v) || 0), 0)
)

function openWeightDialog() {
  const ids = props.strategies.map(s => s.strategy_id)
  // 从 detailCache 取权重，否则均分
  const existing = {}
  for (const id of ids) {
    const w = detailCache[id]?.weight ?? (1 / Math.max(ids.length, 1))
    existing[id] = Math.round(w * 100)
  }
  weightDialog.weights = existing
  weightDialog.saving  = false
  weightDialog.visible = true
}

function autoEqualWeights() {
  const ids = Object.keys(weightDialog.weights)
  const each = Math.floor(100 / ids.length)
  const remainder = 100 - each * ids.length
  ids.forEach((id, i) => {
    weightDialog.weights[id] = each + (i === 0 ? remainder : 0)
  })
}

async function saveWeights() {
  if (Math.abs(weightTotal.value - 100) > 1) {
    ElMessage.warning(`权重总和必须为 100%，当前 ${weightTotal.value}%`)
    return
  }
  weightDialog.saving = true
  // 归一化到 0.0~1.0
  const normalized = {}
  for (const [id, w] of Object.entries(weightDialog.weights)) {
    normalized[id] = (Number(w) || 0) / 100
  }
  try {
    await updateWeights(normalized)
    ElMessage.success('权重分配已保存')
    weightDialog.visible = false
    // 刷新所有已展开卡片的详情
    for (const id in expanded) {
      if (expanded[id]) loadDetail(id)
    }
  } catch (err) {
    ElMessage.error(`保存失败：${err.message}`)
  } finally {
    weightDialog.saving = false
  }
}

// ── 辅助：信号方向颜色 ────────────────────────────────────────────────────────
function sigColor(dir) {
  return dir === 'long' ? 'var(--q-green)' : 'var(--q-red)'
}
function sigArrow(dir) {
  return dir === 'long' ? '↑' : '↓'
}
</script>

<template>
  <div class="sp-wrap">

    <!-- ── 面板头部 ──────────────────────────────────────────────────────── -->
    <div class="sp-header">
      <div class="sp-header-left">
        <span class="sp-title">策略管理</span>
        <el-tag type="info" size="small">共 {{ strategies.length }} 个</el-tag>
        <el-tag type="success" size="small">
          运行中 {{ strategies.filter(s => s.status === 'running').length }}
        </el-tag>
      </div>
      <div class="sp-header-right">
        <el-button size="small" plain @click="openWeightDialog" :disabled="!strategies.length">
          <el-icon><Histogram /></el-icon>
          权重分配
        </el-button>
        <el-button size="small" plain :loading="loading" @click="emit('refresh')">
          <el-icon><RefreshRight /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <!-- ── 无数据占位 ────────────────────────────────────────────────────── -->
    <div v-if="!loading && !strategies.length" class="sp-empty">
      <el-icon size="48" class="c-muted"><DataLine /></el-icon>
      <p class="c-muted">暂无策略数据</p>
    </div>

    <!-- ── 策略卡片列表 ─────────────────────────────────────────────────── -->
    <div class="sp-cards" v-loading="loading">
      <div
        v-for="row in strategies"
        :key="row.strategy_id"
        class="sp-card"
        :class="`sp-card--${row.status}`"
      >

        <!-- 卡片头 -->
        <div class="card-header" @click="toggleExpand(row.strategy_id)">
          <div class="card-header-left">
            <span class="status-dot" :class="getStatus(row.status).dot"></span>
            <span class="card-name">{{ row.name }}</span>
            <el-tag v-if="row.symbol" type="warning" size="small" effect="plain" class="symbol-tag">
              {{ row.symbol }}
            </el-tag>
            <el-tag :type="getStatus(row.status).type" size="small" effect="dark">
              {{ getStatus(row.status).label }}
            </el-tag>
          </div>

          <div class="card-header-right">
            <!-- 权重迷你条 -->
            <div class="weight-mini" v-if="detailCache[row.strategy_id]">
              <span class="c-muted" style="font-size:10px">权重</span>
              <el-progress
                :percentage="Math.round((detailCache[row.strategy_id]?.weight ?? 0) * 100)"
                :stroke-width="4"
                :show-text="false"
                color="var(--q-blue)"
                style="width:60px"
              />
              <span class="mono" style="font-size:11px;color:var(--q-blue)">
                {{ fmt.pct(detailCache[row.strategy_id]?.weight ?? 0) }}
              </span>
            </div>

            <el-button
              :type="row.status === 'running' ? 'danger' : 'primary'"
              :loading="!!actionLoading[row.strategy_id]"
              :disabled="row.status === 'connecting'"
              size="small" round
              @click.stop="handleAction(row)"
            >
              <el-icon v-if="!actionLoading[row.strategy_id]">
                <VideoPause v-if="row.status === 'running'" />
                <VideoPlay v-else />
              </el-icon>
              {{ row.status === 'running' ? '停止' : '启动' }}
            </el-button>

            <el-button size="small" plain @click.stop="openParamDialog(row)">
              <el-icon><Setting /></el-icon>
              参数
            </el-button>

            <el-icon class="expand-icon" :class="{ rotated: expanded[row.strategy_id] }">
              <ArrowDown />
            </el-icon>
          </div>
        </div>

        <!-- KPI 行（始终显示） -->
        <div class="card-kpi">
          <div class="kpi-item">
            <span class="kpi-l">实时盈亏</span>
            <span :class="['kpi-v', 'mono', pnlCss(row.pnl)]">{{ fmt.signed(row.pnl) }}</span>
          </div>
          <div class="kpi-sep"></div>
          <div class="kpi-item">
            <span class="kpi-l">成交次数</span>
            <span class="kpi-v mono" style="color:var(--q-blue)">{{ row.trade_count }}</span>
          </div>
          <div class="kpi-sep"></div>
          <div class="kpi-item">
            <span class="kpi-l">错误次数</span>
            <span class="kpi-v mono" :style="{ color: row.error_count > 0 ? 'var(--q-red)' : 'var(--q-muted)' }">
              {{ row.error_count }}
            </span>
          </div>
          <div class="kpi-sep"></div>
          <div class="kpi-item">
            <span class="kpi-l">持仓数</span>
            <span class="kpi-v mono">{{ row.positions?.length ?? 0 }}</span>
          </div>
        </div>

        <!-- 持仓标签行 -->
        <div class="card-positions" v-if="row.positions?.length">
          <el-tag
            v-for="pos in row.positions"
            :key="pos.symbol + pos.direction"
            :type="dirType(pos.direction)"
            size="small" effect="plain" class="pos-tag"
          >
            {{ pos.symbol }} {{ dirLabel(pos.direction) }} {{ Math.abs(pos.volume) }}手
            <span v-if="pos.pnl !== 0" :class="pnlCss(pos.pnl)" class="mono">
              {{ pos.pnl >= 0 ? '+' : '' }}{{ pos.pnl.toFixed(0) }}
            </span>
          </el-tag>
        </div>

        <!-- 展开区域 -->
        <Transition name="expand">
          <div v-if="expanded[row.strategy_id]" class="card-detail">

            <!-- 加载遮罩 -->
            <div v-if="detailLoading[row.strategy_id]" class="detail-loading">
              <el-icon class="is-loading" size="20"><Loading /></el-icon>
              <span class="c-muted">加载中…</span>
            </div>

            <template v-else-if="detailCache[row.strategy_id]">
              <!-- 实时信号流 -->
              <div class="detail-section">
                <div class="detail-section-title">
                  <el-icon><TrendCharts /></el-icon>
                  最新信号
                  <span class="c-muted" style="font-weight:400">(最近 {{ detailCache[row.strategy_id].recent_signals.length }} 条)</span>
                </div>
                <div class="signal-list" v-if="detailCache[row.strategy_id].recent_signals.length">
                  <div
                    v-for="(sig, idx) in [...detailCache[row.strategy_id].recent_signals].reverse()"
                    :key="idx"
                    class="signal-row"
                  >
                    <span class="sig-time mono c-muted">{{ sig.time }}</span>
                    <span class="sig-dir mono" :style="{ color: sigColor(sig.direction) }">
                      {{ sigArrow(sig.direction) }} {{ sig.direction === 'long' ? '多' : '空' }}
                    </span>
                    <span class="sig-symbol mono">{{ sig.symbol }}</span>
                    <span class="sig-price mono c-text">@{{ sig.price.toLocaleString() }}</span>
                    <span class="sig-vol  mono c-muted">×{{ sig.volume }}</span>
                    <el-tag size="small" effect="plain" :type="sig.direction === 'long' ? 'success' : 'danger'" class="sig-comment">
                      {{ commentCn(sig.comment) }}
                    </el-tag>
                  </div>
                </div>
                <p v-else class="c-muted" style="font-size:12px;margin:6px 0">暂无信号记录</p>
              </div>

              <!-- 自定义参数 -->
              <div class="detail-section">
                <div class="detail-section-title">
                  <el-icon><Operation /></el-icon>
                  运行参数
                  <el-button
                    size="small" link type="primary"
                    @click.stop="openParamDialog(row)"
                    style="margin-left:auto"
                  >
                    <el-icon><EditPen /></el-icon>编辑
                  </el-button>
                </div>
                <div class="params-grid">
                  <div
                    v-for="(val, key) in detailCache[row.strategy_id].params"
                    :key="key"
                    class="param-chip"
                  >
                    <span class="param-key">{{ key }}</span>
                    <span class="param-val mono">{{ val }}</span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </Transition>
      </div>
    </div>

    <!-- ╔══════════════════════════════════════════╗ -->
    <!-- ║         参数编辑对话框                   ║ -->
    <!-- ╚══════════════════════════════════════════╝ -->
    <el-dialog
      v-model="paramDialog.visible"
      :title="`编辑参数 — ${paramDialog.name}`"
      width="520px"
      :close-on-click-modal="false"
      class="param-dialog"
    >
      <div class="param-form">
        <div v-for="(val, key) in paramDialog.editParams" :key="key" class="param-field">
          <label class="param-field-label">
            <span class="param-key-label">{{ key }}</span>
            <span class="param-type-badge">{{ paramType(val) }}</span>
          </label>

          <!-- Boolean -->
          <el-switch
            v-if="paramType(val) === 'boolean'"
            v-model="paramDialog.editParams[key]"
          />
          <!-- Number -->
          <el-input-number
            v-else-if="paramType(val) === 'number'"
            v-model="paramDialog.editParams[key]"
            :precision="Number.isInteger(val) ? 0 : 4"
            :step="Number.isInteger(val) ? 1 : 0.01"
            size="small"
            style="width:160px"
          />
          <!-- String -->
          <el-input
            v-else
            v-model="paramDialog.editParams[key]"
            size="small"
            style="width:200px"
          />
        </div>
      </div>

      <div class="param-restart-row">
        <el-checkbox v-model="paramDialog.restart">
          策略运行中时自动重启以使参数生效
        </el-checkbox>
        <el-tooltip content="停止后重启策略，所有参数立即生效（约需 1-3 秒）" placement="top">
          <el-icon class="c-muted" style="cursor:pointer"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>

      <template #footer>
        <el-button @click="paramDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="paramDialog.saving" @click="saveParams">
          应用参数
        </el-button>
      </template>
    </el-dialog>

    <!-- ╔══════════════════════════════════════════╗ -->
    <!-- ║         权重分配对话框                   ║ -->
    <!-- ╚══════════════════════════════════════════╝ -->
    <el-dialog
      v-model="weightDialog.visible"
      title="权重分配"
      width="480px"
      :close-on-click-modal="false"
      class="weight-dialog"
    >
      <div class="weight-hint">
        权重控制各策略的资金分配比例，总和须等于 100%。
      </div>

      <div class="weight-list">
        <div
          v-for="row in strategies"
          :key="row.strategy_id"
          class="weight-row"
        >
          <div class="weight-name">
            <el-tag :type="getStatus(row.status).type" size="small" effect="dark">
              {{ getStatus(row.status).label }}
            </el-tag>
            <span class="w-name">{{ row.name }}</span>
            <span class="c-muted" style="font-size:11px">{{ row.symbol }}</span>
          </div>
          <div class="weight-control">
            <el-slider
              v-model="weightDialog.weights[row.strategy_id]"
              :min="0" :max="100" :step="1"
              show-input input-size="small"
              style="flex:1"
            />
            <span class="mono" style="width:40px;text-align:right;color:var(--q-blue)">
              {{ weightDialog.weights[row.strategy_id] ?? 0 }}%
            </span>
          </div>
        </div>
      </div>

      <!-- 总和提示 -->
      <div class="weight-total" :class="Math.abs(weightTotal - 100) <= 1 ? 'total-ok' : 'total-warn'">
        <span>权重总和：</span>
        <span class="mono fw-600">{{ weightTotal }}%</span>
        <span v-if="Math.abs(weightTotal - 100) <= 1" class="c-green">✓ 已平衡</span>
        <span v-else class="c-yellow">需调整至 100%</span>
      </div>

      <template #footer>
        <el-button @click="autoEqualWeights" plain>均等分配</el-button>
        <el-button @click="weightDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="weightDialog.saving"
          :disabled="Math.abs(weightTotal - 100) > 1"
          @click="saveWeights"
        >
          保存权重
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<style scoped>
/* ── 面板容器 ── */
.sp-wrap {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid var(--q-border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--q-panel);
}

/* ── 面板头部 ── */
.sp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--q-border);
  background: rgba(0,0,0,.2);
  flex-wrap: wrap;
  gap: 8px;
}
.sp-header-left  { display: flex; align-items: center; gap: 8px; }
.sp-header-right { display: flex; align-items: center; gap: 8px; }
.sp-title { font-size: 14px; font-weight: 700; color: var(--q-text); }

/* ── 空状态 ── */
.sp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 64px 0;
  gap: 12px;
}

/* ── 卡片列表 ── */
.sp-cards {
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: var(--q-border);
}

/* ── 单张策略卡 ── */
.sp-card {
  background: var(--q-panel);
  transition: background .2s;
}
.sp-card--error { background: rgba(248,81,73,.04); }

/* ── 卡头 ── */
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  cursor: pointer;
  user-select: none;
  gap: 12px;
  flex-wrap: wrap;
}
.card-header:hover { background: rgba(255,255,255,.03); }

.card-header-left  { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-header-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; flex-wrap: wrap; }

.card-name   { font-weight: 700; font-size: 14px; color: var(--q-text); }
.symbol-tag  { font-family: var(--q-font-mono); font-size: 11px; }

/* 状态指示点 */
.status-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.dot-run  { background: var(--q-green);  box-shadow: 0 0 6px var(--q-green);  animation: pulse 2s infinite; }
.dot-stop { background: var(--q-muted);  }
.dot-warn { background: var(--q-yellow); box-shadow: 0 0 6px var(--q-yellow); }
.dot-err  { background: var(--q-red);    box-shadow: 0 0 6px var(--q-red);    }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.45} }

/* 权重迷你条 */
.weight-mini { display: flex; align-items: center; gap: 5px; }

/* 展开箭头 */
.expand-icon { color: var(--q-muted); transition: transform .25s; }
.expand-icon.rotated { transform: rotate(180deg); }

/* ── KPI 行 ── */
.card-kpi {
  display: flex;
  align-items: center;
  padding: 8px 18px;
  gap: 0;
  background: rgba(0,0,0,.12);
  border-top: 1px solid var(--q-border);
}
.kpi-item { display: flex; flex-direction: column; gap: 2px; padding: 0 16px 0 0; }
.kpi-l { font-size: 10px; color: var(--q-muted); }
.kpi-v { font-size: 15px; font-weight: 700; line-height: 1; }
.kpi-sep { width: 1px; height: 28px; background: var(--q-border); margin: 0 16px 0 0; }

/* ── 持仓行 ── */
.card-positions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 18px;
  border-top: 1px solid var(--q-border);
}
.pos-tag { font-family: var(--q-font-mono); font-size: 11px; }

/* ── 展开区域 ── */
.card-detail {
  border-top: 1px solid var(--q-border);
  background: rgba(0,0,0,.08);
  padding: 14px 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 展开动画 */
.expand-enter-active, .expand-leave-active {
  transition: all .28s ease;
  overflow: hidden;
}
.expand-enter-from, .expand-leave-to { max-height: 0; opacity: 0; padding: 0 18px; }
.expand-enter-to,   .expand-leave-from { max-height: 600px; opacity: 1; }

.detail-loading {
  display: flex; align-items: center; gap: 8px; color: var(--q-muted); font-size: 12px;
}

/* 分区标题 */
.detail-section { display: flex; flex-direction: column; gap: 8px; }
.detail-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--q-text);
  text-transform: uppercase;
  letter-spacing: .4px;
}

/* ── 信号列表 ── */
.signal-list { display: flex; flex-direction: column; gap: 4px; }
.signal-row  {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 8px;
  background: rgba(0,0,0,.15);
  border-radius: 4px;
  font-size: 12px;
  flex-wrap: wrap;
}
.sig-time   { width: 60px; flex-shrink: 0; }
.sig-dir    { width: 30px; font-weight: 700; flex-shrink: 0; }
.sig-symbol { flex-shrink: 0; }
.sig-price  { flex-shrink: 0; }
.sig-vol    { flex-shrink: 0; }
.sig-comment{ flex-shrink: 0; }

/* ── 参数网格 ── */
.params-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.param-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  background: rgba(88,166,255,.1);
  border: 1px solid rgba(88,166,255,.25);
  border-radius: 12px;
  font-size: 11px;
}
.param-key  { color: var(--q-muted); }
.param-val  { color: var(--q-blue); font-weight: 600; }

/* ╔════════════════════════════╗ */
/* ║  参数对话框内部样式        ║ */
/* ╚════════════════════════════╝ */
.param-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 60vh;
  overflow-y: auto;
  padding-right: 4px;
}
.param-field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.param-field-label {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
}
.param-key-label  { font-size: 13px; color: var(--q-text); font-weight: 500; }
.param-type-badge {
  font-size: 10px;
  color: var(--q-muted);
  background: var(--q-border);
  padding: 1px 6px;
  border-radius: 8px;
}
.param-restart-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--q-border);
}

/* ╔════════════════════════════╗ */
/* ║  权重对话框内部样式        ║ */
/* ╚════════════════════════════╝ */
.weight-hint {
  font-size: 12px;
  color: var(--q-muted);
  margin-bottom: 16px;
}
.weight-list { display: flex; flex-direction: column; gap: 14px; }
.weight-row  { display: flex; flex-direction: column; gap: 6px; }
.weight-name { display: flex; align-items: center; gap: 8px; }
.w-name      { font-weight: 600; font-size: 13px; }
.weight-control { display: flex; align-items: center; gap: 10px; }

.weight-total {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}
.total-ok   { background: rgba(63,185,80,.1);  border: 1px solid rgba(63,185,80,.3);  }
.total-warn { background: rgba(227,179,65,.1); border: 1px solid rgba(227,179,65,.3); }

/* ── 工具类 ── */
.mono   { font-family: var(--q-font-mono); font-variant-numeric: tabular-nums; }
.c-green { color: var(--q-green); }
.c-red   { color: var(--q-red);   }
.c-muted { color: var(--q-muted); }
.c-text  { color: var(--q-text);  }
.c-blue  { color: var(--q-blue);  }
.c-yellow{ color: var(--q-yellow);}
.fw-600  { font-weight: 600; }
</style>
