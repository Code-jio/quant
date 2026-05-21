<template>
  <div class="trial-run-page">
    <header class="trial-topbar">
      <div class="topbar-left">
        <el-button text @click="router.push('/')">
          <el-icon><ArrowLeft /></el-icon>
          监控台
        </el-button>
        <div class="title-block">
          <h1>试运行操作台</h1>
          <span>2 秒轮询 · 单合约验证 · 风控闭环</span>
        </div>
      </div>
      <div class="topbar-right">
        <span class="poll-dot" :class="{ active: pollingActive }" />
        <span class="muted">2 秒轮询</span>
        <el-button size="small" plain :loading="refreshing" @click="refreshAll(false)">
          <el-icon><RefreshRight /></el-icon>
          刷新
        </el-button>
      </div>
    </header>

    <main class="trial-content">
      <section class="status-strip">
        <div v-for="item in statusItems" :key="item.label" class="status-cell">
          <span class="status-label">{{ item.label }}</span>
          <strong class="status-value" :class="item.className">{{ item.value }}</strong>
        </div>
      </section>

      <section class="work-grid">
        <div class="panel account-panel">
          <div class="panel-head">
            <div>
              <h2>连接账户</h2>
              <span>密码仅本次输入，连接成功后立即清空</span>
            </div>
            <el-tag :type="connected ? 'success' : 'info'" effect="plain">
              {{ connected ? '已连接' : '未连接' }}
            </el-tag>
          </div>

          <el-form
            ref="formRef"
            :model="accountForm"
            :rules="rules"
            label-position="top"
            class="account-form"
            @submit.prevent="handleLogin"
          >
            <div class="form-grid">
              <el-form-item label="账号" prop="username">
                <el-input v-model="accountForm.username" clearable :disabled="actionLoading.connect" />
              </el-form-item>
              <el-form-item label="密码" prop="password">
                <el-input
                  v-model="accountForm.password"
                  type="password"
                  show-password
                  autocomplete="current-password"
                  :disabled="actionLoading.connect"
                />
              </el-form-item>
              <el-form-item label="Broker" prop="broker_id">
                <el-input v-model="accountForm.broker_id" :disabled="actionLoading.connect" />
              </el-form-item>
              <el-form-item label="线路">
                <el-select
                  v-model="selectedFrontKey"
                  placeholder="选择前置线路"
                  :disabled="actionLoading.connect || frontOptions.length === 0"
                  @change="applyFrontPreset"
                >
                  <el-option
                    v-for="front in frontOptions"
                    :key="front.key"
                    :label="front.label"
                    :value="front.key"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="TD Server" prop="td_server">
                <el-input v-model="accountForm.td_server" :disabled="actionLoading.connect" />
              </el-form-item>
              <el-form-item label="MD Server" prop="md_server">
                <el-input v-model="accountForm.md_server" :disabled="actionLoading.connect" />
              </el-form-item>
              <el-form-item label="AppID">
                <el-input v-model="accountForm.app_id" :disabled="actionLoading.connect" />
              </el-form-item>
              <el-form-item label="认证码">
                <el-input v-model="accountForm.auth_code" :disabled="actionLoading.connect" />
              </el-form-item>
              <el-form-item label="环境">
                <el-select v-model="accountForm.environment" :disabled="actionLoading.connect">
                  <el-option label="实盘（生产版 API）" value="实盘" />
                  <el-option label="测试" value="测试" />
                </el-select>
              </el-form-item>
            </div>

            <div class="action-row">
              <el-button type="primary" native-type="submit" :loading="actionLoading.connect">
                <el-icon><Connection /></el-icon>
                连接
              </el-button>
              <el-button type="warning" plain :loading="actionLoading.prepare" :disabled="!canPrepare" @click="handlePrepare">
                <el-icon><Operation /></el-icon>
                准备策略
              </el-button>
              <el-button type="success" plain :loading="actionLoading.start" :disabled="!canStart" @click="handleStart">
                <el-icon><VideoPlay /></el-icon>
                开始验证交易
              </el-button>
              <el-button type="danger" plain :loading="actionLoading.stop" :disabled="!canStop" @click="handleStop">
                <el-icon><SwitchButton /></el-icon>
                停止策略
              </el-button>
              <el-button plain :loading="actionLoading.reset" :disabled="!canReset" @click="handleReset">
                <el-icon><RefreshRight /></el-icon>
                重新准备
              </el-button>
            </div>
          </el-form>
        </div>

        <div class="panel flow-panel">
          <div class="panel-head">
            <div>
              <h2>流程区</h2>
              <span>从账户连接到闭环完成的最短链路</span>
            </div>
            <el-tag :type="trialStatusType" effect="plain">{{ trialStatusLabel }}</el-tag>
          </div>

          <div class="flow-list">
            <div v-for="item in flowItems" :key="item.label" class="flow-item" :class="item.state">
              <span class="flow-mark">
                <el-icon v-if="item.state === 'done'"><CircleCheck /></el-icon>
                <el-icon v-else-if="item.state === 'active'"><VideoPlay /></el-icon>
                <el-icon v-else><Lock /></el-icon>
              </span>
              <div>
                <strong>{{ item.label }}</strong>
                <span>{{ item.detail }}</span>
              </div>
            </div>
          </div>

          <div class="readiness-box">
            <div class="readiness-meta">
              <span>行情就绪</span>
              <strong class="mono">{{ barCount }} / {{ readinessBars }}</strong>
            </div>
            <el-progress
              :percentage="readinessPercent"
              :stroke-width="8"
              :show-text="false"
              color="#58a6ff"
            />
          </div>
        </div>
      </section>

      <section class="metrics-grid">
        <div class="panel risk-panel">
          <div class="panel-head compact">
            <div>
              <h2>风险区</h2>
              <span>只读展示后端风控限制</span>
            </div>
          </div>
          <div class="risk-grid">
            <div v-for="item in riskItems" :key="item.label" class="metric-cell">
              <span>{{ item.label }}</span>
              <strong :class="item.className">{{ item.value }}</strong>
            </div>
          </div>
        </div>

        <div class="panel monitor-panel">
          <div class="panel-head compact">
            <div>
              <h2>监控区</h2>
              <span>试运行核心计数</span>
            </div>
          </div>
          <div class="monitor-grid">
            <div class="metric-cell">
              <span>行情 bar / 就绪阈值</span>
              <strong class="mono">{{ barCount }} / {{ readinessBars }}</strong>
            </div>
            <div class="metric-cell">
              <span>持仓数量</span>
              <strong class="mono">{{ positions.length }}</strong>
            </div>
            <div class="metric-cell">
              <span>订单数量</span>
              <strong class="mono">{{ orders.length }}</strong>
            </div>
            <div class="metric-cell">
              <span>成交数量</span>
              <strong class="mono">{{ trades.length }}</strong>
            </div>
          </div>
          <div class="danger-row">
            <el-button type="danger" plain :loading="actionLoading.emergency" :disabled="!canEmergencyStop" @click="handleEmergencyStop">
              <el-icon><WarningFilled /></el-icon>
              急停
            </el-button>
            <el-button type="success" plain :loading="actionLoading.resume" :disabled="!canResume" @click="handleResume">
              <el-icon><CircleCheck /></el-icon>
              解除急停
            </el-button>
            <el-button type="warning" plain :loading="actionLoading.cancelAll" :disabled="!canCancelAll" @click="handleCancelAll">
              <el-icon><CloseBold /></el-icon>
              一键撤单
            </el-button>
            <el-button type="danger" plain :loading="actionLoading.close" :disabled="!canQuickClose" @click="handleQuickClose">
              <el-icon><Operation /></el-icon>
              快捷平仓
            </el-button>
          </div>
        </div>
      </section>

      <section class="panel table-panel">
        <el-tabs v-model="activeTab" class="trial-tabs">
          <el-tab-pane label="订单" name="orders">
            <div class="table-scroll">
              <el-table :data="orders" size="small" height="320" empty-text="暂无订单">
                <el-table-column prop="order_id" label="委托号" min-width="120" show-overflow-tooltip />
                <el-table-column prop="symbol" label="合约" min-width="100" show-overflow-tooltip />
                <el-table-column prop="direction" label="方向" width="76" />
                <el-table-column prop="offset" label="开平" width="76" />
                <el-table-column prop="price" label="价格" width="92" />
                <el-table-column prop="volume" label="数量" width="80" />
                <el-table-column prop="status" label="状态" min-width="100" show-overflow-tooltip />
                <el-table-column label="时间" min-width="150" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.datetime || row.time || row.update_time || '--' }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>

          <el-tab-pane label="成交" name="trades">
            <div class="table-scroll">
              <el-table :data="trades" size="small" height="320" empty-text="暂无成交">
                <el-table-column prop="trade_id" label="成交号" min-width="120" show-overflow-tooltip />
                <el-table-column prop="order_id" label="委托号" min-width="120" show-overflow-tooltip />
                <el-table-column prop="symbol" label="合约" min-width="100" show-overflow-tooltip />
                <el-table-column prop="direction" label="方向" width="76" />
                <el-table-column prop="price" label="价格" width="92" />
                <el-table-column prop="volume" label="数量" width="80" />
                <el-table-column label="时间" min-width="150" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.datetime || row.time || '--' }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>

          <el-tab-pane label="持仓" name="positions">
            <div class="table-scroll">
              <el-table :data="positions" size="small" height="320" empty-text="暂无持仓">
                <el-table-column prop="symbol" label="合约" min-width="110" show-overflow-tooltip />
                <el-table-column prop="direction" label="方向" width="76" />
                <el-table-column prop="volume" label="持仓" width="88" />
                <el-table-column prop="available" label="可用" width="88" />
                <el-table-column prop="frozen" label="冻结" width="88" />
                <el-table-column prop="pnl" label="盈亏" min-width="100" />
                <el-table-column prop="price" label="均价" min-width="100" />
              </el-table>
            </div>
          </el-tab-pane>

          <el-tab-pane label="日志" name="logs">
            <div class="table-scroll">
              <el-table :data="logs" size="small" height="320" empty-text="暂无日志">
                <el-table-column label="时间" min-width="150" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.time || row.datetime || row.timestamp || '--' }}</template>
                </el-table-column>
                <el-table-column prop="level" label="级别" width="92" />
                <el-table-column label="内容" min-width="320" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.message || row.msg || row.text || row }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>
        </el-tabs>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth.js'
