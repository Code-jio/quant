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
          <span>短预热 · 自动验证 · 单合约风控闭环</span>
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
                <el-icon><VideoPlay /></el-icon>
                {{ prepareButtonLabel }}
              </el-button>
              <el-button v-if="!autoArm" type="success" plain :loading="actionLoading.start" :disabled="!canStart" @click="handleStart">
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
              <strong class="mono">{{ tickCount }} / {{ readinessBars }}</strong>
            </div>
            <el-progress
              :percentage="readinessPercent"
              :stroke-width="8"
              :show-text="false"
              color="#58a6ff"
            />
            <p v-if="marketWarning" class="market-warning">{{ marketWarning }}</p>
            <el-alert
              v-if="executionWarning"
              class="execution-warning"
              :title="executionWarning"
              type="warning"
              show-icon
              :closable="false"
            />
            <div class="diagnostic-grid">
              <div v-for="item in marketDiagnostics" :key="item.label" class="diagnostic-cell">
                <span>{{ item.label }}</span>
                <strong :class="item.className">{{ item.value }}</strong>
              </div>
            </div>
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
              <span>实时权益</span>
              <strong class="mono">{{ formatMoney(accountEquity) }}</strong>
            </div>
            <div class="metric-cell">
              <span>可用资金</span>
              <strong class="mono">{{ formatMoney(accountAvailable) }}</strong>
            </div>
            <div class="metric-cell">
              <span>保证金占用</span>
              <strong class="mono">{{ formatMoney(accountMargin) }}</strong>
            </div>
            <div class="metric-cell">
              <span>浮动盈亏</span>
              <strong class="mono" :class="pnlClass(accountPnl)">{{ formatMoney(accountPnl) }}</strong>
            </div>
            <div class="metric-cell">
              <span>bar_count / warmup_bars</span>
              <strong class="mono">{{ barCount }} / {{ warmupBars }}</strong>
            </div>
            <div class="metric-cell">
              <span>行情 tick / 就绪阈值</span>
              <strong class="mono">{{ tickCount }} / {{ readinessBars }}</strong>
            </div>
            <div class="metric-cell">
              <span>行情诊断</span>
              <strong class="mono" :class="{ warn: Boolean(marketIssue) }">{{ marketDiagnosticLabel }}</strong>
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

            <el-button plain :loading="actionLoading.report" :disabled="!canExportReport" @click="exportTrialRunReport">
              <el-icon><Download /></el-icon>
              导出测试报告
            </el-button>
          </div>
        </div>
      </section>

      <section class="panel simulation-panel">
  <div class="panel-head compact">
    <div>
      <h2>无对手盘模拟验证</h2>
      <span>仅测试/仿真环境可用；模拟成交不是券商真实成交</span>
    </div>
    <el-tag :type="simulationTagType" effect="plain">{{ simulationStateLabel }}</el-tag>
  </div>
  <div class="simulation-body">
    <el-alert v-if="flattenBlocked" class="flatten-band" title="真实持仓尚未归零" type="error" show-icon :closable="false" description="请先通过快捷平仓或人工处置把真实持仓和活动委托归零。" />
    <div class="simulation-flow">
      <el-button type="warning" plain :loading="actionLoading.simulationPrepare" :disabled="!canRequestSimulation" @click="requestSimulationPrepare">
        <el-icon><SwitchButton /></el-icon>
        撤单并准备模拟验证
      </el-button>
      <el-button disabled :loading="simulationCancelPending">
        <el-icon><Timer /></el-icon>
        等待券商撤单确认
      </el-button>
      <el-button type="primary" plain :loading="actionLoading.simulateFill" :disabled="!canSimulateEntryFill" @click="simulateSelectedFill('entry')">
        <el-icon><CircleCheck /></el-icon>
        模拟开仓成交
      </el-button>
      <el-button disabled :loading="simulationHolding">
        <el-icon><Timer /></el-icon>
        等待模拟平仓委托
      </el-button>
      <el-button type="success" plain :loading="actionLoading.simulateFill" :disabled="!canSimulateCloseFill" @click="simulateSelectedFill('close')">
        <el-icon><CircleCheck /></el-icon>
        模拟平仓成交
      </el-button>
    </div>
    <div class="simulation-detail">
      <span>当前委托：{{ currentTrialOrderText }}</span>
      <span>模拟持仓：{{ simulatedPositionVolume }} 手</span>
      <span>券商持仓：{{ brokerPositionVolume }} 手</span>
      <span>持仓截止：{{ formatTimeValue(holdDeadlineAt) }}</span>
    </div>
  </div>
