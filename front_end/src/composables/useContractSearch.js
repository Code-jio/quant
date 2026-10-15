/**
 * useContractSearch — 期货合约搜索 composable
 *
 * 职责：
 *  - 防抖搜索（300ms），调用 GET /api/watch/search
 *  - 管理收藏（favorites）和最近查看（recent）—— 持久化到 localStorage
 *  - 键盘导航（↑↓ 选行，Enter 确认，Escape 清空）
 *  - 提供常量：EXCHANGES / HOT_CONTRACTS
 */

import { ref, computed, watch, reactive, onMounted, onScopeDispose } from 'vue'
import { searchContracts } from '@/api/index.js'
import { DOMESTIC_EXCHANGES, domesticFutures, isDomesticFuture } from '@/utils/domesticFutures.js'

// ── 常量 ──────────────────────────────────────────────────────────────────
export const EXCHANGES = DOMESTIC_EXCHANGES

/** 每个交易所对应的 Element Plus tag type */
export const EXCH_COLOR = {
  SHFE:  'danger',
  DCE:   'warning',
  CZCE:  'success',
  CFFEX: '',
  INE:   'info',
  GFEX:  'primary',
}

/** 产品类型对应徽标颜色 */
export const TYPE_COLOR = {
  金融:   '#3b82f6',
  贵金属: '#f59e0b',
  有色:   '#f97316',
  黑色:   '#6b7280',
  能源:   '#ef4444',
  化工:   '#8b5cf6',
  农产品: '#10b981',
  油脂:   '#84cc16',
}

// Domestic futures from the gateway, not a volume ranking.
export const HOT_CONTRACTS = reactive([])

// ── localStorage 键 ────────────────────────────────────────────────────────
const LS_FAVORITES = 'quant_contract_favorites'
const LS_RECENT    = 'quant_contract_recent'
const MAX_RECENT   = 5
const MAX_FAV      = 30

// ── API base ───────────────────────────────────────────────────────────────
// ── 工具 ───────────────────────────────────────────────────────────────────
function readLS(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key))
    return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : fallback
  }
  catch { return fallback }
}

// ── Composable ─────────────────────────────────────────────────────────────
export function useContractSearch() {
  const query         = ref('')
  const exchange      = ref('')
  const results       = ref([])
  const loading       = ref(false)
  const error         = ref('')
  const selectedIndex = ref(-1)

  // 持久化状态
  const savedFavorites = ref(readLS(LS_FAVORITES, []))
  const savedRecent = ref(readLS(LS_RECENT, []))
  const favorites = computed(() => domesticFutures(savedFavorites.value))
  const recent = computed(() => domesticFutures(savedRecent.value))

  function _saveFav()    { localStorage.setItem(LS_FAVORITES, JSON.stringify(savedFavorites.value)) }
  function _saveRecent() { localStorage.setItem(LS_RECENT, JSON.stringify(savedRecent.value)) }

  // ── 收藏 ──────────────────────────────────────────────────────────────────
  function isFavorite(symbol) {
    return favorites.value.some(f => f.symbol === symbol)
  }

  function toggleFavorite(contract) {
    if (!isDomesticFuture(contract)) return
    const idx = savedFavorites.value.findIndex(f => f.symbol === contract.symbol)
    if (idx >= 0 && isDomesticFuture(savedFavorites.value[idx])) {
      savedFavorites.value.splice(idx, 1)
    } else if (idx >= 0) {
      savedFavorites.value.splice(idx, 1, { ...contract })
    } else {
      savedFavorites.value.unshift({ ...contract })
      if (savedFavorites.value.length > MAX_FAV) savedFavorites.value.pop()
    }
    _saveFav()
  }

  // ── 最近查看 ──────────────────────────────────────────────────────────────
  function addToRecent(contract) {
    if (!isDomesticFuture(contract)) return
    const idx = savedRecent.value.findIndex(r => r.symbol === contract.symbol)
    if (idx >= 0) savedRecent.value.splice(idx, 1)
    savedRecent.value.unshift({ ...contract })
    if (savedRecent.value.length > MAX_RECENT) savedRecent.value.pop()
    _saveRecent()
  }

  function removeRecent(symbol) {
    savedRecent.value = savedRecent.value.filter(r => r.symbol !== symbol)
    _saveRecent()
  }

  function clearRecent() {
    savedRecent.value = []
    _saveRecent()
  }

  // ── 搜索 ──────────────────────────────────────────────────────────────────
  let _timer = null
  let generation = 0
  let disposed = false
  onScopeDispose(() => { disposed=true; generation++; clearTimeout(_timer) })
  onMounted(async () => {
    try {
      const data=await searchContracts({limit:60})
      if (!disposed && data.code===0) HOT_CONTRACTS.splice(0,HOT_CONTRACTS.length,...data.data)
    } catch { /* The search box reports request errors when used. */ }
  })

  async function _doSearch() {
    const version=++generation
    const q = query.value.trim()
    if (!q && !exchange.value) {
      results.value = []
      loading.value = false
      return
    }

    loading.value = true
    error.value   = ''
    selectedIndex.value = -1

    try {
      const data=await searchContracts({query:q,exchange:exchange.value,limit:60})
      if (disposed || version!==generation) return

      results.value = data.code === 0 ? (data.data ?? []) : []
      if (data.code !== 0) error.value = data.msg ?? '搜索失败'
    } catch (e) {
      if (disposed || version!==generation) return
      results.value = []
      error.value   = '网络错误: ' + e.message
    } finally {
      if (version===generation) loading.value = false
    }
  }

  /** 带防抖的触发（300ms） */
  function triggerSearch() {
    generation++
    clearTimeout(_timer)
    _timer = setTimeout(_doSearch, 300)
  }

  /** 切换交易所时立即搜索（无防抖） */
  function triggerImmediate() {
    clearTimeout(_timer)
    _doSearch()
  }

  /** 清空搜索 */
  function clearSearch() {
    generation++; clearTimeout(_timer); loading.value=false
    query.value         = ''
    results.value       = []
    selectedIndex.value = -1
    error.value         = ''
  }

  // 监听变化
  watch(query,    triggerSearch)
  watch(exchange, triggerImmediate)

  // ── 键盘导航 ──────────────────────────────────────────────────────────────
  /**
   * 在 el-input 的 @keydown 上使用。
   * @param {KeyboardEvent} e
   * @param {Function} onSelect  接受一个 contract 对象
   */
  function handleKeydown(e, onSelect) {
    const len = results.value.length
    if (e.key === 'Escape') {
      clearSearch()
      return
    }
    if (!len) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      selectedIndex.value = Math.min(selectedIndex.value + 1, len - 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
    } else if (e.key === 'Enter' && selectedIndex.value >= 0) {
      e.preventDefault()
      onSelect(results.value[selectedIndex.value])
    }
  }

  return {
    // 状态
    query, exchange, results, loading, error, selectedIndex,
    favorites, recent,
    // 操作
    isFavorite, toggleFavorite,
    addToRecent, removeRecent, clearRecent,
    triggerSearch, triggerImmediate, clearSearch,
    handleKeydown,
  }
}
