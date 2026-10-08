/**
 * useKlineData — K 线数据获取与指标解析 composable
 *
 * 职责：
 *  - 封装 fetchKline API 调用
 *  - 解析 bars 数组（datetime / OHLCV）
 *  - 解析 MA、MACD、KDJ、RSI 指标数据
 *  - 提供 loading / error 响应状态
 */

import { ref, getCurrentScope, onScopeDispose } from 'vue'
import { fetchKline } from '@/api/index.js'
export { DEFAULT_MA_CONFIG, INTERVALS } from '@/config/kline.js'

// ── 工具函数 ───────────────────────────────────────────────────────────────

/**
 * 将后端返回的 bar 对象归一化为内部格式。
 * 后端实际返回字段为 timestamp（ISO 字符串），也兼容 datetime/time。
 */
function normalizeBar(bar) {
  return {
    time:   bar.timestamp ?? bar.datetime ?? bar.time ?? '',
    open:   Number(bar.open),
    high:   Number(bar.high),
    low:    Number(bar.low),
    close:  Number(bar.close),
    volume: Number(bar.volume ?? bar.vol ?? 0),
  }
}

/**
 * 后端将 K 线和指标合并在同一个扁平记录数组里返回（无独立 indicators 对象）。
 * 本函数从扁平记录数组中提取指定字段，组成 number[] 指标数组。
 * 支持 ma5 / MA5 / ma_5 等多种命名方式。
 */
function pickFromRecords(records, ...keys) {
  for (const k of keys) {
    if (records.length > 0 && k in records[0]) {
      return records.map(r => (r[k] == null ? null : Number(r[k])))
    }
  }
  return null
}

// ── Composable ─────────────────────────────────────────────────────────────

