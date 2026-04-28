/**
 * useWatchWs — 盯盘系统 WebSocket 实时数据服务（单例模式）
 *
 * 功能：
 *  1. 订阅管理  — subscribe / unsubscribe，支持多品种同时订阅
 *  2. Tick 推送 — 每品种最新价、买卖盘、成交量等实时更新
 *  3. K 线更新  — 当前周期 K 线实时推送（bar 形成中）
 *  4. 价格异动  — 涨跌幅超阈值 / 单笔跳价 触发告警
 *  5. 成交量异动 — 当前量超均量 N 倍触发告警
 *  6. 自动重连  — 指数退避，最大 30s
 *  7. 心跳检测  — 定时 ping + pong 超时主动断线重连
 *  8. 可见性感知 — 页面隐藏时暂停心跳，恢复时检查连接
 *
 * WebSocket 端点：/ws/watch
 *
 * 消息协议（JSON）：
 *   客户端 → 服务端
 *     { type: 'subscribe',   symbols: ['IF2506'], channels: ['tick','kline_1m'] }
 *     { type: 'unsubscribe', symbols: ['IF2506'] }
 *
 *   服务端 → 客户端
 *     { type: 'subscribed',   symbols: ['IF2506'] }
 *     { type: 'tick',         symbol, last, open, high, low, pre_close, volume,
 *                             turnover, open_interest, bid1, ask1, bid1_vol, ask1_vol,
 *                             change, change_rate, time }
 *     { type: 'kline_update', symbol, interval, bar: {time,open,high,low,close,volume} }
 *     { type: 'error',        code, msg }
 *
 *   心跳（纯文本帧）：
 *     客户端发 'ping' → 服务端回 'pong'
 */

import { ref, reactive, computed, onUnmounted } from 'vue'
import { useAlertConfig } from '@/composables/useAlertConfig.js'
import { buildWsUrl } from '@/config/network.js'

// ── 常量 ──────────────────────────────────────────────────────────────────
const WS_PATH        = '/ws/watch'
const HEARTBEAT_MS   = 20_000   // 心跳间隔
const PONG_TIMEOUT   = 10_000   // pong 等待超时
const RECONNECT_BASE = 1_000    // 初始重连延迟
const RECONNECT_MAX  = 30_000   // 最大重连延迟
const MAX_ALERTS     = 100      // 最多保留告警条数
const MAX_TICK_HIST  = 50       // 每品种保留 tick 历史条数（用于均量计算）
const TICK_FLUSH_MS  = 80       // tick 批量写入间隔，约 12 fps

// ── 工厂：空 Tick 对象 ─────────────────────────────────────────────────────
function emptyTick(symbol = '') {
  return {
    symbol,
    last:         0,
    open:         0,
    high:         0,
    low:          0,
    preClose:     0,
    volume:       0,
    turnover:     0,
    openInterest: 0,
    bid1: 0, bid2: 0, bid3: 0, bid4: 0, bid5: 0,
    ask1: 0, ask2: 0, ask3: 0, ask4: 0, ask5: 0,
    bid1Vol: 0, bid2Vol: 0, bid3Vol: 0, bid4Vol: 0, bid5Vol: 0,
    ask1Vol: 0, ask2Vol: 0, ask3Vol: 0, ask4Vol: 0, ask5Vol: 0,
    change:     0,
    changeRate: 0,
    time:       '',
    updatedAt:  0,
  }
}

// ── 工厂：空 Bar 对象 ──────────────────────────────────────────────────────
function emptyBar(symbol = '', interval = '1m') {
  return { symbol, interval, time: '', open: 0, high: 0, low: 0, close: 0, volume: 0, updatedAt: 0 }
}

// ══════════════════════════════════════════════════════════════════════════
// 单例状态（模块级，所有组件共享同一份数据）
// ══════════════════════════════════════════════════════════════════════════

const _instanceId = ref(0)
const alertCfg = useAlertConfig()

const connected  = ref(false)
const connecting = ref(false)
const lastPongAt = ref(Date.now())

const _subscriptions = reactive({})
const subscribedSymbols = computed(() => Object.keys(_subscriptions))

const ticks       = reactive({})
const currentBars = reactive({})
const _volHistory = {}
const volAvg      = reactive({})

const alerts      = reactive([])
const unreadCount = ref(0)

let ws             = null
let heartbeatTimer = null
let pongCheckTimer = null
let reconnectTimer = null
let retryDelay     = RECONNECT_BASE
let destroyed      = false
const _tickBuf     = {}

