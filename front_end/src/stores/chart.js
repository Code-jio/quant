/**
 * useChartStore — 图表样式配置状态
 *
 * 职责：
 *  - 维护全局图表颜色、线型等样式配置
 *  - 为每个品种单独存储覆写配置（用户可为不同品种定制外观）
 *  - 所有配置持久化到 localStorage
 */

import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

// ── localStorage 键 ───────────────────────────────────────────────────────
const LS_GLOBAL_CFG  = 'quant_chart_global'
const LS_SYMBOL_CFG  = 'quant_chart_symbols'

// ── 默认值 ────────────────────────────────────────────────────────────────
/** 全局默认图表配置 */
export const DEFAULT_CHART_CONFIG = {
  /** K 线阳线颜色（涨） */
  upColor:     '#ef4444',
  /** K 线阴线颜色（跌） */
  downColor:   '#22c55e',
  /** 图表背景色 */
  bgColor:     '#0f0f1a',
  /** 网格线颜色 */
  gridColor:   '#222222',
  /** 坐标轴文字颜色 */
  textColor:   '#888888',
  /** K 线主图高度比例 0~1 */
  mainHeightRatio: 0.60,
  /** 成交量副图高度比例 */
  volHeightRatio:  0.15,
  /** 指标副图高度比例 */
  indHeightRatio:  0.15,
  /** K 线最大柱宽（像素） */
  candleMaxWidth:  16,
  /** 是否显示成交量副图 */
  showVolume: true,
  /** 是否显示指标副图 */
  showIndicator: true,
}

// ── 工具函数 ──────────────────────────────────────────────────────────────
function readLS(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback }
  catch { return fallback }
}

function writeLS(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) }
  catch { /* quota exceeded */ }
}

function mergeConfig(base, overrides) {
  return { ...base, ...(overrides ?? {}) }
}

// ── Store ──────────────────────────────────────────────────────────────────
export const useChartStore = defineStore('chart', () => {

  // ── 全局配置 ──────────────────────────────────────────────────────────
  const globalConfig = ref(
    mergeConfig(DEFAULT_CHART_CONFIG, readLS(LS_GLOBAL_CFG, {}))
  )

  // ── 每品种覆写配置 ──────────────────────────────────────────────────
  /** { [symbol]: Partial<ChartConfig> } */
  const perSymbolConfig = ref(readLS(LS_SYMBOL_CFG, {}))

  // ── 计算属性 ──────────────────────────────────────────────────────────
  /**
   * 获取指定品种的最终配置（全局 + 品种覆写合并）
   * 用法：chartStore.resolvedConfig('IF2506')
   */
  const resolvedConfig = computed(() => (symbol) =>
    mergeConfig(globalConfig.value, symbol ? (perSymbolConfig.value[symbol] ?? {}) : {})
  )

  // ── 操作 ──────────────────────────────────────────────────────────────

  /** 更新全局配置（部分字段覆写） */
  function setGlobalConfig(patch) {
    globalConfig.value = { ...globalConfig.value, ...patch }
    writeLS(LS_GLOBAL_CFG, globalConfig.value)
  }

  /** 重置全局配置为默认值 */
  function resetGlobalConfig() {
    globalConfig.value = { ...DEFAULT_CHART_CONFIG }
    writeLS(LS_GLOBAL_CFG, globalConfig.value)
  }

  /** 为指定品种设置覆写配置（部分字段） */
  function setSymbolConfig(symbol, patch) {
    if (!symbol) return
    perSymbolConfig.value = {
      ...perSymbolConfig.value,
      [symbol]: { ...(perSymbolConfig.value[symbol] ?? {}), ...patch },
    }
    writeLS(LS_SYMBOL_CFG, perSymbolConfig.value)
  }

  /** 清除指定品种的覆写配置，恢复使用全局配置 */
  function resetSymbolConfig(symbol) {
    if (!symbol || !perSymbolConfig.value[symbol]) return
    const next = { ...perSymbolConfig.value }
    delete next[symbol]
    perSymbolConfig.value = next
    writeLS(LS_SYMBOL_CFG, perSymbolConfig.value)
  }

  /** 清除所有品种覆写配置 */
  function clearAllSymbolConfigs() {
    perSymbolConfig.value = {}
    writeLS(LS_SYMBOL_CFG, {})
  }

  // ── 暴露 ──────────────────────────────────────────────────────────────
  return {
    globalConfig,
    perSymbolConfig,
    resolvedConfig,
    setGlobalConfig,
    resetGlobalConfig,
    setSymbolConfig,
    resetSymbolConfig,
    clearAllSymbolConfigs,
  }
})