import {
  ArrowLeft,
  CircleCheck,
  CloseBold,
  Connection,
  Lock,
  Operation,
  RefreshRight,
  SwitchButton,
  VideoPlay,
  WarningFilled,
} from '@element-plus/icons-vue'
import {
  cancelAllOrders,
  closePosition,
  emergencyStop,
  fetchAuthStatus,
  fetchOrders,
  fetchPositions,
  fetchRiskStatus,
  fetchSystemLogs,
  fetchTrades,
  fetchTrialRunConfig,
  fetchTrialRunStatus,
  login,
  prepareTrialRun,
  resetTrialRun,
  resumeTrading,
  startTrialRun,
  stopTrialRun,
} from '@/api/index.js'

const router = useRouter()
const authStore = useAuthStore()
const formRef = ref(null)
const activeTab = ref('orders')
const pollingActive = ref(false)
const refreshing = ref(false)
const config = ref({})
const selectedFrontKey = ref('')
const trialStatus = ref({})
const authStatus = ref({})
const riskStatus = ref({})
const orders = ref([])
const trades = ref([])
const positions = ref([])
const logs = ref([])

const accountForm = reactive({
  username: '',
  password: '',
  broker_id: '',
  td_server: '',
  md_server: '',
  app_id: '',
  auth_code: '',
  environment: '测试',
})

