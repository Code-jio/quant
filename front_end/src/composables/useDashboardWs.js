/**
 * useDashboardWs.js — 全局仪表盘 WebSocket 可组合函数
 *
 * 连接 /ws/dashboard，每 2 秒接收一次仪表盘指标快照。
 * 支持指数退避自动重连、页面可见性感知暂停。
 */
import { reactive, ref, onUnmounted } from 'vue'
import { buildWsUrl } from '@/config/network.js'

const DEFAULT_DATA = {
  accountId:       '',
  totalPnl:        0,
  returnRate:      0,
  todayReturn:     0,
  balance:         0,
  available:       0,
  margin:          0,
  initialCapital:  1_000_000,
  sharpeRatio:     0,
  maxDrawdownPct:  0,
  totalExposure:   0,
  exposurePct:     0,
  positions:       [],
  equityCurve:     [],
  activeStrategies: 0,
  timestamp:       '',
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
    data.accountId        = payload.account_id       ?? data.accountId
    data.totalPnl         = payload.total_pnl        ?? data.totalPnl
    data.returnRate       = payload.return_rate      ?? data.returnRate
    data.todayReturn      = payload.today_return     ?? data.todayReturn
    data.balance          = payload.balance          ?? data.balance
    data.available        = payload.available        ?? data.available
    data.margin           = payload.margin           ?? data.margin
    data.initialCapital   = payload.initial_capital  ?? data.initialCapital
    data.sharpeRatio      = payload.sharpe_ratio     ?? data.sharpeRatio
    data.maxDrawdownPct   = payload.max_drawdown_pct ?? data.maxDrawdownPct
    data.totalExposure    = payload.total_exposure   ?? data.totalExposure
    data.exposurePct      = payload.exposure_pct     ?? data.exposurePct
    data.positions        = payload.positions        ?? data.positions
    data.equityCurve      = payload.equity_curve     ?? data.equityCurve
    data.activeStrategies = payload.active_strategies ?? data.activeStrategies
    data.timestamp        = payload.timestamp        ?? data.timestamp
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
    if (destroyed) return
    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      connected.value = true
      retryDelay = 1_000
      startPing()
    }

    ws.onmessage = (e) => {
      try { applyPayload(JSON.parse(e.data)) } catch { /* ignore */ }
    }

    ws.onclose = () => {
      connected.value = false
      stopPing()
      if (!destroyed) scheduleRetry()
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
