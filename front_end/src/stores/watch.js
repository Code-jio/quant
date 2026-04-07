/**
 * useWatchStore — 盯盘系统核心状态
 *
 * 职责：
 *  - 管理当前选中品种和 K 线周期
 *  - 维护监控列表（固定观察的品种）
 *  - 缓存各品种+周期的 K 线数据，避免重复请求
 *  - 监控列表持久化到 localStorage
 */

import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

// ── localStorage 键 ───────────────────────────────────────────────────────
const LS_WATCH_LIST  = 'quant_watch_list'
const LS_CUR_SYMBOL  = 'quant_cur_symbol'
const LS_CUR_INTERVAL = 'quant_cur_interval'

/** K 线缓存最大条目数（超出后按 LRU 淘汰） */
const MAX_CACHE = 30
/** 缓存有效期：15 分钟（毫秒） */
const CACHE_TTL = 15 * 60 * 1000

// ── 工具函数 ──────────────────────────────────────────────────────────────
function readLS(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback }
  catch { return fallback }
}

function writeLS(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) }
  catch { /* quota exceeded, ignore */ }
}

// ── Store ──────────────────────────────────────────────────────────────────
export const useWatchStore = defineStore('watch', () => {

  // ── 当前品种 & 周期 ───────────────────────────────────────────────────
  /** 当前选中的合约对象 { symbol, name, exchange, product_type } */
  const currentSymbol   = ref(readLS(LS_CUR_SYMBOL,   null))
  /** 当前 K 线周期字符串，如 '1d' */
  const currentInterval = ref(readLS(LS_CUR_INTERVAL, '1d'))

  /** 复合缓存键 */
  const cacheKey = computed(() =>
    currentSymbol.value ? `${currentSymbol.value.symbol}_${currentInterval.value}` : ''
  )

  // ── 监控列表 ──────────────────────────────────────────────────────────
  /** 固定观察的品种列表，最多 20 条 */
  const watchList = ref(readLS(LS_WATCH_LIST, []))

  const isWatched = computed(() => (symbol) =>
    watchList.value.some(c => c.symbol === symbol)
  )

  function addToWatchList(contract) {
    if (watchList.value.some(c => c.symbol === contract.symbol)) return
    watchList.value.unshift({ ...contract })
    if (watchList.value.length > 20) watchList.value.pop()
    writeLS(LS_WATCH_LIST, watchList.value)
  }

  function removeFromWatchList(symbol) {
    watchList.value = watchList.value.filter(c => c.symbol !== symbol)
    writeLS(LS_WATCH_LIST, watchList.value)
  }

  function toggleWatchList(contract) {
    if (isWatched.value(contract.symbol)) {
      removeFromWatchList(contract.symbol)
    } else {
      addToWatchList(contract)
    }
  }

  // ── K 线数据缓存（内存，不持久化） ────────────────────────────────────
  /**
   * 内存缓存结构：Map<cacheKey, { bars, indicators, fetchedAt }>
   * fetchedAt 用于 TTL 判断
   */
  const _klineCache = new Map()

  function setCacheEntry(symbol, interval, data) {
    const key = `${symbol}_${interval}`
    _klineCache.set(key, { ...data, fetchedAt: Date.now() })
    // LRU 淘汰：超出上限则删除最旧的条目
    if (_klineCache.size > MAX_CACHE) {
      const oldest = [..._klineCache.entries()]
        .sort((a, b) => a[1].fetchedAt - b[1].fetchedAt)[0]
      if (oldest) _klineCache.delete(oldest[0])
    }
  }

  function getCacheEntry(symbol, interval) {
    const key = `${symbol}_${interval}`
    const entry = _klineCache.get(key)
    if (!entry) return null
    if (Date.now() - entry.fetchedAt > CACHE_TTL) {
      _klineCache.delete(key)
      return null
    }
    return entry
  }

  function clearCache(symbol = null) {
    if (symbol) {
      for (const key of _klineCache.keys()) {
        if (key.startsWith(symbol + '_')) _klineCache.delete(key)
      }
    } else {
      _klineCache.clear()
    }
  }

  // ── 切换品种 / 周期 ────────────────────────────────────────────────────
  function setSymbol(contract) {
    currentSymbol.value = contract ? { ...contract } : null
    writeLS(LS_CUR_SYMBOL, currentSymbol.value)
  }

  function setInterval(interval) {
    currentInterval.value = interval
    writeLS(LS_CUR_INTERVAL, interval)
  }

  // ── 暴露 ──────────────────────────────────────────────────────────────
  return {
    // 状态
    currentSymbol,
    currentInterval,
    cacheKey,
    watchList,
    // 计算
    isWatched,
    // 操作
    setSymbol,
    setInterval,
    addToWatchList,
    removeFromWatchList,
    toggleWatchList,
    setCacheEntry,
    getCacheEntry,
    clearCache,
  }
})