const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  broker_id: [{ required: true, message: '请输入 Broker', trigger: 'blur' }],
  td_server: [{ required: true, message: '请输入交易前置', trigger: 'blur' }],
  md_server: [{ required: true, message: '请输入行情前置', trigger: 'blur' }],
}

const actionLoading = reactive({
  connect: false,
  prepare: false,
  start: false,
  stop: false,
  reset: false,
  emergency: false,
  resume: false,
  cancelAll: false,
  close: false,
})

const hasSession = computed(() => authStore.isLoggedIn)

const STATUS_LABELS = {
  disconnected: '未连接',
  idle: '待准备',
  prepared: '已准备',
  waiting_market_data: '等行情',
  warming: '等行情',
  warmup: '等行情',
  ready_to_start: '可开始',
  ready_to_arm: '可开始',
  started: '已开始',
  armed: '已开始',
  entry_pending: '开仓待成',
  holding: '持仓中',
  closing: '平仓中',
  running: '运行中',
  stopped: '已停止',
  completed: '闭环完成',
  emergency_stopped: '急停中',
  error: '异常',
}

let pollTimer = null

const connected = computed(() => {
  const status = authStatus.value || {}
  if ('gateway_connected' in status) return Boolean(status.gateway_connected)
  if ('connected' in status) return Boolean(status.connected)
  if (status.status) return ['connected', 'ready', 'ok'].includes(String(status.status).toLowerCase())
  return Boolean(status.td_connected || status.md_connected)
})

const riskConfig = computed(() => {
  const risk = riskStatus.value?.risk ?? riskStatus.value ?? {}
  return Object.keys(risk).length ? risk : (config.value?.risk ?? {})
})