// ── WebSocket URL ──────────────────────────────────────────────────────────
function buildUrl() {
  return buildWsUrl(WS_PATH)
}

function _send(data) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(typeof data === 'string' ? data : JSON.stringify(data))
  }
}

// ══════════════════════════════════════════════════════════════════════════
// 告警系统
// ══════════════════════════════════════════════════════════════════════════

function _pushAlert({ symbol, type, message, level = 'warning' }) {
  if (!alertCfg.config.enabled) return
  if (!alertCfg.config.types[type]) return
  if (alertCfg.isCoolingDown(symbol, type)) return

  alertCfg.markTriggered(symbol, type)

  alerts.unshift({
    id:      Date.now() + Math.random(),
    symbol,
    type,
    message,
    level,
    time:    new Date().toLocaleTimeString('zh-CN', { hour12: false }),
  })

  if (alerts.length > MAX_ALERTS) alerts.splice(MAX_ALERTS)
  unreadCount.value++
}

function clearAlerts() {
  alerts.splice(0)
  unreadCount.value = 0
}

function markAlertsRead() {
  unreadCount.value = 0
}

// ── 成交量均量更新 ──────────────────────────────────────────────────────────
function _updateVolAvg(symbol, vol) {
  if (!_volHistory[symbol]) _volHistory[symbol] = []
  const hist = _volHistory[symbol]
  hist.push(vol)
  if (hist.length > MAX_TICK_HIST) hist.shift()
  volAvg[symbol] = hist.reduce((s, v) => s + v, 0) / hist.length
}

// ── 价格异动检测 ────────────────────────────────────────────────────────────
function _detectPrice(symbol, newTick, oldTick) {
  const { priceChangeThreshold, tickJumpThreshold } = alertCfg.config

  if (newTick.changeRate && Math.abs(newTick.changeRate) >= priceChangeThreshold) {
    const dir = newTick.changeRate > 0 ? '↑' : '↓'
    _pushAlert({
      symbol, type: 'price',
      message: `${symbol} 涨跌幅异动 ${dir}${Math.abs(newTick.changeRate).toFixed(2)}%，现价 ${newTick.last}`,
      level:   Math.abs(newTick.changeRate) >= priceChangeThreshold * 2 ? 'danger' : 'warning',
    })
  }

  if (oldTick && oldTick.last > 0 && tickJumpThreshold > 0) {
    const jumpRate = Math.abs((newTick.last - oldTick.last) / oldTick.last) * 100
    if (jumpRate >= tickJumpThreshold) {
      const diff = newTick.last - oldTick.last
      _pushAlert({
        symbol, type: 'price_jump',
        message: `${symbol} 单笔跳价 ${diff > 0 ? '+' : ''}${diff.toFixed(2)} (${diff > 0 ? '+' : ''}${jumpRate.toFixed(3)}%)`,
        level:   'warning',
      })
    }
  }
}

// ── 成交量异动检测 ──────────────────────────────────────────────────────────
function _detectVolume(symbol, tick) {
  const { volSpikeMultiplier } = alertCfg.config
  const avg = volAvg[symbol]
  if (!avg || avg <= 0 || !tick.volume) return
  const ratio = tick.volume / avg
  if (ratio >= volSpikeMultiplier) {
    _pushAlert({
      symbol, type: 'volume',
      message: `${symbol} 成交量异常：${tick.volume} 手（均量 ${Math.round(avg)} 的 ${ratio.toFixed(1)}x）`,
      level:   ratio >= volSpikeMultiplier * 2 ? 'danger' : 'warning',
    })
  }
}

// ══════════════════════════════════════════════════════════════════════════
// 消息处理
// ══════════════════════════════════════════════════════════════════════════

