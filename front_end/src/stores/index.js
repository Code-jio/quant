/**
 * stores/index.js — 统一导出入口
 *
 * 使用方式：
 *   import { useWatchStore, useChartStore, useIndicatorStore, useHistoryStore } from '@/stores'
 */

export { useWatchStore }     from './watch.js'
export { useChartStore,
         DEFAULT_CHART_CONFIG }   from './chart.js'
export { useIndicatorStore,
         DEFAULT_MA_LIST,
         DEFAULT_MACD,
         DEFAULT_KDJ,
         DEFAULT_RSI,
         DEFAULT_VOL_MA,
         MA_PRESET_COLORS }       from './indicator.js'
export { useHistoryStore }   from './history.js'
export { useAuthStore }      from './auth.js'