const frontOptions = computed(() => {
  const fronts = config.value?.trading?.fronts
  if (!Array.isArray(fronts)) return []
  return fronts
    .map((front, index) => ({
      ...front,
      key: front.key || `${front.td_server || ''}|${front.md_server || ''}|${index}`,
      label: front.label || `${front.td_server || '--'} / ${front.md_server || '--'}`,
    }))
    .filter(front => front.td_server && front.md_server)
})

const allowedSymbols = computed(() => {
  const value = riskConfig.value.allowed_symbols ?? config.value.allowed_symbols ?? config.value.risk?.allowed_symbols ?? []
  if (Array.isArray(value)) return value
  if (typeof value === 'string' && value) return value.split(',').map(item => item.trim()).filter(Boolean)
  return []
})

const allowedSymbol = computed(() => (
  config.value.allowed_symbol
  || config.value.symbol
  || config.value.strategy?.symbol
  || allowedSymbols.value[0]
  || ''
))

const statusSnapshot = computed(() => trialStatus.value.snapshot || {})
const barCount = computed(() => numberOf(trialStatus.value.bar_count ?? statusSnapshot.value.bar_count ?? trialStatus.value.bars, 0))
const readinessBars = computed(() => Math.max(1, numberOf(
  trialStatus.value.readiness_bars
  ?? statusSnapshot.value.readiness_bars
  ?? trialStatus.value.warmup_bars
  ?? statusSnapshot.value.warmup_bars,
  1,
)))
const marketReady = computed(() => Boolean(
  trialStatus.value.market_ready
  || statusSnapshot.value.market_ready
  || trialStatus.value.ready_to_arm
  || statusSnapshot.value.ready_to_arm
  || ['ready_to_start', 'ready_to_arm', 'started', 'armed', 'entry_pending', 'holding', 'closing', 'running', 'completed'].includes(statusCode.value),
))
const readinessPercent = computed(() => {
  if (marketReady.value) return 100
  return Math.min(100, Math.round((barCount.value / readinessBars.value) * 100))
})
const prepared = computed(() => Boolean(trialStatus.value.prepared || ['prepared', 'waiting_market_data', 'warming', 'warmup', 'ready_to_start', 'ready_to_arm', 'started', 'armed', 'entry_pending', 'holding', 'closing', 'running', 'completed'].includes(statusCode.value)))
const started = computed(() => Boolean(trialStatus.value.started || trialStatus.value.authorized || trialStatus.value.armed || statusSnapshot.value.started || statusSnapshot.value.authorized || ['started', 'armed', 'entry_pending', 'holding', 'closing', 'running', 'completed'].includes(statusCode.value)))
const closedLoop = computed(() => Boolean(trialStatus.value.completed || statusSnapshot.value.completed || trialStatus.value.closed_loop_completed || statusCode.value === 'completed'))
const statusCode = computed(() => String(trialStatus.value.status || trialStatus.value.state || 'idle').toLowerCase())
const trialStatusLabel = computed(() => STATUS_LABELS[statusCode.value] || trialStatus.value.status || trialStatus.value.state || '待准备')
const trialStatusType = computed(() => {
  if (['error', 'emergency_stopped'].includes(statusCode.value)) return 'danger'
  if (['started', 'armed', 'entry_pending', 'holding', 'running', 'completed'].includes(statusCode.value)) return 'success'
  if (['waiting_market_data', 'warming', 'warmup', 'prepared', 'ready_to_start', 'ready_to_arm', 'closing'].includes(statusCode.value)) return 'warning'
  return 'info'
})

const emergencyActive = computed(() => Boolean(riskConfig.value.emergency_stop || riskStatus.value.emergency_stop))
const activeOrderCount = computed(() => orders.value.filter(order => ['submitting', 'submitted', 'partfilled'].includes(String(order.status || '').toLowerCase())).length)
const hasCloseablePosition = computed(() => positions.value.some(position => (
  matchesAllowedSymbol(position) && Math.abs(numberOf(position.volume, 0)) > 0
)))
const canPrepare = computed(() => (
  hasSession.value
  && connected.value
  && !actionLoading.prepare
  && !['started', 'armed', 'entry_pending', 'holding', 'closing'].includes(statusCode.value)
))
const canStart = computed(() => (
  hasSession.value
  && connected.value
  && prepared.value
  && !started.value
  && marketReady.value
  && !actionLoading.start
))
const canStop = computed(() => hasSession.value && prepared.value && !actionLoading.stop)
const canReset = computed(() => hasSession.value && !actionLoading.reset)
const canEmergencyStop = computed(() => hasSession.value && connected.value && !emergencyActive.value && !actionLoading.emergency)
const canResume = computed(() => hasSession.value && emergencyActive.value && !actionLoading.resume)
const canCancelAll = computed(() => hasSession.value && connected.value && activeOrderCount.value > 0 && !actionLoading.cancelAll)
const canQuickClose = computed(() => hasSession.value && connected.value && hasCloseablePosition.value && !actionLoading.close)

