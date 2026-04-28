/**
 * useContractSearch — 期货合约搜索 composable
 *
 * 职责：
 *  - 防抖搜索（300ms），调用 GET /api/watch/search
 *  - 管理收藏（favorites）和最近查看（recent）—— 持久化到 localStorage
 *  - 键盘导航（↑↓ 选行，Enter 确认，Escape 清空）
 *  - 提供常量：EXCHANGES / HOT_CONTRACTS
 */

import { ref, watch } from 'vue'
import { buildApiUrl } from '@/config/network.js'

// ── 常量 ──────────────────────────────────────────────────────────────────
export const EXCHANGES = ['SHFE', 'DCE', 'CZCE', 'CFFEX', 'INE', 'GFEX']

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

export const HOT_CONTRACTS = [
  { symbol: 'IF2506',  name: '沪深300 2506', exchange: 'CFFEX', product_type: '金融' },
  { symbol: 'IC2506',  name: '中证500 2506',  exchange: 'CFFEX', product_type: '金融' },
  { symbol: 'rb2601',  name: '螺纹钢 2601',   exchange: 'SHFE',  product_type: '黑色' },
  { symbol: 'au2506',  name: '黄金 2506',     exchange: 'SHFE',  product_type: '贵金属' },
  { symbol: 'ag2506',  name: '白银 2506',     exchange: 'SHFE',  product_type: '贵金属' },
  { symbol: 'cu2506',  name: '铜 2506',       exchange: 'SHFE',  product_type: '有色' },
  { symbol: 'i2509',   name: '铁矿石 2509',   exchange: 'DCE',   product_type: '黑色' },
  { symbol: 'MA2509',  name: '甲醇 2509',     exchange: 'CZCE',  product_type: '化工' },
  { symbol: 'sc2506',  name: '原油 2506',     exchange: 'INE',   product_type: '能源' },
  { symbol: 'c2509',   name: '玉米 2509',     exchange: 'DCE',   product_type: '农产品' },
  { symbol: 'SR2509',  name: '白糖 2509',     exchange: 'CZCE',  product_type: '农产品' },
  { symbol: 'si2506',  name: '工业硅 2506',   exchange: 'GFEX',  product_type: '化工' },
]

// ── localStorage 键 ────────────────────────────────────────────────────────
const LS_FAVORITES = 'quant_contract_favorites'
const LS_RECENT    = 'quant_contract_recent'
const MAX_RECENT   = 5
const MAX_FAV      = 30

// ── API base ───────────────────────────────────────────────────────────────
// ── 工具 ───────────────────────────────────────────────────────────────────
function readLS(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback }
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
  const favorites = ref(readLS(LS_FAVORITES, []))
  const recent    = ref(readLS(LS_RECENT,    []))

  function _saveFav()    { localStorage.setItem(LS_FAVORITES, JSON.stringify(favorites.value)) }
  function _saveRecent() { localStorage.setItem(LS_RECENT,    JSON.stringify(recent.value)) }

  // ── 收藏 ──────────────────────────────────────────────────────────────────
  function isFavorite(symbol) {
    return favorites.value.some(f => f.symbol === symbol)
  }

  function toggleFavorite(contract) {
    const idx = favorites.value.findIndex(f => f.symbol === contract.symbol)
    if (idx >= 0) {
      favorites.value.splice(idx, 1)
    } else {
      favorites.value.unshift({ ...contract })
      if (favorites.value.length > MAX_FAV) favorites.value.pop()
    }
    _saveFav()
  }

  // ── 最近查看 ──────────────────────────────────────────────────────────────
  function addToRecent(contract) {
    const idx = recent.value.findIndex(r => r.symbol === contract.symbol)
    if (idx >= 0) recent.value.splice(idx, 1)
    recent.value.unshift({ ...contract })
    if (recent.value.length > MAX_RECENT) recent.value.pop()
    _saveRecent()
  }

  function removeRecent(symbol) {
    recent.value = recent.value.filter(r => r.symbol !== symbol)
    _saveRecent()
  }

  // ── 搜索 ──────────────────────────────────────────────────────────────────
  let _timer = null

  async function _doSearch() {
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
      const p = new URLSearchParams()
      if (q)              p.set('query',    q)
      if (exchange.value) p.set('exchange', exchange.value)
      p.set('limit', '60')

      const res  = await fetch(buildApiUrl(`/watch/search?${p}`), { credentials: 'include' })
      const data = await res.json()

      results.value = data.code === 0 ? (data.data ?? []) : []
      if (data.code !== 0) error.value = data.msg ?? '搜索失败'
    } catch (e) {
      results.value = []
      error.value   = '网络错误: ' + e.message
    } finally {
      loading.value = false
    }
  }

  /** 带防抖的触发（300ms） */
  function triggerSearch() {
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
    addToRecent, removeRecent,
    triggerSearch, triggerImmediate, clearSearch,
    handleKeydown,
  }
}
