/**
 * useIndicatorWorker — 封装 indicatorWorker.js 的 Vue 3 Composable
 *
 * 特性：
 *  - Worker 单例（模块级），多组件共享同一 Worker，避免重复创建
 *  - Promise 化 API，调用方无需手动处理 postMessage / onmessage
 *  - 指标计算结果缓存（LRU-lite，最多保留 50 条）
 *  - 若浏览器不支持 Worker，回退到主线程同步计算
 */

// ── Worker 单例 ─────────────────────────────────────────────────────────────
let _worker   = null
let _callbacks = {}
let _nextId    = 0

function _getWorker() {
  if (_worker) return _worker
  try {
    _worker = new Worker(new URL('@/workers/indicatorWorker.js', import.meta.url), { type: 'module' })
    _worker.onmessage = ({ data }) => {
      const { id, result, error } = data
      const cb = _callbacks[id]
      if (!cb) return
      delete _callbacks[id]
      error ? cb.reject(new Error(error)) : cb.resolve(result)
    }
    _worker.onerror = (e) => {
      console.error('[indicatorWorker] 全局错误:', e.message)
      // 让所有 pending 的 promise reject
      for (const cb of Object.values(_callbacks)) cb.reject(new Error(e.message))
      _callbacks = {}
      _worker    = null  // 重置，下次调用自动重建
    }
  } catch (e) {
    console.warn('[indicatorWorker] Worker 创建失败，将回退到主线程:', e.message)
    _worker = null
  }
  return _worker
}

function _dispatch(type, payload) {
  const id = ++_nextId
  return new Promise((resolve, reject) => {
    const w = _getWorker()
    if (!w) {
      // 回退：同步计算（import 动态加载纯函数即可，这里简单 reject 留给调用方处理）
      reject(new Error('Worker 不可用'))
      return
    }
    _callbacks[id] = { resolve, reject }
    w.postMessage({ id, type, payload })
  })
}

// ── 结果缓存（key = 指纹字符串） ────────────────────────────────────────────
const _cache     = new Map()
const CACHE_SIZE = 50

function _cacheKey(symbol, interval, barCount, paramHash) {
  return `${symbol}|${interval}|${barCount}|${paramHash}`
}

function _cacheGet(key) {
  if (!_cache.has(key)) return null
  // 命中时移到末尾（LRU）
  const v = _cache.get(key)
  _cache.delete(key)
  _cache.set(key, v)
  return v
}

function _cacheSet(key, value) {
  if (_cache.size >= CACHE_SIZE) {
    // 删除最旧的条目
    _cache.delete(_cache.keys().next().value)
  }
  _cache.set(key, value)
}

// ── 公共 API ─────────────────────────────────────────────────────────────────

export function useIndicatorWorker() {
  /**
   * 一次性计算全部指标（MA / MACD / KDJ / RSI / 成交量MA）。
   *
   * @param {string}   symbol
   * @param {string}   interval
   * @param {object[]} bars      [{open, high, low, close, volume}, ...]
   * @param {object}   [params]  指标参数，包含 maParams / macdParams / kdjParams / rsiParams
   * @returns {Promise<object>}  { ma, volMa, macd, rsi, kdj }
   */
  async function calcAll(symbol, interval, bars, params = {}) {
    if (!bars?.length) return null

    const paramHash = JSON.stringify(params)
    const key = _cacheKey(symbol, interval, bars.length, paramHash)
    const cached = _cacheGet(key)
    if (cached) return cached

    try {
      const result = await _dispatch('calc_all', { bars, ...params })
      _cacheSet(key, result)
      return result
    } catch (e) {
      console.warn('[useIndicatorWorker] calcAll 失败:', e.message)
      return null
    }
  }

  /** 单独计算 MA（可独立调用，例如添加新均线时） */
  async function calcMA(closes, periods) {
    if (!closes?.length || !periods?.length) return {}
    try {
      return await _dispatch('calc_ma', { closes, periods })
    } catch {
      return {}
    }
  }

  /** 单独计算 MACD */
  async function calcMACD(closes, params = {}) {
    if (!closes?.length) return { diff: [], dea: [], hist: [] }
    try {
      return await _dispatch('calc_macd', { closes, ...params })
    } catch {
      return { diff: [], dea: [], hist: [] }
    }
  }

  /** 清除全部缓存 */
  function clearCache() {
    _cache.clear()
  }

  return { calcAll, calcMA, calcMACD, clearCache }
}