const statusItems = computed(() => [
  { label: '账号', value: accountForm.username || authStatus.value.account_id || config.value.masked_account_id || '--' },
  { label: '环境', value: accountForm.environment || config.value.environment || '--' },
  { label: '唯一合约', value: allowedSymbol.value || '--', className: 'mono' },
  { label: '连接状态', value: connected.value ? '已连接' : '未连接', className: connected.value ? 'ok' : 'muted-strong' },
  { label: '试运行状态', value: trialStatusLabel.value, className: trialStatusType.value === 'danger' ? 'bad' : '' },
  { label: '急停状态', value: emergencyActive.value ? '急停中' : '正常', className: emergencyActive.value ? 'bad' : 'ok' },
])

const flowItems = computed(() => [
  {
    label: '连接账户',
    detail: connected.value ? '交易通道已就绪' : '等待账户登录',
    state: connected.value ? 'done' : 'active',
  },
  {
    label: '准备策略',
    detail: prepared.value ? '策略参数已加载' : '等待准备策略',
    state: prepared.value ? 'done' : connected.value ? 'active' : 'idle',
  },
  {
    label: '行情就绪',
    detail: marketReady.value ? '已收到有效行情 bar' : '等待首根有效行情 bar',
    state: marketReady.value ? 'done' : prepared.value ? 'active' : 'idle',
  },
  {
    label: '开始验证交易',
    detail: started.value ? '验证开仓流程已启动' : '点击后等待下一根有效 bar 发单',
    state: started.value ? 'done' : marketReady.value ? 'active' : 'idle',
  },
  {
    label: '闭环完成',
    detail: closedLoop.value ? '验证链路完成' : '等待成交与平仓回报',
    state: closedLoop.value ? 'done' : started.value ? 'active' : 'idle',
  },
])

const riskItems = computed(() => [
  { label: 'allowed_symbols', value: allowedSymbols.value.join(', ') || allowedSymbol.value || '--', className: 'mono wrap' },
  { label: 'max_order_volume', value: displayValue(riskConfig.value.max_order_volume) },
  { label: 'max_position_volume', value: displayValue(riskConfig.value.max_position_volume) },
  { label: 'max_orders_per_minute', value: displayValue(riskConfig.value.max_orders_per_minute) },
  {
    label: 'allow_market_orders',
    value: riskConfig.value.allow_market_orders === undefined
      ? '--'
      : riskConfig.value.allow_market_orders ? '是' : '否',
  },
  { label: 'last_reject_reason', value: trialStatus.value.last_reject_reason || riskStatus.value.last_reject_reason || riskConfig.value.last_reject_reason || '--', className: 'wrap warn' },
])

function pick(source, keys, fallback = '') {
  for (const key of keys) {
    const value = source?.[key]
    if (value !== undefined && value !== null && value !== '') return value
  }
  return fallback
}

function numberOf(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function displayValue(value) {
  return value === undefined || value === null || value === '' ? '--' : String(value)
}

function syncSelectedFront() {
  const matched = frontOptions.value.find(front => (
    front.td_server === accountForm.td_server
    && front.md_server === accountForm.md_server
  ))
  selectedFrontKey.value = matched?.key || ''
}

function applyFrontPreset(key) {
  const front = frontOptions.value.find(item => item.key === key)
  if (!front) return
  accountForm.td_server = front.td_server
  accountForm.md_server = front.md_server
  accountForm.environment = front.environment || accountForm.environment
}

function safeLoginPayload(payload) {
  return {
    ...payload,
    password: payload.password ? '<hidden>' : '',
  }
}

function matchesAllowedSymbol(row) {
  return String(row?.symbol || '') === allowedSymbol.value
}

function firstClosePrice() {
  const row = positions.value.find(position => matchesAllowedSymbol(position) && Math.abs(numberOf(position.volume, 0)) > 0)
  if (!row) return 0
  for (const key of ['last_price', 'cur_price', 'price', 'cost_price', 'cost', 'avg_price']) {
    const price = numberOf(row[key], 0)
    if (price > 0) return price
  }
  return 0
}

function normalizeList(payload, keys = []) {
  if (Array.isArray(payload)) return payload
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key]
  }
  if (Array.isArray(payload?.data)) return payload.data
  if (Array.isArray(payload?.items)) return payload.items
  return []
}

function objectOrNull(value) {
  return value && typeof value === 'object' ? value : null
}

