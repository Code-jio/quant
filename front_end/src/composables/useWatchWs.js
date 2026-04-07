/**
 * useWatchWs — 盯盘系统 WebSocket 实时数据服务
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

// ── 常量 ──────────────────────────────────────────────────────────────────
const WS_PATH        = '/ws/watch'
const HEARTBEAT_MS   = 20_000   // 心跳间隔
const PONG_TIMEOUT   = 10_000   // pong 等待超时
const RECONNECT_BASE = 1_000    // 初始重连延迟
const RECONNECT_MAX  = 30_000   // 最大重连延迟
const MAX_ALERTS     = 100      // 最多保留告警条数
const MAX_TICK_HIST  = 50       // 每品种保留 tick 历史条数（用于均量计算）

// ── 工厂：空 Tick 对象 ─────────────────────────────────────────────────────
function emptyTick(symbol = '') {
  return {
    symbol,
    last:         0,
    open:         0,
    high:         0,
    low:          0,
    preClose:     0,
    volume:       0,    // 本笔成交量
    turnover:     0,    // 成交额
    openInterest: 0,    // 持仓量
    // 5 档买卖盘
    bid1: 0, bid2: 0, bid3: 0, bid4: 0, bid5: 0,
    ask1: 0, ask2: 0, ask3: 0, ask4: 0, ask5: 0,
    bid1Vol: 0, bid2Vol: 0, bid3Vol: 0, bid4Vol: 0, bid5Vol: 0,
    ask1Vol: 0, ask2Vol: 0, ask3Vol: 0, ask4Vol: 0, ask5Vol: 0,
    change:       0,    // 涨跌额
    changeRate:   0,    // 涨跌幅（百分比值，如 1.23 表示 +1.23%）
    time:         '',
    updatedAt:    0,
  }
}

// ── 工厂：空 Bar 对象 ──────────────────────────────────────────────────────
function emptyBar(symbol = '', interval = '1m') {
  return { symbol, interval, time: '', open: 0, high: 0, low: 0, close: 0, volume: 0, updatedAt: 0 }
}

// ── Tick 节流缓冲（模块级，跨组件共享） ─────────────────────────────────────
/**
 * 高频 tick 消息先积累在此普通对象，每 TICK_FLUSH_MS 统一写入
 * Vue 响应式 ticks，避免每条消息都触发组件重渲染。
 * key = symbol，value = 原始字段合并（最新覆盖旧值）
 */
const _tickBuf      = {}
const TICK_FLUSH_MS = 80   // 约 12 fps，行情正常显示无需更快

