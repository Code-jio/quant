/**
 * useSystemWs — 系统状态 WebSocket 可组合函数
 *
 * 功能：
 *  - 自动连接 ws://.../ws/system
 *  - 断线后指数退避自动重连（3s → 最大 30s）
 *  - 每 30s 发送一次 ping 保持连接
 *  - 组件卸载时自动清理
 *
 * 返回：
 *  - connected: Ref<boolean>  — 当前连接状态
 *  - data: Reactive<SystemData> — 最新系统数据快照
 */

import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { buildWsUrl } from '@/config/network.js'

const RECONNECT_BASE  = 3_000   // 初始重连延迟 3s
const RECONNECT_MAX   = 30_000  // 最大重连延迟 30s
const HEARTBEAT_EVERY = 30_000  // 心跳间隔 30s

export function useSystemWs(url = buildWsUrl('/ws/system')) {
  const connected = ref(false)

  const data = reactive({
    totalPnl:          0,
    returnRate:        0,     // 已是百分比值，如 0.85 表示 0.85%
    balance:           0,
    cpuPercent:        0,
    memoryPercent:     0,
    activeStrategies:  0,
    marketConnected:   false,
    tdConnected:       false,
    mdConnected:       false,
    gatewayStatus:     'stopped',
    gatewayName:       'N/A',
    gatewayLatencyMs:  -1,    // -1 = 尚未收到回调
    networkSendBps:    0,     // 字节/秒
    networkRecvBps:    0,
    timestamp:         null,
  })

  let ws              = null
  let reconnectDelay  = RECONNECT_BASE
  let reconnectTimer  = null
  let heartbeatTimer  = null
  let destroyed       = false   // 组件已卸载标志

  // ── 消息处理 ──────────────────────────────────────────────────────────────
  function onMessage(event) {
    try {
      const msg = JSON.parse(event.data)
      if (msg.type !== 'system_status') return

      data.totalPnl          = msg.total_pnl          ?? data.totalPnl
      data.returnRate        = msg.return_rate        ?? data.returnRate
      data.balance           = msg.balance            ?? data.balance
      data.cpuPercent        = msg.cpu_percent        ?? data.cpuPercent
      data.memoryPercent     = msg.memory_percent     ?? data.memoryPercent
      data.activeStrategies  = msg.active_strategies  ?? data.activeStrategies
      data.marketConnected   = msg.market_connected   ?? data.marketConnected
      data.tdConnected       = msg.td_connected       ?? data.tdConnected
      data.mdConnected       = msg.md_connected       ?? data.mdConnected
      data.gatewayStatus     = msg.gateway_status     ?? data.gatewayStatus
      data.gatewayName       = msg.gateway_name       ?? data.gatewayName
      data.gatewayLatencyMs  = msg.gateway_latency_ms ?? data.gatewayLatencyMs
      data.networkSendBps    = msg.network_send_bps   ?? data.networkSendBps
      data.networkRecvBps    = msg.network_recv_bps   ?? data.networkRecvBps
      data.timestamp         = msg.timestamp          ?? data.timestamp

      // 成功收到消息，重置重连延迟
      reconnectDelay = RECONNECT_BASE
    } catch (e) {
      console.warn('[useSystemWs] 消息解析失败:', e)
    }
  }

  // ── 心跳 ──────────────────────────────────────────────────────────────────
  function startHeartbeat() {
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) ws.send('ping')
    }, HEARTBEAT_EVERY)
  }

  function stopHeartbeat() {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }

  // ── 连接管理 ──────────────────────────────────────────────────────────────
  function connect() {
    if (destroyed) return

    try {
      ws = new WebSocket(url)
    } catch (e) {
      console.error('[useSystemWs] WebSocket 创建失败:', e)
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      startHeartbeat()
      console.info(`[useSystemWs] 已连接: ${url}`)
    }

    ws.onmessage = onMessage

    ws.onclose = (e) => {
      connected.value = false
      stopHeartbeat()
      if (!destroyed) {
        console.warn(`[useSystemWs] 连接断开 (code=${e.code})，${reconnectDelay / 1000}s 后重连`)
        scheduleReconnect()
      }
    }

    ws.onerror = () => {
      connected.value = false
      // onclose 会随后触发，在那里处理重连
    }
  }

  function scheduleReconnect() {
    if (destroyed) return
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 1.5, RECONNECT_MAX)
      connect()
    }, reconnectDelay)
  }

  function disconnect() {
    destroyed = true
    clearTimeout(reconnectTimer)
    stopHeartbeat()
    if (ws) {
      ws.onclose = null  // 防止触发重连
      ws.close()
      ws = null
    }
  }

  onMounted(connect)
  onUnmounted(disconnect)

  return { connected, data }
}