export function useKlineData() {
  let generation=0
  let controller=null
  let context=''
  const provenance=ref('unknown')
  if (getCurrentScope()) onScopeDispose(()=>{ generation++; controller?.abort() })
  // K 线原始数据
  const bars = ref([])

  // 均线数据：key = `ma${n}`，value = number[]
  const maData = ref({})

  // MACD
  const macdData = ref({ diff: [], dea: [], hist: [] })

  // KDJ
  const kdjData = ref({ k: [], d: [], j: [] })

  // RSI
  const rsiData = ref([])

  // 成交量均线（默认 MA5）
  const volMaData = ref([])

  const loading     = ref(false)
  const loadingMore = ref(false)  // 向前翻页加载中
  const error       = ref('')
  const hasMore     = ref(true)   // 是否还有更早的历史数据
  const totalLoaded = ref(0)      // 已加载 bar 数量

  // ── 内部：通用 fetch + 解析 ─────────────────────────────────────────────
  async function _fetch(symbol, interval, limit, maNums, before = null, signal = undefined) {
    const indicatorStr = [
      ...maNums.map(n => `ma${n}`),
      'vol_ma5', 'macd', 'kdj', 'rsi14',
    ].join(',')

    const params = { symbol, interval, limit, indicators: indicatorStr }
    if (before) params.before = before

    const raw = await fetchKline({...params,signal})

    /**
     * 后端实际返回格式（扁平记录数组）：
     * {
     *   code: 0,
     *   data: [
     *     { timestamp, open, high, low, close, volume, ma5, macd, macd_signal, ... },
     *     ...
     *   ]
     * }
     * 兼容旧格式（data.bars + data.indicators 对象）。
     */
    let records = []
    const topData = raw?.data

    if (Array.isArray(topData)) {
      // 新格式：data 直接是记录数组
      records = topData
    } else if (Array.isArray(topData?.bars)) {
      // 旧格式：data.bars + data.indicators（indicators 合并到每条记录）
      const indObj = topData.indicators ?? {}
      records = topData.bars.map((bar, i) => {
        const indFields = {}
        for (const [k, arr] of Object.entries(indObj)) {
          indFields[k] = Array.isArray(arr) ? arr[i] : null
        }
        return { ...bar, ...indFields }
      })
    }

    const parsedBars = records.map(normalizeBar)

    // 从扁平记录中提取各指标数组
    const ma = {}
    for (const n of maNums) {
      const arr = pickFromRecords(records, `ma${n}`, `MA${n}`, `ma_${n}`)
      if (arr) ma[`ma${n}`] = arr
    }

    return {
      bars:  parsedBars,
      hasMore: raw.has_more,
      source: raw.data_source ?? "unknown",
      ma,
      volMa: pickFromRecords(records, 'vol_ma5', 'vol_ma_5', 'VOL_MA5') ?? [],
      macd: {
        // 后端 MACD 字段名：macd / macd_signal / macd_hist
        diff: pickFromRecords(records, 'macd', 'macd_diff', 'MACD_DIFF') ?? [],
        dea:  pickFromRecords(records, 'macd_signal', 'macd_dea', 'MACD_DEA') ?? [],
        hist: pickFromRecords(records, 'macd_hist', 'MACD_HIST') ?? [],
      },
      kdj: {
        k: pickFromRecords(records, 'k', 'kdj_k', 'KDJ_K') ?? [],
        d: pickFromRecords(records, 'd', 'kdj_d', 'KDJ_D') ?? [],
        j: pickFromRecords(records, 'j', 'kdj_j', 'KDJ_J') ?? [],
      },
      rsi: pickFromRecords(records, 'rsi14', 'rsi_14', 'RSI14') ?? [],
    }
  }

  /**
   * 全量加载（初始 or 切换品种/周期时调用）。
   * @param {string}   symbol
   * @param {string}   interval
   * @param {number}   limit    初始加载条数，默认 500
   * @param {number[]} maNums   均线周期列表
   */
  async function load(symbol, interval = '1d', limit = 500, maNums = [5, 10, 20, 30]) {
    if (!symbol) return

    const epoch=++generation
    controller?.abort()
    controller=new AbortController()
    context=`${symbol}|${interval}`
    bars.value=[]; maData.value={}; macdData.value={diff:[],dea:[],hist:[]}
    kdjData.value={k:[],d:[],j:[]}; rsiData.value=[]; volMaData.value=[]
    loadingMore.value=false
    loading.value = true
    error.value   = ''
    hasMore.value = true

    try {
      const d = await _fetch(symbol, interval, limit, maNums,null,controller.signal)
      if (epoch!==generation) return false
      provenance.value=d.source

      bars.value      = d.bars
      maData.value    = d.ma
      volMaData.value = d.volMa
      macdData.value  = d.macd
      kdjData.value   = d.kdj
      rsiData.value   = d.rsi
      totalLoaded.value = d.bars.length

      // 返回条数 < 请求条数 → 没有更多历史了
      hasMore.value=d.hasMore ?? d.bars.length>=limit
      return true
    } catch (e) {
      if (epoch===generation && e.name!=='AbortError') error.value = e.message ?? '数据加载失败'
      return false
    } finally {
      if (epoch===generation) loading.value = false
    }
  }

  /**
   * 加载更多历史 K 线（向左翻页）。
   * 将旧 bar 拼接到现有数据前面，指标数据同步扩展。
   *
   * @param {string}   symbol
   * @param {string}   interval
   * @param {number}   pageSize  每次加载条数，默认 300
   * @param {number[]} maNums
   * @returns {number} 本次新增的 bar 数量
   */
  async function loadMore(symbol, interval = '1d', pageSize = 300, maNums = [5, 10, 20, 30]) {
    if (!symbol || loading.value || loadingMore.value || !hasMore.value || context!==`${symbol}|${interval}`) return 0
    const epoch=generation

    // 最早的 bar 时间作为分页游标
    const oldestBar = bars.value[0]
    if (!oldestBar) return 0

    loadingMore.value = true
    try {
      const d = await _fetch(symbol, interval, pageSize, maNums, oldestBar.time,controller.signal)
      if (epoch!==generation) return 0

      if (!d.bars.length) {
        hasMore.value = false
        return 0
      }

      // 去重：过滤掉已存在的 bar（按 time 判断）
      const existTimes = new Set(bars.value.map(b => b.time))
      const indices=d.bars.map((b,i)=>!existTimes.has(b.time) && b.time<oldestBar.time ? i : -1).filter(i=>i>=0)
      const newBars=indices.map(i=>d.bars[i])
      for (const key of Object.keys(d.ma)) d.ma[key]=indices.map(i=>d.ma[key][i])
      for (const key of ['volMa','rsi']) d[key]=indices.map(i=>d[key][i]??null)
      for (const group of ['macd','kdj']) for (const key of Object.keys(d[group])) d[group][key]=indices.map(i=>d[group][key][i]??null)

      if (!newBars.length) {
        hasMore.value = false
        return 0
      }

      // 前置拼接 bars 和所有指标数组
      bars.value = [...newBars, ...bars.value]

      // 对每条均线前置拼接
      const merged = {}
      for (const n of maNums) {
        const key = `ma${n}`
        merged[key] = [...(d.ma[key] ?? []), ...(maData.value[key] ?? [])]
      }
      maData.value    = merged
      volMaData.value = [...d.volMa, ...volMaData.value]
      macdData.value  = {
        diff: [...d.macd.diff, ...macdData.value.diff],
        dea:  [...d.macd.dea,  ...macdData.value.dea],
        hist: [...d.macd.hist, ...macdData.value.hist],
      }
      kdjData.value = {
        k: [...d.kdj.k, ...kdjData.value.k],
        d: [...d.kdj.d, ...kdjData.value.d],
        j: [...d.kdj.j, ...kdjData.value.j],
      }
      rsiData.value = [...d.rsi, ...rsiData.value]

      totalLoaded.value = bars.value.length

      hasMore.value=(d.hasMore ?? d.bars.length>=pageSize) && bars.value.length<5000

      return newBars.length
    } catch (e) {
      console.warn('[useKlineData] loadMore 失败:', e.message)
      return 0
    } finally {
      if (epoch===generation) loadingMore.value = false
    }
  }

  return {
    bars,
    provenance,
    maData,
    macdData,
    kdjData,
    rsiData,
    volMaData,
    loading,
    loadingMore,
    hasMore,
    totalLoaded,
    error,
    load,
    loadMore,
  }
}