// ── Composable ─────────────────────────────────────────────────────────────
export function useWatchWs() {
  const alertCfg = useAlertConfig()

  // ── 连接状态 ──────────────────────────────────────────────────────────
  const connected  = ref(false)
  const connecting = ref(false)
  const lastPongAt = ref(Date.now())

  // ── 订阅状态 ──────────────────────────────────────────────────────────
  /**
   * 各品种当前订阅的频道集合：{ [symbol]: Set<channel> }
   * channel 示例: 'tick' | 'kline_1m' | 'kline_5m' | 'kline_1h' | 'kline_1d'
   */
  const _subscriptions = reactive({})

  /** 计算属性：所有已订阅品种列表 */
  const subscribedSymbols = computed(() => Object.keys(_subscriptions))

  // ── 实时数据 ──────────────────────────────────────────────────────────
  /** 每品种最新 tick：{ [symbol]: TickData } */
  const ticks = reactive({})

  /** 每品种当前形成中的 bar：{ [symbol_interval]: BarData } */
  const currentBars = reactive({})

  /** 各品种 tick 成交量历史（用于均量计算）：{ [symbol]: number[] } */
  const _volHistory = {}

  /** 各品种成交量移动平均：{ [symbol]: number } */
  const volAvg = reactive({})

  // ── 告警 ─────────────────────────────────────────────────────────────
  /**
   * 告警列表，最新在前
   * 每项 { id, symbol, type, message, level, time }
   */
  const alerts      = reactive([])
  const unreadCount = ref(0)

  // ── 内部变量 ──────────────────────────────────────────────────────────
  let ws             = null
  let heartbeatTimer = null
  let pongCheckTimer = null
  let reconnectTimer = null
  let retryDelay     = RECONNECT_BASE
  let destroyed      = false

  // ── WebSocket URL ──────────────────────────────────────────────────────
  function buildUrl() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${location.host}${WS_PATH}`
  }

  // ── 发送消息 ───────────────────────────────────────────────────────────
  function _send(data) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(typeof data === 'string' ? data : JSON.stringify(data))
    }
  }

  // ══════════════════════════════════════════════════════════════════════
  // 告警系统
  // ══════════════════════════════════════════════════════════════════════

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

  // ── 成交量均量更新（指数移动平均） ─────────────────────────────────────
  function _updateVolAvg(symbol, vol) {
    if (!_volHistory[symbol]) _volHistory[symbol] = []
    const hist = _volHistory[symbol]
    hist.push(vol)
    if (hist.length > MAX_TICK_HIST) hist.shift()
    // 简单算术均值（前 N 笔）
    const avg = hist.reduce((s, v) => s + v, 0) / hist.length
    volAvg[symbol] = avg
  }

  // ── 价格异动检测 ─────────────────────────────────────────────────────
  function _detectPrice(symbol, newTick, oldTick) {
    const { priceChangeThreshold, tickJumpThreshold } = alertCfg.config

    // 涨跌幅告警（与昨收相比）
    if (newTick.changeRate && Math.abs(newTick.changeRate) >= priceChangeThreshold) {
      const dir = newTick.changeRate > 0 ? '↑' : '↓'
      _pushAlert({
        symbol, type: 'price',
        message: `${symbol} 涨跌幅异动 ${dir}${Math.abs(newTick.changeRate).toFixed(2)}%，现价 ${newTick.last}`,
        level:   Math.abs(newTick.changeRate) >= priceChangeThreshold * 2 ? 'danger' : 'warning',
      })
    }

    // 单笔跳价告警（与上一 tick 相比）
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

  // ── 成交量异动检测 ────────────────────────────────────────────────────
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

  // ══════════════════════════════════════════════════════════════════════
  // 消息处理
  // ══════════════════════════════════════════════════════════════════════

  function _handleMessage(raw) {
    let msg
    try { msg = JSON.parse(raw) } catch { return }

    switch (msg.type) {

      // ── 服务端确认订阅 ──────────────────────────────────────────────
      case 'subscribed':
        break   // 可在此记录日志

      // ── Tick 数据 ────────────────────────────────────────────────────
      case 'tick': {
        const s = msg.symbol
        if (!s) break

        // ① 先确保响应式槽位存在（只写一次）
        if (!ticks[s]) ticks[s] = emptyTick(s)

        // ② 取当前已渲染的值，用于异动检测基准
        const oldLast       = ticks[s].last
        const oldUpdatedAt  = ticks[s].updatedAt ?? 0

        // ③ 立即写入缓冲（普通对象），供异动检测使用
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
          change:      msg.change       ?? prev.change,
          changeRate:  msg.change_rate  ?? prev.changeRate,
          time:        msg.time         ?? prev.time,
          updatedAt:   Date.now(),
        }

        // ④ 均量计算（使用缓冲值，不依赖响应式）
        const bufVol = _tickBuf[s].volume
        if (bufVol > 0) _updateVolAvg(s, bufVol)

        // ⑤ 异动检测（间隔 500ms 以上才检测，避免频繁触发）
        const now = _tickBuf[s].updatedAt
        if ((now - oldUpdatedAt) >= 500) {
          const oldSnap = { last: oldLast, updatedAt: oldUpdatedAt }
          _detectPrice(s, _tickBuf[s], oldSnap)
          _detectVolume(s, _tickBuf[s])
        }
        break
      }

      // ── K 线实时更新 ─────────────────────────────────────────────────
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

  // ══════════════════════════════════════════════════════════════════════
  // 订阅管理
  // ══════════════════════════════════════════════════════════════════════

  /**
   * 订阅品种的实时数据
   * @param {string|string[]} symbols  品种代码，可传数组
   * @param {string[]}        channels 频道列表，如 ['tick', 'kline_1m', 'kline_5m']
   */
  function subscribe(symbols, channels = ['tick', 'kline_1m']) {
    const syms = (Array.isArray(symbols) ? symbols : [symbols]).filter(Boolean)
    if (!syms.length) return

    for (const s of syms) {
      if (!_subscriptions[s]) _subscriptions[s] = new Set()
      for (const ch of channels) _subscriptions[s].add(ch)
    }

    _send({ type: 'subscribe', symbols: syms, channels })
  }

  /**
   * 取消订阅品种
   * @param {string|string[]} symbols  品种代码
   * @param {string[]}        [channels] 不传则取消全部频道
   */
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

  /**
   * 重连后重新发送所有订阅（由 WS onopen 自动调用）
   */
  function _resubscribeAll() {
    for (const [symbol, chSet] of Object.entries(_subscriptions)) {
      if (chSet.size > 0) {
        _send({ type: 'subscribe', symbols: [symbol], channels: [...chSet] })
      }
    }
  }

  // ── 数据访问便捷方法 ───────────────────────────────────────────────────
  function getTick(symbol) {
    return ticks[symbol] ?? emptyTick(symbol)
  }

  function getCurrentBar(symbol, interval = '1m') {
    return currentBars[`${symbol}_${interval}`] ?? emptyBar(symbol, interval)
  }

  // ══════════════════════════════════════════════════════════════════════
  // 心跳
  // ══════════════════════════════════════════════════════════════════════

  function _startHeartbeat() {
    _stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState !== WebSocket.OPEN) return
      _send('ping')
      // 超时检测：如果 pong_timeout 毫秒内没收到 pong，主动重连
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

  // ══════════════════════════════════════════════════════════════════════
  // 连接管理
  // ══════════════════════════════════════════════════════════════════════

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
    }

    ws.onmessage = (e) => {
      // 纯文本 pong 帧
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
      // onclose 会随后触发，重连逻辑在那里
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

  /** 手动断开（不再重连） */
  function disconnect() {
    destroyed = true
    _stopHeartbeat()
    clearTimeout(reconnectTimer)
    if (ws) {
      ws.onclose = null  // 避免触发自动重连
      ws.close()
      ws = null
    }
    connected.value  = false
    connecting.value = false
  }

  /** 手动触发重连（重置退避延迟） */
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

  // ══════════════════════════════════════════════════════════════════════
  // 页面可见性感知
  // ══════════════════════════════════════════════════════════════════════

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

  // ── Tick 节流 flush 定时器 ─────────────────────────────────────────────
  /**
   * 每 TICK_FLUSH_MS 将缓冲池中积累的 tick 批量写入 Vue 响应式对象，
   * 避免高频行情（数十条/秒）每条都触发组件重渲染。
   */
  const _flushTimer = setInterval(() => {
    for (const [symbol, data] of Object.entries(_tickBuf)) {
      Object.assign(ticks[symbol], data)
      delete _tickBuf[symbol]
    }
  }, TICK_FLUSH_MS)

  // ── 初始化 & 清理 ──────────────────────────────────────────────────────
  connect()

  onUnmounted(() => {
    clearInterval(_flushTimer)
    disconnect()
    document.removeEventListener('visibilitychange', _onVisibilityChange)
  })

  // ── 暴露 API ───────────────────────────────────────────────────────────
  return {
    // 连接状态
    connected,
    connecting,
    lastPongAt,

    // 订阅管理
    subscribedSymbols,
    subscribe,
    unsubscribe,

    // 实时数据
    ticks,
    currentBars,
    volAvg,
    getTick,
    getCurrentBar,

    // 告警
    alerts,
    unreadCount,
    clearAlerts,
    markAlertsRead,

    // 控制
    reconnect,
    disconnect,
  }
}