function _handleMessage(raw) {
  let msg
  try { msg = JSON.parse(raw) } catch { return }

  switch (msg.type) {

    case 'subscribed':
      break

    case 'tick': {
      const s = msg.symbol
      if (!s) break

      if (!ticks[s]) ticks[s] = emptyTick(s)

      const oldLast      = ticks[s].last
      const oldUpdatedAt = ticks[s].updatedAt ?? 0

      const prev = _tickBuf[s] ?? { ...ticks[s] }
      _tickBuf[s] = {
        ...prev,
        last:         msg.last          ?? prev.last,
        open:         msg.open          ?? prev.open,
        high:         msg.high          ?? prev.high,
        low:          msg.low           ?? prev.low,
        preClose:     msg.pre_close     ?? prev.preClose,
        volume:       msg.volume        ?? prev.volume,
        turnover:     msg.turnover      ?? prev.turnover,
        openInterest: msg.open_interest ?? prev.openInterest,
        bid1:    msg.bid1     ?? prev.bid1,
        bid2:    msg.bid2     ?? prev.bid2,
        bid3:    msg.bid3     ?? prev.bid3,
        bid4:    msg.bid4     ?? prev.bid4,
        bid5:    msg.bid5     ?? prev.bid5,
        ask1:    msg.ask1     ?? prev.ask1,
        ask2:    msg.ask2     ?? prev.ask2,
        ask3:    msg.ask3     ?? prev.ask3,
        ask4:    msg.ask4     ?? prev.ask4,
        ask5:    msg.ask5     ?? prev.ask5,
        bid1Vol: msg.bid1_vol ?? prev.bid1Vol,
        bid2Vol: msg.bid2_vol ?? prev.bid2Vol,
        bid3Vol: msg.bid3_vol ?? prev.bid3Vol,
        bid4Vol: msg.bid4_vol ?? prev.bid4Vol,
        bid5Vol: msg.bid5_vol ?? prev.bid5Vol,
        ask1Vol: msg.ask1_vol ?? prev.ask1Vol,
        ask2Vol: msg.ask2_vol ?? prev.ask2Vol,
        ask3Vol: msg.ask3_vol ?? prev.ask3Vol,
        ask4Vol: msg.ask4_vol ?? prev.ask4Vol,
        ask5Vol: msg.ask5_vol ?? prev.ask5Vol,
        change:     msg.change       ?? prev.change,
        changeRate: msg.change_rate  ?? prev.changeRate,
        time:       msg.time         ?? prev.time,
        updatedAt:  Date.now(),
      }

      const bufVol = _tickBuf[s].volume
      if (bufVol > 0) _updateVolAvg(s, bufVol)

      const now = _tickBuf[s].updatedAt
      if ((now - oldUpdatedAt) >= 500) {
        _detectPrice(s, _tickBuf[s], { last: oldLast, updatedAt: oldUpdatedAt })
        _detectVolume(s, _tickBuf[s])
      }
      break
    }

    case 'kline_update': {
      const s   = msg.symbol
      const iv  = msg.interval ?? '1m'
      const bar = msg.bar
      if (!s || !bar) break

      const key = `${s}_${iv}`
      if (!currentBars[key]) currentBars[key] = emptyBar(s, iv)

      const b = currentBars[key]
      b.time      = bar.time      ?? b.time
      b.open      = bar.open      ?? b.open
      b.high      = bar.high      ?? b.high
      b.low       = bar.low       ?? b.low
      b.close     = bar.close     ?? b.close
      b.volume    = bar.volume    ?? b.volume
      b.symbol    = s
      b.interval  = iv
      b.updatedAt = Date.now()
      break
    }

    case 'error':
      console.warn('[useWatchWs] 服务端错误:', msg.code, msg.msg ?? msg.message)
      break

    default:
      break
  }
}

// ══════════════════════════════════════════════════════════════════════════
// 订阅管理
// ══════════════════════════════════════════════════════════════════════════

function subscribe(symbols, channels = ['tick', 'kline_1m']) {
  const syms = (Array.isArray(symbols) ? symbols : [symbols]).filter(Boolean)
  if (!syms.length) return

  for (const s of syms) {
    if (!_subscriptions[s]) _subscriptions[s] = new Set()
    for (const ch of channels) _subscriptions[s].add(ch)
  }

  _send({ type: 'subscribe', symbols: syms, channels })
}

function unsubscribe(symbols, channels = null) {
  const syms = (Array.isArray(symbols) ? symbols : [symbols]).filter(Boolean)
  if (!syms.length) return

  for (const s of syms) {
    if (channels) {
      for (const ch of channels) _subscriptions[s]?.delete(ch)
      if (_subscriptions[s]?.size === 0) delete _subscriptions[s]
    } else {
      delete _subscriptions[s]
    }
  }

  _send({ type: 'unsubscribe', symbols: syms, ...(channels ? { channels } : {}) })
}

function _resubscribeAll() {
  for (const [symbol, chSet] of Object.entries(_subscriptions)) {
    if (chSet.size > 0) {
      _send({ type: 'subscribe', symbols: [symbol], channels: [...chSet] })
    }
  }
}

// ── 数据访问 ────────────────────────────────────────────────────────────────
function getTick(symbol) {
  return ticks[symbol] ?? (ticks[symbol] = reactive(emptyTick(symbol)))
}

