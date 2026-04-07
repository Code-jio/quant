/**
 * useIndicatorStore — 技术指标配置状态
 *
 * 职责：
 *  - 管理均线列表（周期、颜色、线型、显示开关）
 *  - 管理当前激活的副图指标（MACD / KDJ / RSI）
 *  - 存储各指标的计算参数
 *  - 所有配置持久化到 localStorage
 */

import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

// ── localStorage 键 ───────────────────────────────────────────────────────
const LS_MA_LIST       = 'quant_indicator_ma'
const LS_ACTIVE_IND    = 'quant_indicator_active'
const LS_MACD_PARAMS   = 'quant_indicator_macd'
const LS_KDJ_PARAMS    = 'quant_indicator_kdj'
const LS_RSI_PARAMS    = 'quant_indicator_rsi'
const LS_VOL_MA        = 'quant_indicator_vol_ma'

// ── 默认值 ────────────────────────────────────────────────────────────────
/** 均线可选颜色预设 */
export const MA_PRESET_COLORS = [
  '#f5c842', '#f09a00', '#f06400', '#cc4444',
  '#3b82f6', '#10b981', '#8b5cf6', '#ec4899',
]

/** 默认均线列表 */
export const DEFAULT_MA_LIST = [
  { n: 5,  color: '#f5c842', visible: true, dashType: 'solid' },
  { n: 10, color: '#f09a00', visible: true, dashType: 'solid' },
  { n: 20, color: '#f06400', visible: true, dashType: 'solid' },
  { n: 30, color: '#cc4444', visible: true, dashType: 'solid' },
]

/** 默认 MACD 参数 */
export const DEFAULT_MACD = { fast: 12, slow: 26, signal: 9 }

/** 默认 KDJ 参数 */
export const DEFAULT_KDJ  = { n: 9, m1: 3, m2: 3 }

/** 默认 RSI 参数 */
export const DEFAULT_RSI  = { period: 14, overbought: 80, oversold: 20 }

/** 默认成交量均线周期 */
export const DEFAULT_VOL_MA = { n: 5, enabled: true }

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
export const useIndicatorStore = defineStore('indicator', () => {

  // ── 均线列表 ──────────────────────────────────────────────────────────
  const maList = ref(readLS(LS_MA_LIST, DEFAULT_MA_LIST.map(m => ({ ...m }))))

  /** 所有可见均线 */
  const visibleMas = computed(() => maList.value.filter(m => m.visible))

  /** 需要请求的均线周期数组 */
  const maNums = computed(() => maList.value.map(m => m.n))

  /** 添加均线；若同周期已存在则忽略 */
  function addMa(n, color = null) {
    const num = Number(n)
    if (!num || num < 1 || maList.value.some(m => m.n === num)) return false
    const autoColor = MA_PRESET_COLORS[maList.value.length % MA_PRESET_COLORS.length]
    maList.value.push({
      n:       num,
      color:   color ?? autoColor,
      visible: true,
      dashType:'solid',
    })
    // 按周期排序
    maList.value.sort((a, b) => a.n - b.n)
    writeLS(LS_MA_LIST, maList.value)
    return true
  }

  /** 删除指定周期均线 */
  function removeMa(n) {
    maList.value = maList.value.filter(m => m.n !== n)
    writeLS(LS_MA_LIST, maList.value)
  }

  /** 更新指定周期均线的部分字段 */
  function updateMa(n, patch) {
    const ma = maList.value.find(m => m.n === n)
    if (!ma) return
    Object.assign(ma, patch)
    writeLS(LS_MA_LIST, maList.value)
  }

  /** 重置均线列表为默认值 */
  function resetMaList() {
    maList.value = DEFAULT_MA_LIST.map(m => ({ ...m }))
    writeLS(LS_MA_LIST, maList.value)
  }

  // ── 激活指标 ──────────────────────────────────────────────────────────
  /** 当前副图显示的指标：'macd' | 'kdj' | 'rsi' */
  const activeIndicator = ref(readLS(LS_ACTIVE_IND, 'macd'))

  function setActiveIndicator(name) {
    activeIndicator.value = name
    writeLS(LS_ACTIVE_IND, name)
  }

  // ── MACD 参数 ──────────────────────────────────────────────────────────
  const macdParams = ref({ ...DEFAULT_MACD, ...readLS(LS_MACD_PARAMS, {}) })

  function updateMacdParams(patch) {
    macdParams.value = { ...macdParams.value, ...patch }
    writeLS(LS_MACD_PARAMS, macdParams.value)
  }

  function resetMacdParams() {
    macdParams.value = { ...DEFAULT_MACD }
    writeLS(LS_MACD_PARAMS, macdParams.value)
  }

  // ── KDJ 参数 ───────────────────────────────────────────────────────────
  const kdjParams = ref({ ...DEFAULT_KDJ, ...readLS(LS_KDJ_PARAMS, {}) })

  function updateKdjParams(patch) {
    kdjParams.value = { ...kdjParams.value, ...patch }
    writeLS(LS_KDJ_PARAMS, kdjParams.value)
  }

  function resetKdjParams() {
    kdjParams.value = { ...DEFAULT_KDJ }
    writeLS(LS_KDJ_PARAMS, kdjParams.value)
  }

  // ── RSI 参数 ───────────────────────────────────────────────────────────
  const rsiParams = ref({ ...DEFAULT_RSI, ...readLS(LS_RSI_PARAMS, {}) })

  function updateRsiParams(patch) {
    rsiParams.value = { ...rsiParams.value, ...patch }
    writeLS(LS_RSI_PARAMS, rsiParams.value)
  }

  function resetRsiParams() {
    rsiParams.value = { ...DEFAULT_RSI }
    writeLS(LS_RSI_PARAMS, rsiParams.value)
  }

  // ── 成交量均线 ────────────────────────────────────────────────────────
  const volMa = ref({ ...DEFAULT_VOL_MA, ...readLS(LS_VOL_MA, {}) })

  function updateVolMa(patch) {
    volMa.value = { ...volMa.value, ...patch }
    writeLS(LS_VOL_MA, volMa.value)
  }

  // ── 构造请求所需的 indicators 字符串 ─────────────────────────────────
  /**
   * 生成传给 fetchKline 的 indicators 参数字符串。
   * 包含所有均线 + 成交量均线 + 当前激活的技术指标。
   */
  const indicatorString = computed(() => {
    const parts = maNums.value.map(n => `ma${n}`)
    if (volMa.value.enabled) {
      parts.push(`vol_ma${volMa.value.n}`)
    }
    parts.push('macd', 'kdj', `rsi${rsiParams.value.period}`)
    return parts.join(',')
  })

  // ── 暴露 ──────────────────────────────────────────────────────────────
  return {
    // 均线
    maList,
    visibleMas,
    maNums,
    addMa,
    removeMa,
    updateMa,
    resetMaList,
    // 指标选择
    activeIndicator,
    setActiveIndicator,
    // 参数
    macdParams, updateMacdParams, resetMacdParams,
    kdjParams,  updateKdjParams,  resetKdjParams,
    rsiParams,  updateRsiParams,  resetRsiParams,
    volMa,      updateVolMa,
    // 汇总
    indicatorString,
  }
})