function applyConfig(data = {}) {
  const rawConfig = objectOrNull(data.config) || {}
  const trialRun = objectOrNull(rawConfig.trial_run) || {}
  const trading = objectOrNull(data.trading) || objectOrNull(rawConfig.trading) || {}
  const strategy = objectOrNull(data.strategy) || objectOrNull(rawConfig.strategy) || {}
  const risk = objectOrNull(data.risk) || objectOrNull(rawConfig.risk) || {}
  config.value = {
    ...data,
    raw_config: rawConfig,
    trading,
    strategy,
    risk,
    allowed_symbol: data.allowed_symbol || trialRun.allowed_symbol || strategy.symbol || '',
    environment: data.environment || trialRun.vnpy_environment || trading.vnpy_environment || trading.environment || '测试',
  }
  const loginConfig = {
    ...trading,
    ...trialRun,
    account_id: data.account_id || trialRun.account_id || trading.username || '',
    environment: config.value.environment,
  }
  const server = pick(loginConfig, ['server', 'front_server', 'ctp_server'])

  accountForm.username = pick(loginConfig, ['username', 'account_id', 'account', 'investor_id', 'user_id'], accountForm.username)
  accountForm.broker_id = pick(loginConfig, ['broker_id', 'broker'], accountForm.broker_id)
  accountForm.td_server = pick(loginConfig, ['td_server', 'trade_server'], accountForm.td_server || server)
  accountForm.md_server = pick(loginConfig, ['md_server', 'quote_server', 'market_server'], accountForm.md_server || server)
  accountForm.app_id = pick(loginConfig, ['app_id', 'appid', 'app'], accountForm.app_id)
  accountForm.auth_code = pick(loginConfig, ['auth_code', 'auth', 'authcode'], accountForm.auth_code)
  accountForm.environment = pick(loginConfig, ['environment', 'env'], accountForm.environment)
  syncSelectedFront()
}

async function loadConfig() {
  try {
    const data = await fetchTrialRunConfig()
    applyConfig(data)
  } catch (err) {
    ElMessage.warning(`试运行配置暂不可用: ${err.message}`)
  }
}

async function refreshAll(silent = true) {
  refreshing.value = !silent
  const publicResults = await Promise.allSettled([
    fetchTrialRunStatus(),
    fetchAuthStatus(),
  ])

  if (publicResults[0].status === 'fulfilled') trialStatus.value = publicResults[0].value || {}
  if (publicResults[1].status === 'fulfilled') {
    authStatus.value = publicResults[1].value || {}
    if (authStatus.value.logged_in === false) {
      authStore.clearAuth()
    } else if (authStatus.value.logged_in === true && !authStore.isLoggedIn && authStatus.value.account_id) {
      authStore.setAuth({ accountId: authStatus.value.account_id })
    }
  }

  if (!hasSession.value) {
    refreshing.value = false
    return
  }

  const noRedirect = { redirectOn401: false }
  const results = await Promise.allSettled([
    fetchRiskStatus(noRedirect),
    fetchOrders(noRedirect),
    fetchTrades(noRedirect),
    fetchPositions(noRedirect),
    fetchSystemLogs({ limit: 200 }, noRedirect),
  ])

  if (results.some(result => result.status === 'rejected' && /未登录|401/.test(String(result.reason?.message || '')))) {
    authStore.clearAuth()
  }

  if (results[0].status === 'fulfilled') riskStatus.value = results[0].value || {}
  if (results[1].status === 'fulfilled') orders.value = normalizeList(results[1].value, ['orders'])
  if (results[2].status === 'fulfilled') trades.value = normalizeList(results[2].value, ['trades'])
  if (results[3].status === 'fulfilled') positions.value = normalizeList(results[3].value, ['positions'])
  if (results[4].status === 'fulfilled') logs.value = normalizeList(results[4].value, ['logs'])

  refreshing.value = false
}

function startPolling() {
  pollingActive.value = true
  pollTimer = setInterval(() => refreshAll(true), 2000)
}

function stopPolling() {
  pollingActive.value = false
  clearInterval(pollTimer)
  pollTimer = null
}

async function runAction(key, action, successText) {
  actionLoading[key] = true
  try {
    await action()
    if (successText) ElMessage.success(successText)
    await refreshAll(true)
  } catch (err) {
    ElMessage.error(err.message || '操作失败')
  } finally {
    actionLoading[key] = false
  }
}

