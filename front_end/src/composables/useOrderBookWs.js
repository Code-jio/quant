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
import { fetchTradingSnapshot } from '@/api/index.js'
import { buildWsUrl } from '@/config/network.js'

const MAX_TRADES  = 500
const MAX_BACKOFF = 30_000
const PING_MS     = 20_000

function makeWs(urlPath, onMsg, onOpen, onClose) {
  const url   = buildWsUrl(urlPath)

  let ws         = null
  let retryDelay = 1_000
  let retryTimer = null
  let pingTimer  = null
  let destroyed  = false
  const alive    = ref(false)

  function connect() {
    if (destroyed || sessionStorage.getItem('quant_session_active')!=='1') return
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
    ws.onclose = (event) => {
      onClose?.()
      alive.value = false
      clearInterval(pingTimer)
      if (!destroyed && event.code!==1008 && sessionStorage.getItem('quant_session_active')==='1') retryTimer = setTimeout(() => {
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
  const stale=ref(true)
  const error=ref('')
  let revision=0
  let positionRevision=0
  const orderRevisions=new Map()
  let requestVersion=0
  let disposed=false
  let buffered=[]
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
    const version=++requestVersion
    loading.value=true
    try {
      const snapshot=await fetchTradingSnapshot()
      if (disposed || version!==requestVersion) return
      revision=snapshot.revision
      positionRevision=revision
      orderRevisions.clear()
      ordersMap.clear()
      for (const order of snapshot.orders) ordersMap.set(order.order_id,order)
      trades.splice(0,trades.length,...snapshot.trades.slice(0,MAX_TRADES))
      positions.splice(0,positions.length,...snapshot.positions)
      loading.value=false
      const pending=buffered; buffered=[]
      for (const message of pending) {
        if (message.type==='positions_update') handlePosMsg(message)
        else handleOrderMsg(message)
      }
      stale.value=false; error.value=''
    } catch (e) {
      if (version===requestVersion) { stale.value=true; error.value=e.message ?? '账户数据刷新失败' }
    } finally {
      if (version===requestVersion) loading.value=false
    }
  }
  function accept(message) {
    if (disposed) return false
    if (loading.value) { buffered.push(message); if (buffered.length>2000) buffered.shift(); return false }
    if ((message.revision??0)<=revision) return false
    return true
  }

  // ── 处理 /ws/orders 消息 ──────────────────────────────────────────────────
  function handleOrderMsg(msg) {
    if (!msg?.type || !accept(msg)) return

    if (msg.type === 'order_update') {
      if (msg.revision <= (orderRevisions.get(msg.order_id) ?? revision)) return
      orderRevisions.set(msg.order_id,msg.revision)
      const previous=ordersMap.get(msg.order_id)
      const terminal=['filled','cancelled','rejected']
      if (previous && ((previous.traded_volume??0)>(msg.traded_volume??0) ||
          terminal.includes(previous.status) && !terminal.includes(msg.status))) return
      ordersMap.set(msg.order_id, msg)
      if (ordersMap.size>2000) {
        const oldest=[...ordersMap].find(([,order])=>terminal.includes(order.status))
        if (oldest) { ordersMap.delete(oldest[0]); orderRevisions.delete(oldest[0]) }
      }
      lastOrderTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    }

    if (msg.type === 'trade_event') {
      // 去重（同一 trade_id 不重复插入）
      const exists = trades.some(t => t.trade_id === msg.trade_id &&
        t.account_id === msg.account_id && t.trading_day === msg.trading_day && t.exchange === msg.exchange)
      if (!exists) {
        trades.unshift(msg)
        if (trades.length > MAX_TRADES) trades.splice(MAX_TRADES)
      }
      lastOrderTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    }
  }

  // ── 处理 /ws/positions 消息 ───────────────────────────────────────────────
  function handlePosMsg(msg) {
    if (msg?.type !== 'positions_update' || !accept(msg)) return
    if (msg.revision <= positionRevision) return
    positionRevision=msg.revision
    positions.splice(0, positions.length, ...(msg.positions ?? []))
    lastPositionTime.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }

  // ── 建立 WebSocket 连接 ────────────────────────────────────────────────────
  const { alive: owAlive, dispose: disposeOrders } = makeWs(
    '/ws/orders',
    handleOrderMsg,
    () => { ordersWsAlive.value = true; loadAll() },
    () => { stale.value=true },
  )
  const { alive: pwAlive, dispose: disposePos } = makeWs(
    '/ws/positions',
    handlePosMsg,
    () => { positionsWsAlive.value = true; loadAll() },
    () => { stale.value=true },
  )

  // 同步 alive 状态
  const _w1 = owAlive
  const _w2 = pwAlive
  // computed alive from individual ws
  const wsConnected = computed(() => _w1.value || _w2.value)

  loadAll()
  function dispose() {
    disposed=true; requestVersion++; disposeOrders(); disposePos()
    ordersMap.clear();orderRevisions.clear();trades.splice(0);positions.splice(0);buffered=[]
  }
  window.addEventListener('quant-auth-change',dispose)
  onUnmounted(() => { dispose(); window.removeEventListener('quant-auth-change',dispose) })

  return {
    ordersArray,
    trades,
    positions,
    loading,stale,error,
    ordersWsAlive:    owAlive,
    positionsWsAlive: pwAlive,
    wsConnected,
    lastOrderTime,
    lastPositionTime,
    reload: loadAll,
  }
}
