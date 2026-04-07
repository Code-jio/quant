/**
 * useHistoryStore — 查看历史状态
 *
 * 职责：
 *  - 记录最近访问的品种列表（最多 20 条，带时间戳）
 *  - 记录每个品种最后使用的 K 线周期（方便下次打开时恢复）
 *  - 记录访问次数，支持"最常看"排序
 *  - 所有数据持久化到 localStorage
 */

import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

// ── localStorage 键 ───────────────────────────────────────────────────────
const LS_RECENT        = 'quant_history_recent'
const LS_LAST_INTERVAL = 'quant_history_intervals'
const LS_VISIT_COUNT   = 'quant_history_counts'

const MAX_RECENT = 20

// ── 工具函数 ──────────────────────────────────────────────────────────────
function readLS(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback }
  catch { return fallback }
}

function writeLS(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) }
  catch { /* quota exceeded */ }
}

// ── Store ──────────────────────────────────────────────────────────────────
export const useHistoryStore = defineStore('history', () => {

  // ── 最近访问列表 ──────────────────────────────────────────────────────
  /**
   * 最近访问的品种列表，每项结构：
   * { symbol, name, exchange, product_type, visitedAt: timestamp }
   * 按 visitedAt 降序排列，最新的在前
   */
  const recentSymbols = ref(readLS(LS_RECENT, []))

  // ── 每品种最后使用的周期 ──────────────────────────────────────────────
  /** { [symbol]: interval } */
  const lastIntervals = ref(readLS(LS_LAST_INTERVAL, {}))

  // ── 访问计数 ──────────────────────────────────────────────────────────
  /** { [symbol]: number } */
  const visitCounts = ref(readLS(LS_VISIT_COUNT, {}))

  // ── 计算属性 ──────────────────────────────────────────────────────────
  /** 按访问频次降序的 Top 10 品种 */
  const mostVisited = computed(() => {
    return [...recentSymbols.value]
      .sort((a, b) => (visitCounts.value[b.symbol] ?? 0) - (visitCounts.value[a.symbol] ?? 0))
      .slice(0, 10)
  })

  /** 今日访问过的品种（按时间戳判断） */
  const todayVisited = computed(() => {
    const today = new Date().setHours(0, 0, 0, 0)
    return recentSymbols.value.filter(c => (c.visitedAt ?? 0) >= today)
  })

  // ── 操作 ──────────────────────────────────────────────────────────────

  /**
   * 记录一次品种访问
   * @param {object} contract  { symbol, name, exchange, product_type }
   * @param {string} interval  当前使用的周期，如 '1d'
   */
  function addVisit(contract, interval) {
    if (!contract?.symbol) return

    // 更新最近列表
    const existIdx = recentSymbols.value.findIndex(c => c.symbol === contract.symbol)
    const entry = { ...contract, visitedAt: Date.now() }

    if (existIdx >= 0) {
      recentSymbols.value.splice(existIdx, 1)
    }
    recentSymbols.value.unshift(entry)

    if (recentSymbols.value.length > MAX_RECENT) {
      recentSymbols.value = recentSymbols.value.slice(0, MAX_RECENT)
    }
    writeLS(LS_RECENT, recentSymbols.value)

    // 记录最后周期
    if (interval) {
      lastIntervals.value = { ...lastIntervals.value, [contract.symbol]: interval }
      writeLS(LS_LAST_INTERVAL, lastIntervals.value)
    }

    // 计数 +1
    visitCounts.value = {
      ...visitCounts.value,
      [contract.symbol]: (visitCounts.value[contract.symbol] ?? 0) + 1,
    }
    writeLS(LS_VISIT_COUNT, visitCounts.value)
  }

  /**
   * 从历史中移除某品种
   */
  function removeRecent(symbol) {
    recentSymbols.value = recentSymbols.value.filter(c => c.symbol !== symbol)
    writeLS(LS_RECENT, recentSymbols.value)
  }

  /**
   * 获取某品种上次使用的周期，若没有则返回默认值
   */
  function getLastInterval(symbol, defaultInterval = '1d') {
    return lastIntervals.value[symbol] ?? defaultInterval
  }

  /**
   * 清空所有历史记录
   */
  function clearHistory() {
    recentSymbols.value = []
    lastIntervals.value = {}
    visitCounts.value   = {}
    writeLS(LS_RECENT,        [])
    writeLS(LS_LAST_INTERVAL, {})
    writeLS(LS_VISIT_COUNT,   {})
  }

  /**
   * 仅清除某品种的历史访问次数
   */
  function resetVisitCount(symbol) {
    const next = { ...visitCounts.value }
    delete next[symbol]
    visitCounts.value = next
    writeLS(LS_VISIT_COUNT, visitCounts.value)
  }

  // ── 暴露 ──────────────────────────────────────────────────────────────
  return {
    recentSymbols,
    lastIntervals,
    visitCounts,
    mostVisited,
    todayVisited,
    addVisit,
    removeRecent,
    getLastInterval,
    clearHistory,
    resetVisitCount,
  }
})