async function handleLogin() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  await runAction('connect', async () => {
    const payload = {
      username: accountForm.username,
      password: accountForm.password,
      broker_id: accountForm.broker_id,
      td_server: accountForm.td_server,
      md_server: accountForm.md_server,
      app_id: accountForm.app_id,
      auth_code: accountForm.auth_code,
      environment: accountForm.environment,
      auto_start_strategy: false,
    }
    let res
    try {
      res = await login(payload)
    } catch (err) {
      console.error('[TrialRun Login Error]', {
        message: err?.message || String(err),
        status: err?.status,
        path: err?.path,
        detail: err?.detail,
        form: safeLoginPayload(payload),
        error: err,
      })
      throw err
    }
    authStore.setAuth({
      accountId: res.account_id || accountForm.username,
      balance: res.balance,
    })
    authStatus.value = { ...authStatus.value, connected: true, account_id: res.account_id || accountForm.username }
    accountForm.password = ''
  }, '账户连接成功')
}

async function handlePrepare() {
  await runAction('prepare', () => prepareTrialRun({ symbol: allowedSymbol.value }), '策略准备已发送')
}

async function handleStart() {
  if (!allowedSymbol.value) {
    ElMessage.warning('缺少 config.allowed_symbol，无法开始验证开仓')
    return
  }

  try {
    await ElMessageBox.confirm(
      `确认开始验证交易？系统将仅对 ${allowedSymbol.value} 发 1 手验证开仓。`,
      '开始验证交易确认',
      { confirmButtonText: '开始验证', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  await runAction('start', () => startTrialRun({ symbol: allowedSymbol.value, volume: 1 }), '开始验证交易已发送')
}

async function handleStop() {
  await runAction('stop', () => stopTrialRun(), '停止策略已发送')
}

async function handleReset() {
  await runAction('reset', () => resetTrialRun(), '重新准备指令已发送')
}

async function handleEmergencyStop() {
  try {
    await ElMessageBox.confirm('确认立即急停并撤销活跃委托？', '交易急停', {
      confirmButtonText: '立即急停',
      cancelButtonText: '取消',
      type: 'error',
    })
  } catch {
    return
  }

  await runAction(
    'emergency',
    () => emergencyStop({ reason: 'trial_run_console', cancel_orders: true, stop_strategies: true }),
    '急停已发送',
  )
}

async function handleResume() {
  try {
    await ElMessageBox.confirm('确认解除交易急停？', '解除急停', {
      confirmButtonText: '解除急停',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  await runAction('resume', () => resumeTrading(), '交易急停已解除')
}

async function handleCancelAll() {
  try {
    await ElMessageBox.confirm('确认撤销所有活跃委托单？', '一键撤单', {
      confirmButtonText: '全部撤销',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  await runAction('cancelAll', () => cancelAllOrders(), '一键撤单已发送')
}

async function handleQuickClose() {
  if (!allowedSymbol.value) {
    ElMessage.warning('缺少唯一合约，无法快捷平仓')
    return
  }

  const allowMarket = riskConfig.value.allow_market_orders !== false
  const closePrice = firstClosePrice()
  if (!allowMarket && closePrice <= 0) {
    ElMessage.warning('当前风控禁止市价单，且未找到可用平仓限价')
    return
  }
  const orderBody = allowMarket
    ? { volume: 0, price: 0, order_type: 'market' }
    : { volume: 0, price: closePrice, order_type: 'limit' }
  const closeMode = allowMarket ? '市价' : `限价 ${closePrice}`

  try {
    await ElMessageBox.confirm(`确认对 ${allowedSymbol.value} 发起${closeMode}快捷平仓？`, '快捷平仓确认', {
      confirmButtonText: '确认平仓',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  await runAction(
    'close',
    () => closePosition(allowedSymbol.value, orderBody),
    '快捷平仓已发送',
  )
}

onMounted(async () => {
  await loadConfig()
  await refreshAll(true)
  startPolling()
})

onUnmounted(stopPolling)
</script>

<style scoped>
.trial-run-page {
  min-height: 100vh;
  background: var(--q-bg);
  color: var(--q-text);
  overflow-x: hidden;
}

.trial-topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 24px;
  background: rgba(22, 27, 34, .96);
  border-bottom: 1px solid var(--q-border);
}

.topbar-left,
.topbar-right,
.action-row,
.danger-row,
.readiness-meta {
  display: flex;
  align-items: center;
}

.topbar-left {
  gap: 12px;
  min-width: 0;
}

.topbar-right {
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.title-block {
  min-width: 0;
}

.title-block h1 {
  margin: 0;
  color: var(--q-text);
  font-size: 20px;
  line-height: 1.25;
  font-weight: 700;
}

.title-block span,
.panel-head span,
.muted,
.status-label,
.metric-cell span,
.flow-item span {
  color: var(--q-muted);
  font-size: 12px;
}

.poll-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--q-muted);
  flex-shrink: 0;
}

.poll-dot.active {
  background: var(--q-green);
  box-shadow: 0 0 9px rgba(63, 185, 80, .7);
}

.trial-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 24px 40px;
  max-width: 1480px;
  margin: 0 auto;
}

.status-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--q-border);
  border-radius: 8px;
  background: var(--q-border);
  overflow: hidden;
}

.status-cell {
  min-width: 0;
  padding: 10px 12px;
  background: var(--q-panel);
}

.status-label,
.status-value,
.metric-cell span,
.metric-cell strong {
  display: block;
  overflow-wrap: anywhere;
}

.status-value {
  margin-top: 4px;
  color: var(--q-text);
  font-size: 14px;
  font-weight: 650;
}

.ok { color: var(--q-green); }
.bad { color: var(--q-red); }
.warn { color: var(--q-yellow); }
.muted-strong { color: var(--q-muted); }
.mono {
  font-family: var(--q-font-mono);
  font-variant-numeric: tabular-nums;
}
.wrap { white-space: normal; }

.work-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(320px, .8fr);
  gap: 14px;
}

.metrics-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
}

.panel {
  min-width: 0;
  background: var(--q-panel);
  border: 1px solid var(--q-border);
  border-radius: 8px;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--q-border);
  background: rgba(0, 0, 0, .18);
}

.panel-head.compact {
  padding-bottom: 12px;
}

.panel-head h2 {
  margin: 0 0 4px;
  color: var(--q-text);
  font-size: 15px;
  font-weight: 650;
}

.account-form,
.flow-panel .readiness-box,
.risk-grid,
.monitor-grid,
.danger-row {
  padding: 14px 16px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0 12px;
}

.action-row {
  gap: 8px;
  flex-wrap: wrap;
}

.flow-list {
  display: grid;
  gap: 8px;
  padding: 14px 16px 0;
}

.flow-item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: 9px;
  align-items: center;
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid rgba(48, 54, 61, .75);
  border-radius: 7px;
  background: rgba(13, 17, 23, .38);
}

.flow-item strong {
  display: block;
  margin-bottom: 2px;
  font-size: 13px;
  color: var(--q-text);
}

.flow-item.done {
  border-color: rgba(63, 185, 80, .35);
}

.flow-item.active {
  border-color: rgba(88, 166, 255, .45);
}

.flow-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  color: var(--q-muted);
  background: rgba(0, 0, 0, .22);
}

.flow-item.done .flow-mark { color: var(--q-green); }
.flow-item.active .flow-mark { color: var(--q-blue); }

.readiness-box {
  border-top: 1px solid var(--q-border);
}

.readiness-meta {
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.risk-grid,
.monitor-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.monitor-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.metric-cell {
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid rgba(48, 54, 61, .75);
  border-radius: 7px;
  background: rgba(13, 17, 23, .34);
}

.metric-cell strong {
  margin-top: 6px;
  color: var(--q-text);
  font-size: 14px;
}

.danger-row {
  gap: 8px;
  flex-wrap: wrap;
  border-top: 1px solid var(--q-border);
}

.table-panel {
  padding: 0 14px 14px;
}

.table-scroll {
  max-width: 100%;
  overflow-x: auto;
}

.trial-tabs :deep(.el-tabs__header) {
  margin: 0;
}

.trial-tabs :deep(.el-tabs__nav-wrap::after) {
  background: var(--q-border);
}

.trial-tabs :deep(.el-tab-pane) {
  padding-top: 12px;
}

:deep(.el-form-item__label) {
  color: var(--q-muted);
  font-size: 12px;
}

:deep(.el-input__wrapper),
:deep(.el-select .el-input__wrapper) {
  min-width: 0;
}

:deep(.el-button) {
  max-width: 100%;
}

@media (max-width: 1180px) {
  .status-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .work-grid,
  .metrics-grid {
    grid-template-columns: 1fr;
  }

  .form-grid,
  .monitor-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 700px) {
  .trial-topbar {
    align-items: flex-start;
    flex-direction: column;
    padding: 12px;
  }

  .trial-content {
    padding: 12px 10px 28px;
  }

  .status-strip,
  .form-grid,
  .risk-grid,
  .monitor-grid {
    grid-template-columns: 1fr;
  }

  .panel-head,
  .action-row,
  .danger-row {
    align-items: stretch;
    flex-direction: column;
  }

  .action-row > *,
  .danger-row > * {
    width: 100%;
    margin-left: 0 !important;
  }
}
</style>
