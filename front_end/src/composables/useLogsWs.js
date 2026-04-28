/**
 * useLogsWs — 系统日志 WebSocket 可组合函数
 *
 * - 连接 ws://.../ws/logs
 * - 连接时拉取最近 200 条历史日志（type: log_history）
 * - 实时追加新日志条目（type: log_entry）
 * - 支持暂停/恢复自动追加、手动清空
 * - 断线自动重连（指数退避）
 *
 * 返回：
 *  - connected : Ref<boolean>
 *  - logs      : Ref<LogEntry[]>  — 最多保留 MAX_LOGS 条
 *  - paused    : Ref<boolean>     — 暂停时不追加新条目
 *  - pause()   : () => void
 *  - resume()  : () => void
 *  - clear()   : () => void
 */

import { ref, onMounted, onUnmounted } from 'vue'
import { buildWsUrl } from '@/config/network.js'

const MAX_LOGS        = 500
const RECONNECT_BASE  = 3_000
const RECONNECT_MAX   = 30_000
const HEARTBEAT_EVERY = 30_000

export function useLogsWs(url = buildWsUrl('/ws/logs')) {
  const connected = ref(false)
  const logs      = ref([])    // LogEntry[]
  const paused    = ref(false)

  let ws             = null
  let reconnectDelay = RECONNECT_BASE
  let reconnectTimer = null
  let heartbeatTimer = null
  let destroyed      = false

  // ── 消息处理 ────────────────────────────────────────────────────────────
  function onMessage(event) {
    try {
      const msg = JSON.parse(event.data)

      if (msg.type === 'log_history') {
        // 历史批量日志：直接替换（倒序 → 最旧在前）
        logs.value = Array.isArray(msg.logs) ? [...msg.logs] : []
        return
      }

      if (msg.type === 'log_entry') {
        if (paused.value) return
        const entry = {
          ts:      msg.ts,
          level:   msg.level,
          name:    msg.name,
          message: msg.message,
        }
        logs.value.push(entry)
        // 超出上限则删头
        if (logs.value.length > MAX_LOGS) {
          logs.value.splice(0, logs.value.length - MAX_LOGS)
        }
        reconnectDelay = RECONNECT_BASE
      }
    } catch (e) {
      console.warn('[useLogsWs] 消息解析失败:', e)
    }
  }

  // ── 心跳 ────────────────────────────────────────────────────────────────
  function startHeartbeat() {
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) ws.send('ping')
    }, HEARTBEAT_EVERY)
  }

  function stopHeartbeat() {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }

  // ── 连接管理 ─────────────────────────────────────────────────────────────
  function connect() {
    if (destroyed) return
    try {
      ws = new WebSocket(url)
    } catch (e) {
      console.error('[useLogsWs] WebSocket 创建失败:', e)
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      startHeartbeat()
    }

    ws.onmessage = onMessage

    ws.onclose = (e) => {
      connected.value = false
      stopHeartbeat()
      if (!destroyed) {
        scheduleReconnect()
      }
    }

    ws.onerror = () => {
      connected.value = false
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
      ws.onclose = null
      ws.close()
      ws = null
    }
  }

  // ── 公开 API ─────────────────────────────────────────────────────────────
  function pause()  { paused.value = true  }
  function resume() { paused.value = false }
  function clear()  { logs.value = []      }

  onMounted(connect)
  onUnmounted(disconnect)

  return { connected, logs, paused, pause, resume, clear }
}
