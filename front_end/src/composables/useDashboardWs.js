/**
 * useDashboardWs.js — 全局仪表盘 WebSocket 可组合函数
 *
 * 连接 /ws/dashboard，每 2 秒接收一次仪表盘指标快照。
 * 支持指数退避自动重连、页面可见性感知暂停。
 */
import { reactive, ref, onUnmounted } from 'vue'
import { finiteAmount } from '@/utils/financialData.js'
import { buildWsUrl } from '@/config/network.js'

const FINANCIAL_FIELDS = {
  totalPnl: 'total_pnl', positionPnl: 'position_pnl', returnRate: 'return_rate', todayReturn: 'today_return',
  balance: 'balance', available: 'available', margin: 'margin', commission: 'commission',
  initialCapital: 'initial_capital', sharpeRatio: 'sharpe_ratio', maxDrawdownPct: 'max_drawdown_pct',
  totalExposure: 'total_exposure', exposurePct: 'exposure_pct', sessionStartBalance: 'session_start_balance',
  sessionBalanceChangePct: 'session_balance_change_pct',
}
const DEFAULT_DATA = {
  ...Object.fromEntries(Object.keys(FINANCIAL_FIELDS).map(key => [key, null])),
  accountId: '', accountKnown: false, accountSource: 'unavailable', positionsKnown: false,
  tradingDay: '', performanceNote: '', exposureBasis: '', positions: [], equityCurve: [],
  activeStrategies: 0, timestamp: '',
}

const MAX_BACKOFF = 30_000
const PING_INTERVAL = 20_000

export function useDashboardWs(url) {
  const wsUrl = url ?? buildWsUrl('/ws/dashboard')

  const connected = ref(false)
  const data      = reactive({ ...DEFAULT_DATA })

  let ws          = null
  let retryTimer  = null
  let pingTimer   = null
  let retryDelay  = 1_000
  let destroyed   = false

  function applyPayload(payload) {
    if (!payload || payload.type !== 'dashboard_metrics') return
    for (const [key, source] of Object.entries(FINANCIAL_FIELDS)) data[key] = payload.account_known === true ? finiteAmount(payload[source]) : null
    data.accountId = payload.account_id ?? ''
    data.accountKnown = payload.account_known === true
    data.accountSource = payload.account_source ?? 'unavailable'
    data.positionsKnown = payload.positions_known === true
    data.tradingDay = payload.trading_day ?? ''
    data.performanceNote = payload.performance_note ?? ''
    data.exposureBasis = payload.exposure_basis ?? ''
    data.positions = payload.positions ?? []
    data.equityCurve = payload.equity_curve ?? []
    data.activeStrategies = payload.active_strategies ?? 0
    data.timestamp = payload.timestamp ?? ''

  }

  function startPing() {
    stopPing()
    pingTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, PING_INTERVAL)
  }

  function stopPing() {
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
  }

  function connect() {
    if (destroyed || sessionStorage.getItem('quant_session_active')!=='1') return
    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      connected.value = true
      retryDelay = 1_000
      startPing()
    }

    ws.onmessage = (e) => {
      try { applyPayload(JSON.parse(e.data)) } catch { /* ignore */ }
    }

    ws.onclose = (event) => {
      connected.value = false
      Object.assign(data, DEFAULT_DATA)
      stopPing()
      if (!destroyed && event.code!==1008) scheduleRetry()
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  function scheduleRetry() {
    retryTimer = setTimeout(() => {
      if (!destroyed) connect()
    }, retryDelay)
    retryDelay = Math.min(retryDelay * 2, MAX_BACKOFF)
  }

  function disconnect() {
    destroyed = true
    stopPing()
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
    ws?.close()
  }

  connect()
  onUnmounted(disconnect)

  return { connected, data, disconnect, reconnect: () => { retryDelay = 1_000; connect() } }
}
