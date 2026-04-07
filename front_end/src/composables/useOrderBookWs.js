/**
 * useOrderBookWs.js — 订单簿 & 持仓簿实时数据
 *
 * 数据来源：
 *   - REST 初始加载：GET /orders, GET /trades, GET /positions
 *   - WS /ws/orders  → type:"order_update" 更新委托单状态
 *                    → type:"trade_event"  追加成交记录
 *   - WS /ws/positions → type:"positions_update" 替换持仓数组
 */
import { reactive, ref, computed, onUnmounted } from 'vue'
import { fetchOrders, fetchTrades, fetchPositions } from '@/api/index.js'

const MAX_TRADES  = 500
const MAX_BACKOFF = 30_000
const PING_MS     = 20_000

function makeWs(urlPath, onMsg, onOpen) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url   = `${proto}//${location.host}${urlPath}`

  let ws         = null
  let retryDelay = 1_000
  let retryTimer = null
  let pingTimer  = null
  let destroyed  = false
  const alive    = ref(false)

  function connect() {
    if (destroyed) return
    ws = new WebSocket(url)

    ws.onopen = () => {
      alive.value = true
      retryDelay  = 1_000
      pingTimer   = setInterval(() => ws?.readyState === 1 && ws.send('ping'), PING_MS)
      onOpen?.()
    }
    ws.onmessage = (e) => {
      try { onMsg(JSON.parse(e.data)) } catch { /* ignore */ }
    }
    ws.onclose = () => {
      alive.value = false
      clearInterval(pingTimer)
      if (!destroyed) retryTimer = setTimeout(() => {
        retryDelay = Math.min(retryDelay * 2, MAX_BACKOFF)
        connect()
      }, retryDelay)
    }
    ws.onerror = () => ws?.close()
  }

  function dispose() {
    destroyed = true
    clearInterval(pingTimer)
    clearTimeout(retryTimer)
    ws?.close()
  }

  connect()
  return { alive, dispose }
}

export function useOrderBookWs() {
  // ── 状态 ──────────────────────────────────────────────────────────────────
  const ordersMap  = reactive(new Map())   // order_id → order object
  const trades     = reactive([])          // 成交记录数组（最新在前）
  const positions  = reactive([])          // 持仓快照数组

  const ordersWsAlive    = ref(false)
  const positionsWsAlive = ref(false)
  const loading          = ref(false)
  const lastOrderTime    = ref('')
  const lastPositionTime = ref('')

  // 委托单：Map → 排序数组（最新时间在前）
  const ordersArray = computed(() =>
    [...ordersMap.values()].sort((a, b) =>
      (b.create_ts ?? b.timestamp ?? '').localeCompare(a.create_ts ?? a.timestamp ?? '')
    )
  )

  // ── 初始数据加载 ───────────────────────────────────────────────────────────
  async function loadAll() {
    loading.value = true
    try {
      const [ords, trds, pos] = await Promise.all([
        fetchOrders().catch(() => []),
        fetchTrades().catch(() => []),
        fetchPositions().catch(() => []),
      ])
      // 委托单写入 Map
      ordersMap.clear()
      for (const o of ords) ordersMap.set(o.order_id, o)
      // 成交记录（从 strategy.trades 返回的格式可能不同，统一化）
      trades.splice(0, trades.length, ...trds.slice(0, MAX_TRADES))
      // 持仓
      positions.splice(0, positions.length, ...pos)
    } catch { /* 静默 */ } finally {
      loading.value = false
    }
  }

  // ── 处理 /ws/orders 消息 ──────────────────────────────────────────────────
  function handleOrderMsg(msg) {
    if (!msg?.type) return

    if (msg.type === 'order_update') {
      ordersMap.set(msg.order_id, msg)
      lastOrderTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    }

    if (msg.type === 'trade_event') {
      // 去重（同一 trade_id 不重复插入）
      const exists = trades.some(t => t.trade_id === msg.trade_id)
      if (!exists) {
        trades.unshift(msg)
        if (trades.length > MAX_TRADES) trades.splice(MAX_TRADES)
      }
      lastOrderTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    }
  }

  // ── 处理 /ws/positions 消息 ───────────────────────────────────────────────
  function handlePosMsg(msg) {
    if (msg?.type !== 'positions_update') return
    positions.splice(0, positions.length, ...(msg.positions ?? []))
    lastPositionTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }

  // ── 建立 WebSocket 连接 ────────────────────────────────────────────────────
  const { alive: owAlive, dispose: disposeOrders } = makeWs(
    '/ws/orders',
    handleOrderMsg,
    () => { ordersWsAlive.value = true },
  )
  const { alive: pwAlive, dispose: disposePos } = makeWs(
    '/ws/positions',
    handlePosMsg,
    () => { positionsWsAlive.value = true },
  )

  // 同步 alive 状态
  const _w1 = owAlive
  const _w2 = pwAlive
  // computed alive from individual ws
  const wsConnected = computed(() => _w1.value || _w2.value)

  loadAll()
  onUnmounted(() => { disposeOrders(); disposePos() })

  return {
    ordersArray,
    trades,
    positions,
    loading,
    ordersWsAlive:    owAlive,
    positionsWsAlive: pwAlive,
    wsConnected,
    lastOrderTime,
    lastPositionTime,
    reload: loadAll,
  }
}
