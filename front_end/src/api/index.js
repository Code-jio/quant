/**
 * API 客户端
 *
 * - 开发环境：请求通过 Vite proxy 转发（/api → http://localhost:8000）
 * - 生产环境：设置 VITE_API_BASE_URL=http://<backend>:<port>
 * - 所有需要鉴权的请求自动附加 Authorization: Bearer <token>
 * - 收到 401 时自动清除本地 token 并跳转登录页
 */

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

function getToken() {
  return localStorage.getItem('quant_token') ?? ''
}

async function request(path, options = {}) {
  const token   = getToken()
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  }

  let res
  try {
    res = await fetch(`${BASE}${path}`, { ...options, headers })
  } catch (e) {
    throw new Error(`网络请求失败: ${e.message}`)
  }

  // 401：清除凭证并跳转登录页
  if (res.status === 401) {
    localStorage.removeItem('quant_token')
    localStorage.removeItem('quant_account_id')
    window.location.href = '/login'
    throw new Error('401')
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json()).detail ?? detail } catch { /* ignore */ }
    throw new Error(detail)
  }

  return res.json()
}

// ── Auth ──────────────────────────────────────────────────────────────────
/** 获取预设服务器列表 */
export const fetchServers = () => request('/auth/servers')

/** 获取当前连接状态（无需鉴权） */
export const fetchAuthStatus = () => request('/auth/status')

/** CTP 登录 */
export const login = (body) =>
  request('/auth/login', { method: 'POST', body: JSON.stringify(body) })

/** 断开连接 */
export const logout = () =>
  request('/auth/logout', { method: 'POST' })

// ── 行情 ──────────────────────────────────────────────────────────────────
/** 批量查询实时 tick（轮询接口） */
export const fetchTicks = (symbols) =>
  request(`/watch/tick?symbols=${encodeURIComponent(symbols.join(','))}`)

// ── 系统 ──────────────────────────────────────────────────────────────────
export const fetchSystemStatus = () => request('/system/status')

// ── 策略 ──────────────────────────────────────────────────────────────────
/** 策略列表（简要） */
export const fetchStrategies = () => request('/strategies')

/** 策略详情（含信号列表、参数、权重） */
export const fetchStrategyDetail = (strategyId) =>
  request(`/strategies/${strategyId}`)

/** 启停策略 */
export const sendStrategyAction = (strategyId, action) =>
  request(`/strategy/${strategyId}/action`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })

/** 更新策略参数；restart=true 则自动重启策略 */
export const updateStrategyParams = (strategyId, params, restart = false) =>
  request(`/strategies/${strategyId}/params`, {
    method: 'PUT',
    body: JSON.stringify({ params, restart }),
  })

/** 批量更新策略权重 {strategyId: 0.0~1.0} */
export const updateWeights = (weights) =>
  request('/strategies/weights', {
    method: 'PUT',
    body: JSON.stringify({ weights }),
  })

// ── 仪表盘 ────────────────────────────────────────────────────────────────
export const fetchDashboardMetrics = () => request('/dashboard/metrics')

// ── 订单簿 ────────────────────────────────────────────────────────────────
/** 所有委托单（最近 500 条） */
export const fetchOrders = () => request('/orders')

/** 所有成交记录（最近 500 条） */
export const fetchTrades = () => request('/trades')

/** 撤销委托单 */
export const cancelOrder = (orderId) =>
  request(`/orders/${orderId}`, { method: 'DELETE' })

// ── 手动交易 ──────────────────────────────────────────────────────────────
/**
 * 手动下单
 * @param {object} body
 * @param {string} body.symbol     合约代码
 * @param {string} body.direction  "long" | "short"
 * @param {string} body.offset     "open" | "close" | "close_today" | "close_yesterday"
 * @param {number} body.price      委托价（0 = 市价）
 * @param {number} body.volume     数量
 * @param {string} body.order_type "market" | "limit"
 */
export const placeOrder = (body) =>
  request('/orders', { method: 'POST', body: JSON.stringify(body) })

/** 一键撤销所有活跃委托 */
export const cancelAllOrders = () =>
  request('/orders/cancel-all', { method: 'POST' })

/**
 * 快捷平仓指定合约
 * @param {string} symbol 合约代码
 * @param {object} body   { volume: 0(全部), price: 0(市价) }
 */
export const closePosition = (symbol, body = {}) =>
  request(`/positions/${encodeURIComponent(symbol)}/close`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

// ── 持仓 ──────────────────────────────────────────────────────────────────
export const fetchPositions = () => request('/positions')

// ── 系统日志 ──────────────────────────────────────────────────────────────
/** 查询系统日志；level: DEBUG/INFO/WARNING/ERROR，q: 关键词 */
export const fetchSystemLogs = ({ level = '', q = '', limit = 200 } = {}) => {
  const params = new URLSearchParams()
  if (level) params.set('level', level)
  if (q)     params.set('q', q)
  if (limit) params.set('limit', limit)
  return request(`/system/logs?${params}`)
}

// ── 回测 ──────────────────────────────────────────────────────────────────
/** 可用策略列表及默认参数 */
export const fetchBacktestStrategies = () => request('/backtest/strategies')

/** 运行回测；body = BacktestRunRequest */
export const runBacktest = (body) =>
  request('/backtest/run', { method: 'POST', body: JSON.stringify(body) })

// ── 行情 Watch ─────────────────────────────────────────────────────────────
/** 期货合约搜索；query: 关键词，exchange: 可选交易所，limit: 最多返回条数 */
export const searchContracts = ({ query = '', exchange = '', limit = 60 } = {}) => {
  const p = new URLSearchParams()
  if (query)    p.set('query',    query)
  if (exchange) p.set('exchange', exchange)
  if (limit)    p.set('limit',    String(limit))
  return request(`/watch/search?${p}`)
}

/**
 * 获取 K 线数据及技术指标
 * @param {object} opts
 * @param {string} opts.symbol     合约代码
 * @param {string} opts.interval   周期 1m/5m/15m/30m/1h/4h/1d/1w
 * @param {number} opts.limit      返回条数（最多 1000）
 * @param {string} opts.indicators 逗号分隔指标，如 "ma20,ma60,macd,rsi14"
 * @param {string} [opts.since]    ISO datetime，仅返回该时刻之后的 bar
 */
export const fetchKline = ({ symbol, interval = '1d', limit = 100, indicators = '', since } = {}) => {
  const p = new URLSearchParams({ symbol, interval, limit: String(limit) })
  if (indicators) p.set('indicators', indicators)
  if (since)      p.set('since',      since)
  return request(`/watch/kline?${p}`)
}

/** 清除服务端 K 线缓存（可选指定合约） */
export const clearKlineCache = (symbol = '') => {
  const p = symbol ? `?symbol=${symbol}` : ''
  return request(`/watch/kline/cache${p}`, { method: 'DELETE' })
}