</section>
<section class="panel table-panel">
        <el-tabs v-model="activeTab" class="trial-tabs">
          <el-tab-pane label="试运行订单链" name="trial-chain">
            <div class="table-scroll">
              <el-table :data="trialOrderChain" size="small" height="320" empty-text="暂无试运行订单" :row-class-name="trialOrderRowClass">
                <el-table-column label="当前" width="64">
                  <template #default="{ row }">
                    <el-tag v-if="row.order_id === currentOrderId" size="small" type="primary" effect="plain">当前</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="次序" width="96">
                  <template #default="{ row }">{{ row.sequence }}</template>
                </el-table-column>
                <el-table-column prop="order_id" label="委托号" min-width="130" show-overflow-tooltip />
                <el-table-column label="来源" width="80">
                  <template #default="{ row }">{{ row.track === 'simulated' ? '模拟' : '真实' }}</template>
                </el-table-column>
                <el-table-column prop="symbol" label="合约" min-width="100" show-overflow-tooltip />
                <el-table-column label="方向" width="72">
                  <template #default="{ row }">
                    <el-tag :type="directionType(row.direction)" size="small" effect="plain">{{ directionLabel(row.direction) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="开平" width="72">
                  <template #default="{ row }">{{ offsetLabel(row.offset) }}</template>
                </el-table-column>
                <el-table-column prop="price" label="委托价格" width="96" />
                <el-table-column label="数量/已成" width="96">
                  <template #default="{ row }">{{ row.volume }} / {{ row.status === 'filled' ? row.volume : 0 }}</template>
                </el-table-column>
                <el-table-column label="委托时间" min-width="150" show-overflow-tooltip>
                  <template #default="{ row }">{{ formatRowTime(row) }}</template>
                </el-table-column>
                <el-table-column label="状态" min-width="110" show-overflow-tooltip>
                  <template #default="{ row }">
                    <el-tag :type="orderStatusType(row.status)" size="small" effect="plain">{{ orderStatusLabel(row.status) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="撤单/失败原因" min-width="140" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.failure_reason || row.error_msg || (row.status === 'rejected' ? '券商拒单' : '--') }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>
          <el-tab-pane label="订单" name="orders">
            <div class="table-scroll">
              <el-table :data="orders" size="small" height="320" empty-text="暂无订单">
                <el-table-column prop="order_id" label="委托号" min-width="120" show-overflow-tooltip />
                <el-table-column prop="symbol" label="合约" min-width="100" show-overflow-tooltip />
                <el-table-column label="方向" width="86">
                  <template #default="{ row }">
                    <el-tag :type="directionType(row.direction)" size="small" effect="plain">
                      {{ directionLabel(row.direction) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="开平" width="76">
                  <template #default="{ row }">{{ offsetLabel(row.offset) }}</template>
                </el-table-column>
                <el-table-column prop="price" label="价格" width="92" />
                <el-table-column prop="volume" label="数量" width="80" />
                <el-table-column label="状态" min-width="110" show-overflow-tooltip>
                  <template #default="{ row }">
                    <el-tag :type="orderStatusType(row.status)" size="small" effect="plain">
                      {{ orderStatusLabel(row.status) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="时间" min-width="150" show-overflow-tooltip>
                  <template #default="{ row }">{{ formatRowTime(row) }}</template>
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
                <el-table-column label="方向" width="86">
                  <template #default="{ row }">
                    <el-tag :type="directionType(row.direction)" size="small" effect="plain">
                      {{ directionLabel(row.direction) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="price" label="价格" width="92" />
                <el-table-column prop="volume" label="数量" width="80" />
                <el-table-column label="时间" min-width="150" show-overflow-tooltip>
                  <template #default="{ row }">{{ formatRowTime(row) }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>

          <el-tab-pane label="持仓" name="positions">
            <div class="table-scroll">
              <el-table :data="positions" size="small" height="320" empty-text="暂无持仓">
                <el-table-column prop="symbol" label="合约" min-width="110" show-overflow-tooltip />
                <el-table-column label="方向" width="86">
                  <template #default="{ row }">
                    <el-tag :type="directionType(row.direction)" size="small" effect="plain">
                      {{ positionDirectionLabel(row.direction) }}
                    </el-tag>
                  </template>
                </el-table-column>
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
  Download,
  Lock,
  Operation,
  RefreshRight,
  SwitchButton,
  Timer,
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
  fetchTradingReconcile,
  fetchTrialRunConfig,
  fetchTrialRunStatus,
  downloadTrialRunReport,
  login,
  prepareTrialRunSimulation,
  prepareTrialRun,
  resetTrialRun,
  resumeTrading,
  simulateTrialRunFill,
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
const accountSnapshot = ref({})
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
  simulationPrepare: false,
  simulateFill: false,
  report: false,
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

const MARKET_ISSUE_LABELS = {
  no_tick_timeout: '未收到目标 tick',
  stale_market_data: '行情已过期',
  market_data_timestamp_unavailable: '行情无时间戳',
  symbol_mismatch: '合约不匹配',
  first_tick_bar_not_emitted: '首 tick Bar 未生成',
  invalid_tick_price: 'tick 价格无效',
}

const ACTIVE_ORDER_STATUSES = new Set(['submitting', 'submitted', 'partfilled'])
const ORDER_STATUS_LABELS = {
  submitting: '提交中',
  submitted: '已报未成',
  partfilled: '部分成交',
  filled: '全部成交',
  cancelled: '已撤单',
  rejected: '已拒单',
}
const OFFSET_LABELS = {
  open: '开仓',
  close: '平仓',
  close_today: '平今',
  close_yesterday: '平昨',
}
const DIRECTION_LABELS = {
  long: '多',
  short: '空',
  net: '净持仓',
}
const POSITION_DIRECTION_LABELS = {
  long: '多',
  short: '空',
  net: '净',
}

let pollTimer = null
let simulationWaitTimer = null

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
const autoArm = computed(() => boolOf(
  trialStatus.value.auto_arm
  ?? config.value.auto_arm
  ?? config.value.trial_run?.auto_arm,
  true,
))
const barCount = computed(() => numberOf(trialStatus.value.bar_count ?? statusSnapshot.value.bar_count ?? trialStatus.value.bars, 0))
const warmupBars = computed(() => Math.max(1, numberOf(trialStatus.value.warmup_bars ?? statusSnapshot.value.warmup_bars, 1)))
const noBarWaitSeconds = computed(() => numberOf(trialStatus.value.no_bar_wait_seconds, 0))
const marketWarning = computed(() => trialStatus.value.market_warning || '')
const marketIssue = computed(() => String(trialStatus.value.market_issue || ''))
const marketDiagnosticLabel = computed(() => {
  if (marketIssue.value) return MARKET_ISSUE_LABELS[marketIssue.value] || marketIssue.value
  if (barCount.value > 0) return '首根 Bar 已生成'
  if (tickCount.value > 0) return '已收到 tick'
  if (prepared.value) return `等待 tick ${Math.round(noBarWaitSeconds.value)}s`
  return '--'
})
const tickCount = computed(() => numberOf(trialStatus.value.tick_count ?? statusSnapshot.value.tick_count, barCount.value))
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
  return Math.min(100, Math.round((tickCount.value / readinessBars.value) * 100))
})
const prepared = computed(() => Boolean(trialStatus.value.prepared || ['prepared', 'waiting_market_data', 'warming', 'warmup', 'ready_to_start', 'ready_to_arm', 'started', 'armed', 'entry_pending', 'holding', 'closing', 'running', 'completed'].includes(statusCode.value)))
const started = computed(() => Boolean(trialStatus.value.started || statusSnapshot.value.started || ['started', 'armed', 'entry_pending', 'holding', 'closing', 'running', 'completed'].includes(statusCode.value)))
const closedLoop = computed(() => Boolean(trialStatus.value.completed || statusSnapshot.value.completed || trialStatus.value.closed_loop_completed || statusCode.value === 'completed'))
const statusCode = computed(() => String(trialStatus.value.status || trialStatus.value.state || 'idle').toLowerCase())
const trialStatusLabel = computed(() => {
  const outcome = String(trialStatus.value.outcome || '')
  if (outcome === 'passed_real') return '真实成交闭环通过'
  if (outcome === 'passed_simulated') return '真实报单链路通过；成交后处理由模拟成交验证'
  if (outcome === 'failed') return '试运行失败'
  if (outcome === 'aborted') return '试运行中止'
  return STATUS_LABELS[statusCode.value] || trialStatus.value.status || trialStatus.value.state || '待准备'
})
const trialStatusType = computed(() => {
  if (marketIssue.value) return 'danger'
  if (['error', 'emergency_stopped'].includes(statusCode.value)) return 'danger'
  if (['started', 'armed', 'entry_pending', 'holding', 'running', 'completed'].includes(statusCode.value)) return 'success'
  if (['waiting_market_data', 'warming', 'warmup', 'prepared', 'ready_to_start', 'ready_to_arm', 'closing'].includes(statusCode.value)) return 'warning'
  return 'info'
})

const emergencyActive = computed(() => Boolean(riskConfig.value.emergency_stop || riskStatus.value.emergency_stop))
const activeOrderCount = computed(() => orders.value.filter(order => ACTIVE_ORDER_STATUSES.has(normalizeCode(order.status))).length)
const executionIssue = computed(() => String(
  trialStatus.value.execution_issue
  || trialStatus.value.execution_error
  || statusSnapshot.value.execution_issue
  || '',
))
const executionWarning = computed(() => String(
  trialStatus.value.execution_warning
  || statusSnapshot.value.execution_warning
  || executionIssue.value
  || '',
))

const simulationPrepareAllowed = computed(() => boolOf(
  trialStatus.value.simulation_prepare_allowed
  ?? statusSnapshot.value.simulation_prepare_allowed,
  false,
))
const simulateFillAllowed = computed(() => boolOf(
  trialStatus.value.simulate_fill_allowed
  ?? statusSnapshot.value.simulate_fill_allowed,
  false,
))
const currentOrderId = computed(() => String(trialStatus.value.current_order_id || ''))
const trialOrderChain = computed(() => (
  Array.isArray(trialStatus.value.order_chain)
    ? trialStatus.value.order_chain.map((order, index) => ({
        ...order,
        sequence: Number(order.attempt) === 0 ? '首单' : `追价 ${Number(order.attempt) || ''}`,
        index,
      }))
    : []
))
const currentTrialOrder = computed(() => trialOrderChain.value.find(order => order.order_id === currentOrderId.value) || null)
const simulatedPositionVolume = computed(() => numberOf(trialStatus.value.simulated_position_volume, 0))
const brokerPositionVolume = computed(() => numberOf(trialStatus.value.broker_position_volume, 0))
const holdDeadlineAt = computed(() => trialStatus.value.hold_deadline_at || statusSnapshot.value.hold_deadline_at || '')
const simulationState = computed(() => String(
  trialStatus.value.simulation_state
  || statusSnapshot.value.simulation_state
  || 'not_started',
))
const simulationCancelPending = computed(() => ['cancel_pending', 'simulation_cancel_pending'].includes(simulationState.value))
const simulationReady = computed(() => simulationState.value === 'ready')
const simulationHolding = computed(() => simulationState.value === 'holding')
const simulationClosing = computed(() => simulationState.value === 'closing')
const simulationFlat = computed(() => ['flat', 'passed_simulated', 'passed_real', 'failed', 'aborted'].includes(simulationState.value) || terminalOutcome.value)
const terminalOutcome = computed(() => ['passed_real', 'passed_simulated', 'failed', 'aborted'].includes(String(trialStatus.value.outcome || '')))
const flattenBlocked = computed(() => ['flatten_required', 'flatten_required_market_data'].includes(String(trialStatus.value.failure_code || '')))
const canExportReport = computed(() => hasSession.value && terminalOutcome.value && !actionLoading.report)
const canRequestSimulation = computed(() => (
  hasSession.value
  && connected.value
  && simulationPrepareAllowed.value
  && !flattenBlocked.value
  && !terminalOutcome.value
  && Boolean(currentOrderId.value)
  && !actionLoading.simulationPrepare
))
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
  && !autoArm.value
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
const canSimulateFill = computed(() => (
  hasSession.value
  && connected.value
  && simulateFillAllowed.value
  && Boolean(currentTrialOrder.value)
  && !flattenBlocked.value
  && !terminalOutcome.value
  && !actionLoading.simulateFill
))
const canSimulateEntryFill = computed(() => canSimulateFill.value && ['open', 'entry'].includes(normalizeCode(currentTrialOrder.value?.offset)))
const canSimulateCloseFill = computed(() => canSimulateFill.value && ['close', 'exit'].includes(normalizeCode(currentTrialOrder.value?.offset)))
const prepareButtonLabel = computed(() => autoArm.value ? '开始试运行' : '准备策略')
const accountEquity = computed(() => numberOf(accountSnapshot.value.balance ?? accountSnapshot.value.equity, 0))
const accountAvailable = computed(() => numberOf(accountSnapshot.value.available, 0))
const accountMargin = computed(() => numberOf(accountSnapshot.value.margin, 0))
const accountPnl = computed(() => numberOf(accountSnapshot.value.total_pnl ?? accountSnapshot.value.position_pnl, 0))

const statusItems = computed(() => [
  { label: '账号', value: accountForm.username || authStatus.value.account_id || config.value.masked_account_id || '--' },
  { label: '环境', value: accountForm.environment || config.value.environment || '--' },
  { label: '实时权益', value: formatMoney(accountEquity.value), className: 'mono' },
  { label: '唯一合约', value: allowedSymbol.value || '--', className: 'mono' },
  { label: '连接状态', value: connected.value ? '已连接' : '未连接', className: connected.value ? 'ok' : 'muted-strong' },
  { label: '授权模式', value: autoArm.value ? '自动' : '手动' },
  { label: '试运行状态', value: trialStatusLabel.value, className: trialStatusType.value === 'danger' ? 'bad' : '' },
  { label: '急停状态', value: emergencyActive.value ? '急停中' : '正常', className: emergencyActive.value ? 'bad' : 'ok' },
])

const lastMarketPrice = computed(() => numberOf(
  trialStatus.value.last_market_price ?? statusSnapshot.value.last_market_price,
  0,
))
const lastMarketTimestamp = computed(() => (
  trialStatus.value.last_market_timestamp
  || statusSnapshot.value.last_market_timestamp
  || ''
))
const marketDataAgeSeconds = computed(() => numberOf(
  trialStatus.value.market_data_age_seconds ?? statusSnapshot.value.market_data_age_seconds,
  0,
))
const latestMarketDetail = computed(() => {
  if (!lastMarketTimestamp.value && lastMarketPrice.value <= 0) return ''
  const price = lastMarketPrice.value > 0 ? ` ${lastMarketPrice.value}` : ''
  const time = lastMarketTimestamp.value ? ` ${String(lastMarketTimestamp.value).replace('T', ' ').slice(0, 19)}` : ''
  const age = marketDataAgeSeconds.value > 0 ? ` ${Math.round(marketDataAgeSeconds.value)}秒前` : ''
  return `${time}${price}${age}`.trim()
})

const subscribedSymbolsText = computed(() => {
  const value = trialStatus.value.subscribed_symbols
  if (Array.isArray(value) && value.length) return value.join(', ')
  return '--'
})
const firstTickBarEnabled = computed(() => Boolean(trialStatus.value.first_tick_bar_enabled))
const firstTickBarEmitted = computed(() => Boolean(trialStatus.value.first_tick_bar_emitted))
const firstTickBarSkipReason = computed(() => String(trialStatus.value.first_tick_bar_skip_reason || ''))
const firstTickBarText = computed(() => {
  if (!firstTickBarEnabled.value) return '未启用'
  if (firstTickBarEmitted.value) return '已生成'
  if (firstTickBarSkipReason.value) {
    return MARKET_ISSUE_LABELS[firstTickBarSkipReason.value] || firstTickBarSkipReason.value
  }
  return '等待首个 tick'
})
const lastOrderPrice = computed(() => numberOf(
  trialStatus.value.last_order_price ?? statusSnapshot.value.last_order_price,
  0,
))
const lastOrderPricingSource = computed(() => String(
  trialStatus.value.last_order_pricing_source || statusSnapshot.value.last_order_pricing_source || '',
))
const orderPricingText = computed(() => {
  if (lastOrderPrice.value <= 0) return '--'
  const source = lastOrderPricingSource.value ? ` (${lastOrderPricingSource.value})` : ''
  return `${lastOrderPrice.value}${source}`
})
const chaseText = computed(() => {
  const enabled = Boolean(trialStatus.value.chase_enabled ?? statusSnapshot.value.chase_enabled)
  if (!enabled) return '未启用'
  const attempts = numberOf(trialStatus.value.chase_attempts ?? statusSnapshot.value.chase_attempts, 0)
  const maxAttempts = numberOf(trialStatus.value.chase_max_attempts ?? statusSnapshot.value.chase_max_attempts, 0)
  const pendingCancel = trialStatus.value.chase_pending_cancel_order_id || statusSnapshot.value.chase_pending_cancel_order_id || ''
  const resubmitReady = Boolean(trialStatus.value.chase_resubmit_ready ?? statusSnapshot.value.chase_resubmit_ready)
  const lastPrice = numberOf(trialStatus.value.last_chase_price ?? statusSnapshot.value.last_chase_price, 0)
  const reason = trialStatus.value.last_chase_reason || statusSnapshot.value.last_chase_reason || ''
  if (pendingCancel) return `撤单中 ${attempts}/${maxAttempts} ${pendingCancel}`
  if (resubmitReady) return `待重报 ${attempts}/${maxAttempts}`
  if (attempts > 0) return `已追价 ${attempts}/${maxAttempts}${lastPrice > 0 ? ` @${lastPrice}` : ''}${reason ? ` ${reason}` : ''}`
  return `待触发 0/${maxAttempts}`
})
const marketDiagnostics = computed(() => [
  { label: '订阅合约', value: subscribedSymbolsText.value, className: 'mono wrap' },
  { label: '最近 tick', value: latestMarketDetail.value || '--', className: 'mono wrap' },
  { label: 'tick / bar', value: `${tickCount.value} / ${barCount.value}`, className: 'mono' },
  { label: '委托价格', value: orderPricingText.value, className: 'mono wrap' },
  { label: '追价状态', value: chaseText.value, className: 'mono wrap' },
  { label: '首 tick Bar', value: firstTickBarText.value, className: firstTickBarEmitted.value ? 'ok' : marketIssue.value ? 'warn' : '' },
  { label: '失败原因', value: marketDiagnosticLabel.value, className: marketIssue.value ? 'warn' : '' },
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
    detail: marketReady.value
      ? `已收到有效行情 tick${latestMarketDetail.value ? `：${latestMarketDetail.value}` : ''}`
      : latestMarketDetail.value
        ? `最近 tick：${latestMarketDetail.value}，等待新 tick`
        : '等待首个有效行情 tick',
    state: marketReady.value ? 'done' : prepared.value ? 'active' : 'idle',
  },
  {
    label: autoArm.value ? '自动验证' : '授权交易',
    detail: autoArm.value
      ? '行情就绪后自动发出 1 手验证开仓'
      : started.value ? '1 手验证开仓已授权' : '需二次确认',
    state: autoArm.value
      ? marketReady.value ? 'done' : prepared.value ? 'active' : 'idle'
      : started.value ? 'done' : marketReady.value ? 'active' : 'idle',
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
  { label: 'rate_limit_remaining', value: displayValue(trialStatus.value.rate_limit_remaining), className: 'mono' },
  { label: '下次追价', value: rateLimitRetryText.value, className: 'mono' },
  { label: '券商持仓', value: `${brokerPositionVolume.value} 手`, className: brokerPositionVolume.value ? 'bad' : 'ok' },
  { label: '持仓截止', value: formatTimeValue(holdDeadlineAt.value), className: 'mono wrap' },
  { label: 'last_reject_reason', value: trialStatus.value.last_reject_reason || riskStatus.value.last_reject_reason || riskConfig.value.last_reject_reason || '--', className: 'wrap warn' },
])

const simulationStateLabel = computed(() => ({
  not_started: '未开始',
  cancel_pending: '等待撤单确认',
  simulation_cancel_pending: '等待撤单确认',
  ready: '可模拟开仓',
  holding: '模拟持仓中',
  closing: '等待模拟平仓',
  flat: '模拟已归零',
  cancelled: '已取消',
  failed: '模拟失败',
  real_track: '真实轨道',
  real_cancelled: '真实已撤',
})[simulationState.value] || simulationState.value)
const simulationTagType = computed(() => {
  if (flattenBlocked.value || simulationState.value === 'failed') return 'danger'
  if (simulationCancelPending.value || simulationHolding.value || simulationClosing.value) return 'warning'
  if (simulationReady.value || simulationFlat.value) return 'success'
  return 'info'
})
const currentTrialOrderText = computed(() => {
  if (!currentTrialOrder.value) return '--'
  const order = currentTrialOrder.value
  return `${order.order_id} ${directionLabel(order.direction)} ${offsetLabel(order.offset)} @${order.price}`
})
const rateLimitRetryText = computed(() => {
  const seconds = numberOf(trialStatus.value.rate_limit_retry_after_seconds, 0)
  return seconds > 0 ? `${seconds}s` : '--'
})
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

function boolOf(value, fallback = false) {
  if (value === undefined || value === null || value === '') return fallback
  if (typeof value === 'boolean') return value
  const normalized = String(value).trim().toLowerCase()
  if (['1', 'true', 'yes', 'on', 'enabled'].includes(normalized)) return true
  if (['0', 'false', 'no', 'off', 'disabled'].includes(normalized)) return false
  return fallback
}

function displayValue(value) {
  return value === undefined || value === null || value === '' ? '--' : String(value)
}

function normalizeCode(value) {
  return String(value || '').trim().toLowerCase()
}

function directionLabel(value) {
  const code = normalizeCode(value)
  return DIRECTION_LABELS[code] || displayValue(value)
}

function positionDirectionLabel(value) {
  const code = normalizeCode(value)
  return POSITION_DIRECTION_LABELS[code] || directionLabel(value)
}

function directionType(value) {
  const code = normalizeCode(value)
  if (code === 'long') return 'success'
  if (code === 'short') return 'danger'
  return 'info'
}

function offsetLabel(value) {
  const code = normalizeCode(value)
  return OFFSET_LABELS[code] || displayValue(value)
}

function orderStatusLabel(value) {
  const code = normalizeCode(value)
  return ORDER_STATUS_LABELS[code] || displayValue(value)
}

function orderStatusType(value) {
  const code = normalizeCode(value)
  if (code === 'filled') return 'success'
  if (code === 'rejected' || code === 'cancelled') return 'danger'
  if (code === 'partfilled') return 'warning'
  if (ACTIVE_ORDER_STATUSES.has(code)) return 'primary'
  return 'info'
}

function formatDateTime(date) {
  const pad = value => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatTimeValue(value) {
  if (value === undefined || value === null || value === '') return '--'
  const text = String(value).trim()
  if (!text || text === '--') return '--'
  if (/^\d{2}:\d{2}(:\d{2})?$/.test(text)) return text
  const parsed = new Date(text)
  if (!Number.isNaN(parsed.getTime())) return formatDateTime(parsed)
  return text
}

function formatRowTime(row) {
  return formatTimeValue(pick(row, [
    'create_ts',
    'create_time',
    'update_ts',
    'update_time',
    'timestamp',
    'datetime',
    'time',
    'trade_time',
  ]))
}

function trialOrderRowClass({ row }) {
  return row.order_id === currentOrderId.value ? 'current-order-row' : ''
}
function formatMoney(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n === 0) return '--'
  return `¥${n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function pnlClass(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'ok' : 'bad'
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
    trial_run: trialRun,
    trading,
    strategy,
    risk,
    auto_arm: boolOf(data.auto_arm ?? trialRun.auto_arm, true),
    bar_timeout_seconds: numberOf(data.bar_timeout_seconds ?? trialRun.bar_timeout_seconds, 90),
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
  const quietNoRedirect = { redirectOn401: false, suppressErrorLog: true }
  const results = await Promise.allSettled([
    fetchRiskStatus(noRedirect),
    fetchOrders(noRedirect),
    fetchTrades(noRedirect),
    fetchPositions(noRedirect),
    fetchSystemLogs({ limit: 200 }, noRedirect),
    connected.value ? fetchTradingReconcile(quietNoRedirect) : Promise.resolve(null),
  ])

  if (results.some(result => result.status === 'rejected' && /未登录|401/.test(String(result.reason?.message || '')))) {
    authStore.clearAuth()
  }

  if (results[0].status === 'fulfilled') riskStatus.value = results[0].value || {}
  if (results[1].status === 'fulfilled') orders.value = normalizeList(results[1].value, ['orders'])
  if (results[2].status === 'fulfilled') trades.value = normalizeList(results[2].value, ['trades'])
  if (results[3].status === 'fulfilled') positions.value = normalizeList(results[3].value, ['positions'])
  if (results[4].status === 'fulfilled') logs.value = normalizeList(results[4].value, ['logs'])
  if (results[5].status === 'fulfilled' && results[5].value) {
    accountSnapshot.value = objectOrNull(results[5].value?.account) || {}
  }

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
    accountSnapshot.value = { ...accountSnapshot.value, balance: res.balance }
    accountForm.password = ''
  }, '账户连接成功')
}

async function handlePrepare() {
  if (autoArm.value && allowedSymbol.value) {
    try {
      await ElMessageBox.confirm(
        `开始后将等待首根有效 Bar，并自动对 ${allowedSymbol.value} 发出 1 手验证开仓。确认继续？`,
        '开始试运行确认',
        { confirmButtonText: '开始试运行', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  await runAction(
    'prepare',
    () => prepareTrialRun({ symbol: allowedSymbol.value }),
    autoArm.value ? '试运行已开始，等待首根有效 Bar' : '策略准备已发送',
  )
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

async function requestSimulationPrepare() {
  const orderId = currentOrderId.value
  if (!orderId) {
    ElMessage.warning('当前没有可迁移到模拟账本的真实委托')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认撤单 ${orderId} 并准备隔离模拟账本？后续模拟成交不会写入券商账本。`,
      '准备模拟验证确认',
      { confirmButtonText: '撤单并准备', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  await runAction(
    'simulationPrepare',
    () => prepareTrialRunSimulation({ source_order_id: orderId }),
    '已请求券商撤单，开始等待确认',
  )
  if (!terminalOutcome.value) startSimulationWaitPolling()
}

function stopSimulationWaitPolling() {
  if (simulationWaitTimer) {
    clearInterval(simulationWaitTimer)
    simulationWaitTimer = null
  }
}

function startSimulationWaitPolling() {
  stopSimulationWaitPolling()
  simulationWaitTimer = setInterval(async () => {
    if (terminalOutcome.value || simulationReady.value || simulationFlat.value) {
      stopSimulationWaitPolling()
      return
    }
    try {
      const response = await prepareTrialRunSimulation({ source_order_id: currentOrderId.value })
      if (response?.status) trialStatus.value = response.status
      await refreshAll(true)
      if (simulationReady.value || simulationFlat.value || terminalOutcome.value) {
        stopSimulationWaitPolling()
      }
    } catch (err) {
      stopSimulationWaitPolling()
      ElMessage.error(err.message || '等待券商撤单确认失败')
    }
  }, 1000)
}

async function simulateSelectedFill(kind = 'entry') {
  const order = currentTrialOrder.value
  if (!order) {
    ElMessage.warning('暂无可模拟成交的当前委托')
    return
  }
  const actionLabel = kind === 'close' ? '平仓' : '开仓'
  const detail = `${order.order_id} | ${allowedSymbol.value || order.symbol} | ${directionLabel(order.direction)} | ${offsetLabel(order.offset)} | ${order.price} | ${order.volume} 手`
  try {
    await ElMessageBox.confirm(
      `确认模拟${actionLabel}成交？${detail}。此操作只写入隔离模拟账本，不是券商真实成交。`,
      '模拟成交确认',
      { confirmButtonText: '确认模拟', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  await runAction(
    'simulateFill',
    () => simulateTrialRunFill({ order_id: order.order_id }),
    `模拟${actionLabel}成交已记录`,
  )
}

async function exportTrialRunReport() {
  actionLoading.report = true
  try {
    const blob = await downloadTrialRunReport()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    const timestamp = formatDateTime(new Date()).replace(/[-:\s]/g, '')
    link.href = url
    link.download = `trial-run-report-${timestamp}.docx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('测试报告下载已开始')
  } catch (err) {
    ElMessage.error(err.message || '导出测试报告失败')
  } finally {
    actionLoading.report = false
  }
}

onMounted(async () => {
  await loadConfig()
  await refreshAll(true)
  startPolling()
})

onUnmounted(() => {
  stopPolling()
  stopSimulationWaitPolling()
})
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

.market-warning {
  margin: 10px 0 0;
  color: var(--q-yellow);
  font-size: 13px;
  line-height: 1.55;
}

.execution-warning {
  margin-top: 10px;
}

.diagnostic-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.diagnostic-cell {
  min-width: 0;
  padding: 8px 9px;
  border: 1px solid rgba(48, 54, 61, .7);
  border-radius: 7px;
  background: rgba(13, 17, 23, .3);
}

.diagnostic-cell span,
.diagnostic-cell strong {
  display: block;
}

.diagnostic-cell span {
  color: var(--q-muted);
  font-size: 12px;
}

.diagnostic-cell strong {
  margin-top: 4px;
  color: var(--q-text);
  font-size: 13px;
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
  grid-template-columns: repeat(5, minmax(0, 1fr));
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
  .diagnostic-grid,
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
.simulation-panel {
  margin-top: 14px;
}

.simulation-body {
  display: grid;
  gap: 12px;
  padding: 14px 16px;
}

.simulation-flow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.simulation-flow .el-button + .el-button {
  margin-left: 0;
}

.simulation-detail {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 6px 12px;
  color: var(--q-muted);
  font-size: 12px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.flatten-band {
  margin-bottom: 4px;
}

:deep(.current-order-row) {
  background: rgba(88, 166, 255, .10);
}

</style>