function getCurrentBar(symbol, interval = '1m') {
  return currentBars[`${symbol}_${interval}`] ?? emptyBar(symbol, interval)
}

// ══════════════════════════════════════════════════════════════════════════
// 心跳
// ══════════════════════════════════════════════════════════════════════════

function _startHeartbeat() {
  _stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws?.readyState !== WebSocket.OPEN) return
    _send('ping')
    pongCheckTimer = setTimeout(() => {
      const elapsed = Date.now() - lastPongAt.value
      if (elapsed > HEARTBEAT_MS + PONG_TIMEOUT) {
        console.warn('[useWatchWs] pong 超时，主动重连')
        ws?.close()
      }
    }, PONG_TIMEOUT)
  }, HEARTBEAT_MS)
}

function _stopHeartbeat() {
  clearInterval(heartbeatTimer)
  clearTimeout(pongCheckTimer)
  heartbeatTimer = null
  pongCheckTimer = null
}

// ══════════════════════════════════════════════════════════════════════════
// 连接管理
// ══════════════════════════════════════════════════════════════════════════

function connect() {
  if (destroyed || ws?.readyState === WebSocket.CONNECTING) return

  connecting.value = true

  try {
    ws = new WebSocket(buildUrl())
  } catch (e) {
    connecting.value = false
    console.error('[useWatchWs] 创建 WebSocket 失败:', e.message)
    _scheduleReconnect()
    return
  }

  ws.onopen = () => {
    connected.value  = true
    connecting.value = false
    retryDelay       = RECONNECT_BASE
    lastPongAt.value = Date.now()
    _startHeartbeat()
    _resubscribeAll()
    _instanceId.value++
  }

  ws.onmessage = (e) => {
    if (e.data === 'pong') {
      lastPongAt.value = Date.now()
      return
    }
    _handleMessage(e.data)
  }

  ws.onclose = (e) => {
    connected.value  = false
    connecting.value = false
    _stopHeartbeat()
    if (!destroyed) {
      console.warn(`[useWatchWs] 连接断开 (code=${e.code})，${retryDelay / 1000}s 后重连`)
      _scheduleReconnect()
    }
  }

  ws.onerror = () => {
    connecting.value = false
  }
}

function _scheduleReconnect() {
  if (destroyed) return
  clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX)
    connect()
  }, retryDelay)
}

function disconnect() {
  destroyed = true
  _stopHeartbeat()
  clearTimeout(reconnectTimer)
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
  connected.value  = false
  connecting.value = false
}

function reconnect() {
  destroyed  = false
  retryDelay = RECONNECT_BASE
  clearTimeout(reconnectTimer)
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
  connected.value  = false
  connecting.value = false
  connect()
}

// ══════════════════════════════════════════════════════════════════════════
// 可见性感知
// ══════════════════════════════════════════════════════════════════════════

function _onVisibilityChange() {
  if (document.hidden) {
    _stopHeartbeat()
  } else {
    if (!connected.value && !destroyed) {
      retryDelay = RECONNECT_BASE
      connect()
    } else if (connected.value) {
      _startHeartbeat()
    }
  }
}

document.addEventListener('visibilitychange', _onVisibilityChange)

// ── Tick 批量写入 ──────────────────────────────────────────────────────────
const _flushTimer = setInterval(() => {
  for (const [symbol, data] of Object.entries(_tickBuf)) {
    if (!ticks[symbol]) ticks[symbol] = reactive(emptyTick(symbol))
    Object.assign(ticks[symbol], data)
    delete _tickBuf[symbol]
  }
}, TICK_FLUSH_MS)

// ══════════════════════════════════════════════════════════════════════════
// 初始化 & 清理
// ══════════════════════════════════════════════════════════════════════════

connect()

onUnmounted(() => {
  clearInterval(_flushTimer)
  disconnect()
  document.removeEventListener('visibilitychange', _onVisibilityChange)
})

// ══════════════════════════════════════════════════════════════════════════
// 导出（单例 API，所有组件共享）
// ══════════════════════════════════════════════════════════════════════════

export function useWatchWs() {
  return {
    connected,
    connecting,
    lastPongAt,
    subscribedSymbols,
    subscribe,
    unsubscribe,
    ticks,
    currentBars,
    volAvg,
    getTick,
    getCurrentBar,
    alerts,
    unreadCount,
    clearAlerts,
    markAlertsRead,
    reconnect,
    disconnect,
  }
}
