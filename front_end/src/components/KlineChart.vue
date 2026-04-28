<template>
  <div ref="wrapRef" class="kline-wrap" :class="{ fullscreen: isFullscreen, mobile: isMobile }">

    <KlineToolbar
      :name="name"
      :symbol="symbol"
      :current-interval="currentInterval"
      v-model:custom-n="customN"
      v-model:custom-unit="customUnit"
      :bars-count="bars.length"
      :has-more="hasMore"
      :is-mobile="isMobile"
      :ma-config="maConfig"
      v-model:new-ma-n="newMaN"
      v-model:active-indicator="activeIndicator"
      v-model:draw-mode="drawMode"
      :drawn-lines-count="drawnLines.length"
      :is-fullscreen="isFullscreen"
      :loading="loading"
      v-model:show-shortcuts-help="showShortcutsHelp"
      @set-interval="setInterval"
      @apply-custom-period="applyCustomPeriod"
      @refresh-chart="refreshChart"
      @remove-ma="removeMa"
      @add-ma="addMa"
      @clear-drawings="clearDrawings"
      @toggle-fullscreen="toggleFullscreen"
      @save-image="saveImage"
      @reload="reload"
    />

    <!-- ── 十字光标信息栏 ─────────────────────────────────────────────── -->
    <div v-if="crossInfo.visible" class="crosshair-bar">
      <span class="ci-time">{{ crossInfo.time }}</span>
      <span class="ci-item open">开 {{ crossInfo.open }}</span>
      <span class="ci-item high">高 {{ crossInfo.high }}</span>
      <span class="ci-item low">低 {{ crossInfo.low }}</span>
      <span class="ci-item close" :class="crossInfo.dir">收 {{ crossInfo.close }}</span>
      <span class="ci-item vol">量 {{ crossInfo.vol }}</span>
      <span
        v-for="ma in visibleMas"
        :key="ma.n"
        class="ci-item"
        :style="{ color: ma.color }"
      >MA{{ ma.n }} {{ crossInfo[`ma${ma.n}`] }}</span>
      <span v-if="activeIndicator === 'macd'" class="ci-item">
        DIFF {{ crossInfo.diff }}
        DEA {{ crossInfo.dea }}
        MACD {{ crossInfo.hist }}
      </span>
      <span v-if="activeIndicator === 'kdj'" class="ci-item">
        K {{ crossInfo.k }}
        D {{ crossInfo.d }}
        J {{ crossInfo.j }}
      </span>
      <span v-if="activeIndicator === 'rsi'" class="ci-item">
        RSI {{ crossInfo.rsi }}
      </span>
    </div>

    <!-- ── 快捷键帮助面板 ───────────────────────────────────────────────── -->
    <transition name="sk-fade">
      <div v-if="showShortcutsHelp" class="shortcuts-panel" @click.self="showShortcutsHelp = false">
        <div class="shortcuts-inner">
          <div class="shortcuts-header">
            <span>键盘快捷键</span>
            <button class="icon-btn-sm" @click="showShortcutsHelp = false">×</button>
          </div>
          <div class="shortcuts-list">
            <div v-for="sk in KLINE_SHORTCUTS" :key="sk.keys" class="sk-entry">
              <kbd class="sk-kbd">{{ sk.keys }}</kbd>
              <span class="sk-desc">{{ sk.desc }}</span>
            </div>
          </div>
        </div>
      </div>
    </transition>

    <!-- ── 图表容器 ────────────────────────────────────────────────────── -->
    <div class="chart-area">
      <!-- 骨架屏（首次加载时显示，取代单调的 spinner） -->
      <KlineSkeleton v-if="loading && !bars.length" message="加载 K 线数据…" class="chart-skeleton" />

      <div v-if="error && !loading" class="chart-error">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ error }}</span>
        <el-button text @click="reload">重试</el-button>
      </div>
      <div v-if="!symbol && !loading" class="chart-empty">
        <el-icon><TrendCharts /></el-icon>
        <span>请选择合约</span>
      </div>
      <div ref="chartRef" class="chart-dom" />

      <!-- 向前翻页加载更多提示 -->
      <transition name="sk-fade">
        <div v-if="loadingMore" class="load-more-tip">
          <span class="lm-spinner" />
          <span>加载更多历史…</span>
        </div>
        <div v-else-if="!hasMore && bars.length > 0" class="load-more-tip no-more">
          <span>已到最早历史</span>
        </div>
      </transition>
    </div>

  </div>
</template>

<script setup>
import {
  ref, watch, onMounted, onUnmounted, nextTick,
} from 'vue'
import * as echarts from 'echarts'
import {
  WarningFilled, TrendCharts,
} from '@element-plus/icons-vue'

import { storeToRefs } from 'pinia'
import { useKlineData } from '@/composables/useKlineData.js'
import { INTERVALS } from '@/config/kline.js'
import { useWatchStore, useChartStore, useIndicatorStore, useHistoryStore } from '@/stores/index.js'
import { useHotkeys, KLINE_SHORTCUTS } from '@/composables/useHotkeys.js'
import { useIndicatorWorker } from '@/composables/useIndicatorWorker.js'
import { useWatchWs } from '@/composables/useWatchWs.js'
import KlineSkeleton from '@/components/KlineSkeleton.vue'
import KlineToolbar from '@/components/KlineToolbar.vue'

// ── Props ────────────────────────────────────────────────────────────────
const props = defineProps({
  /** 合约代码，如 IF2506 */
  symbol: { type: String, default: '' },
  /** 合约名称（显示用） */
  name:   { type: String, default: '' },
  /** 默认周期 */
  defaultInterval: { type: String, default: '1d' },
  /** 默认条数 */
  defaultLimit:    { type: Number, default: 500 },
})

// ── Stores ────────────────────────────────────────────────────────────────
const watchStore     = useWatchStore()
const chartStore     = useChartStore()
const indicatorStore = useIndicatorStore()
const historyStore   = useHistoryStore()

// 从 stores 中拉取可写响应式 refs（别名，保持模板兼容）
const { maList: maConfig, activeIndicator, visibleMas, maNums } = storeToRefs(indicatorStore)

// ── 状态 ─────────────────────────────────────────────────────────────────
const chartRef      = ref(null)
const wrapRef       = ref(null)
let   chart         = null

// 周期：优先使用 store 持久化值，回退到 prop 默认值
const currentInterval = ref(
  watchStore.currentInterval || props.defaultInterval
)
const customN    = ref(1)
const customUnit = ref('d')

const newMaN = ref(null)

// 画图相关
const drawMode    = ref('pointer')
const drawnLines  = ref([])
let   drawingLine = null  // 正在绘制的临时线

// 十字光标信息
const crossInfo = ref({ visible: false })

// 全屏 / 移动端
const isFullscreen = ref(false)
const isMobile     = ref(window.innerWidth < 768)

// 数据
const {
  bars, maData, macdData, kdjData, rsiData, volMaData,
  loading, loadingMore, hasMore, error, load, loadMore: loadMoreData,
} = useKlineData()

const indicatorWorker = useIndicatorWorker()
const watchWs = useWatchWs()

// 快捷键帮助面板
const showShortcutsHelp = ref(false)

// ── 数据加载 ──────────────────────────────────────────────────────────────
async function reload() {
  if (!props.symbol) return

  // 尝试命中内存缓存
  const cached = watchStore.getCacheEntry(props.symbol, currentInterval.value)
  if (cached) {
    bars.value    = cached.bars    ?? []
    Object.assign(maData.value,   cached.maData   ?? {})
    Object.assign(macdData.value, cached.macdData ?? {})
    Object.assign(kdjData.value,  cached.kdjData  ?? {})
    rsiData.value    = cached.rsiData    ?? []
    volMaData.value  = cached.volMaData  ?? []
    await nextTick()
    buildChart()
    return
  }

  // 先获取基础 K 线（后端也返回指标，两路可并行）
  await load(props.symbol, currentInterval.value, props.defaultLimit, maNums.value)

  // 尝试用 Web Worker 在后台重新计算指标（更快、不阻塞主线程）
  // 若 Worker 返回结果则覆盖后端指标（保证参数一致性）
  _applyWorkerIndicators()

  // 写入缓存
  watchStore.setCacheEntry(props.symbol, currentInterval.value, {
    bars:     bars.value,
    maData:   { ...maData.value },
    macdData: { ...macdData.value },
    kdjData:  { ...kdjData.value },
    rsiData:  [...rsiData.value],
    volMaData:[...volMaData.value],
  })

  await nextTick()
  buildChart()
}

/** 使用 Web Worker 在后台重算指标（不阻塞渲染，完成后静默更新） */
async function _applyWorkerIndicators() {
  if (!bars.value.length) return
  const { macdParams, kdjParams, rsiParams } = indicatorStore
  const result = await indicatorWorker.calcAll(
    props.symbol,
    currentInterval.value,
    bars.value,
    {
      maParams:   { periods: maNums.value, volPeriod: 5 },
      macdParams: { fast: macdParams.fast, slow: macdParams.slow, signal: macdParams.signal },
      kdjParams:  { n: kdjParams.n, m1: kdjParams.m1, m2: kdjParams.m2 },
      rsiParams:  { period: rsiParams.period },
    }
  )
  if (!result) return
  // 覆盖响应式数据（仅当品种/周期未切换时）
  const snap = { sym: props.symbol, iv: currentInterval.value }
  await nextTick()
  if (props.symbol === snap.sym && currentInterval.value === snap.iv) {
    Object.assign(maData.value, result.ma ?? {})
    if (result.volMa?.length) volMaData.value = result.volMa
    if (result.macd?.diff?.length) Object.assign(macdData.value, result.macd)
    if (result.kdj?.k?.length)    Object.assign(kdjData.value, result.kdj)
    if (result.rsi?.length)       rsiData.value = result.rsi
    // 静默刷新图表（不闪烁）
    if (chart) chart.setOption(buildOption(), { notMerge: false, silent: true })
  }
}

// ── 向前翻页加载更多历史 K 线 ────────────────────────────────────────────
let _loadMoreDebounceTimer = null

async function triggerLoadMore() {
  if (loadingMore.value || !hasMore.value || !props.symbol) return
  clearTimeout(_loadMoreDebounceTimer)
  _loadMoreDebounceTimer = setTimeout(async () => {
    // 记录当前 dataZoom 状态，加载完成后恢复视图位置
    const dzOption = chart?.getOption()?.dataZoom?.[0]
    const startVal = dzOption?.startValue
    const endVal   = dzOption?.endValue

    const added = await loadMoreData(props.symbol, currentInterval.value, 300, maNums.value)
    if (added > 0) {
      await nextTick()
      // 恢复视图：向右偏移 added 个 bar（保持用户当前视图不跳动）
      if (chart && startVal != null) {
        chart.setOption({
          dataZoom: [
            { startValue: startVal + added, endValue: endVal + added },
          ],
        }, { notMerge: false, silent: true })
      } else {
        buildChart()
      }
    }
  }, 300)
}

function setInterval(iv) {
  currentInterval.value = iv
  watchStore.setInterval(iv)
  reload()
}

function applyCustomPeriod() {
  const iv = `${customN.value}${customUnit.value}`
  currentInterval.value = iv
  watchStore.setInterval(iv)
  reload()
}

// ── 均线管理 ──────────────────────────────────────────────────────────────
function addMa() {
  const added = indicatorStore.addMa(newMaN.value)
  if (!added) return
  newMaN.value = null
  // 新周期需要重新请求数据
  watchStore.clearCache(props.symbol)
  reload()
}

function removeMa(idx) {
  const n = maConfig.value[idx]?.n
  if (n) indicatorStore.removeMa(n)
  refreshChart()
}

// ── ECharts 构建 ──────────────────────────────────────────────────────────

/** 仅更新 series/option，不重建实例 */
function refreshChart() {
  if (!chart || !bars.value.length) return
  chart.setOption(buildOption(), { replaceMerge: ['series', 'yAxis'] })
  applyDrawings()
}

/** 完整构建（首次或数据变化后） */
function buildChart() {
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value, 'dark', { renderer: 'canvas' })
    bindChartEvents()
  }
  if (!bars.value.length) return
  chart.setOption(buildOption(), true)
  applyDrawings()
}

function buildOption() {
  const cfg    = chartStore.resolvedConfig(props.symbol)
  const times  = bars.value.map(b => b.time)
  const candleData = bars.value.map(b => [b.open, b.close, b.low, b.high])
  const volData    = bars.value.map((b) => {
    const up = b.close >= b.open
    // 成交量柱用半透明色，避免视觉抢占主图
    const color = up ? (cfg.upColor + 'aa') : (cfg.downColor + 'aa')
    return { value: b.volume, itemStyle: { color } }
  })

  // ── 布局参数 ─────────────────────────────────────────────────────────────
  // ECharts grid 不支持 CSS calc()，所以全部用纯百分比/像素定位。
  // 垂直分区（从上到下）：
  //   [主图 K线]  →  [成交量副图]  →  [技术指标副图]  →  [DataZoom 滑块]
  //
  //  Desktop: 主图 ~58% / 成交量 ~14% / 指标 ~14% / DataZoom ~8% / gaps 2% each
  //  Mobile : 主图 ~52% / 成交量 ~14% / 指标 ~14% / DataZoom ~9% / gaps 2% each
  //
  // 坐标系说明：
  //   top    = 距容器顶部距离（像素或百分比）
  //   bottom = 距容器底部距离（像素或百分比）
  //   各区域 bottom edge = 100% - top - height

  const isMob = isMobile.value

  // grid[0] 主图：top=8px，bottom=40%（桌面）/ 46%（移动端）
  // grid[1] 成交量：top=62%（桌面）/ 56%（移动端），bottom=24% / 29%
  // grid[2] 指标：top=78%（桌面）/ 73%（移动端），bottom=8% / 9%
  const g0Top    = 8
  const g0Bottom = isMob ? '46%' : '40%'
  const g1Top    = isMob ? '56%' : '62%'
  const g1Bottom = isMob ? '29%' : '24%'
  const g2Top    = isMob ? '73%' : '78%'
  const g2Bottom = isMob ?  '9%' :  '8%'

  // 均线系列
  const maSeries = maConfig.value
    .filter(m => m.visible && maData.value[`ma${m.n}`]?.length)

    .map(m => ({
      name:   `MA${m.n}`,
      type:   'line',
      data:   maData.value[`ma${m.n}`],
      smooth: false,
      symbol: 'none',
      xAxisIndex: 0, yAxisIndex: 0,
      lineStyle: {
        color: m.color,
        width: 1.2,
        type:  m.dashType === 'solid' ? 'solid' : m.dashType === 'dashed' ? 'dashed' : 'dotted',
      },
      z: 2,
    }))

  // 成交量均线
  const volMaSeries = volMaData.value.length ? [{
    name: 'VolMA5',
    type: 'line',
    data: volMaData.value,
    symbol: 'none',
    xAxisIndex: 1, yAxisIndex: 2,
    lineStyle: { color: '#f59e0b', width: 1.2 },
    z: 2,
  }] : []

  // 指标系列
  const indSeries = buildIndicatorSeries()

  // Tooltip formatter
  const allMaNames = maConfig.value.filter(m => m.visible).map(m => `MA${m.n}`)
  // suppress unused var warning — used indirectly via formatTooltip
  void allMaNames

  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      show: true,
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        link: [{ xAxisIndex: 'all' }],
        lineStyle: { color: '#555', width: 1, type: 'dashed' },
        crossStyle: { color: '#555' },
        label: { backgroundColor: '#333' },
      },
      backgroundColor: 'rgba(20,20,30,0.92)',
      borderColor: '#444',
      borderWidth: 1,
      textStyle: { color: '#ccc', fontSize: 12 },
      formatter: (params) => formatTooltip(params, times),
      position: (pos, params, dom, rect, size) => {
        const x = pos[0] < size.viewSize[0] / 2 ? pos[0] + 14 : pos[0] - dom.offsetWidth - 14
        return [x, 10]
      },
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
    },
    grid: [
      // 主图 K 线区
      { left: 54, right: 64, top: g0Top,    bottom: g0Bottom, containLabel: false },
      // 成交量副图
      { left: 54, right: 64, top: g1Top,    bottom: g1Bottom, containLabel: false },
      // 技术指标副图
      { left: 54, right: 64, top: g2Top,    bottom: g2Bottom, containLabel: false },
    ],
    xAxis: [0, 1, 2].map(i => ({
      type:       'category',
      data:       times,
      gridIndex:  i,
      axisLine:   { lineStyle: { color: '#333' } },
      axisTick:   { show: false },
      axisLabel:  {
        show:  i === 2,
        color: '#888',
        fontSize: 11,
        formatter: (val) => {
          if (!val) return ''
          const d = new Date(val.replace(' ', 'T'))
          if (isNaN(d)) return val
          const iv = currentInterval.value
          if (iv === '1d' || iv === '1w') {
            return `${d.getMonth() + 1}/${d.getDate()}`
          }
          return `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`
        },
      },
      splitLine:  { show: false },
      boundaryGap: true,
    })),
    yAxis: [
      // ── 主图价格轴（右侧显示） ─────────────────────────────────────────────
      {
        scale:       true,
        gridIndex:   0,
        position:    'right',
        axisLine:    { show: false },
        axisTick:    { show: false },
        axisLabel:   {
          color:    '#999',
          fontSize:  11,
          margin:    6,
          formatter: (v) => v.toFixed(v >= 1000 ? 0 : v >= 100 ? 1 : 2),
        },
        splitLine:   { lineStyle: { color: '#1e1e30', type: 'solid' } },
        splitNumber: 5,
        min: 'dataMin',
        max: 'dataMax',
      },
      // ── 主图左侧隐藏轴（让左右 left/right 对称，否则 left 侧有空白）──────────
      {
        scale:     true,
        gridIndex: 0,
        position:  'left',
        axisLine:  { show: false },
        axisTick:  { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
        min: 'dataMin',
        max: 'dataMax',
      },
      // ── 成交量轴（右侧，标签简化为 万/亿） ─────────────────────────────────
      {
        scale:      false,    // 从 0 开始
        gridIndex:  1,
        position:   'right',
        axisLine:   { show: false },
        axisTick:   { show: false },
        axisLabel:  {
          color:     '#666',
          fontSize:  10,
          margin:    6,
          formatter: (v) => {
            if (v === 0) return ''
            if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿'
            if (v >= 1e4) return (v / 1e4).toFixed(0) + '万'
            return String(v)
          },
        },
        splitLine:   { lineStyle: { color: '#1a1a28', type: 'solid' } },
        splitNumber: 2,
        min: 0,
      },
      // ── 技术指标轴 ─────────────────────────────────────────────────────────
      {
        scale:      true,
        gridIndex:  2,
        position:   'right',
        axisLine:   { show: false },
        axisTick:   { show: false },
        axisLabel:  {
          color:    '#666',
          fontSize:  10,
          margin:    6,
          formatter: (v) => v.toFixed(2),
        },
        splitLine:   { lineStyle: { color: '#1a1a28', type: 'solid' } },
        splitNumber: 3,
      },
    ],
    dataZoom: [
      {
        type:             'inside',
        xAxisIndex:       [0, 1, 2],
        start:            Math.max(0, 100 - Math.round(150 / bars.value.length * 100)),
        end:              100,
        filterMode:       'filter',
        zoomOnMouseWheel: true,
        moveOnMouseMove:  true,
      },
      {
        type:       'slider',
        xAxisIndex: [0, 1, 2],
        // 滑块位于指标区下方，用 bottom=2 贴底，不覆盖任何 grid
        bottom:     2,
        height:     22,
        left:       54,
        right:      64,
        start:      Math.max(0, 100 - Math.round(150 / bars.value.length * 100)),
        end:        100,
        handleIcon: 'path://M10.7,11.9v-1.3H9.3v1.3c-4.9,0.3-8.8,4.4-8.8,9.4c0,5,3.9,9.1,8.8,9.4v1.3h1.3v-1.3c4.9-0.3,8.8-4.4,8.8-9.4C19.5,16.3,15.6,12.2,10.7,11.9z M13.3,24.4H6.7V23h6.6V24.4z M13.3,19.6H6.7v-1.4h6.6V19.6z',
        handleSize:      '80%',
        handleStyle:     { color: '#444' },
        textStyle:       { color: '#555', fontSize: 10 },
        borderColor:     '#2a2a3a',
        fillerColor:     'rgba(99,102,241,0.12)',
        backgroundColor: '#13131f',
        showDetail:      false,
      },
    ],
    series: [
      // K 线
      {
        name: 'K线',
        type: 'candlestick',
        data: candleData,
        xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: {
          color:        cfg.upColor,
          color0:       cfg.downColor,
          borderColor:  cfg.upColor,
          borderColor0: cfg.downColor,
        },
        z: 3,
      },
      // 成交量
      {
        name:       '成交量',
        type:       'bar',
        data:       volData,
        xAxisIndex: 1, yAxisIndex: 2,
        barMaxWidth: 6,
        barMinWidth: 1,
        barCategoryGap: '20%',
        z: 2,
      },
      ...maSeries,
      ...volMaSeries,
      ...indSeries,
    ],
  }
}

function buildIndicatorSeries() {
  switch (activeIndicator.value) {
    case 'macd':
      return buildMacdSeries()
    case 'kdj':
      return buildKdjSeries()
    case 'rsi':
      return buildRsiSeries()
    default:
      return []
  }
}

function buildMacdSeries() {
  const { diff, dea, hist } = macdData.value
  if (!diff?.length) return []
  const histData = hist.map(v => ({
    value: v,
    itemStyle: { color: v >= 0 ? '#ef4444' : '#22c55e' },
  }))
  return [
    {
      name: 'DIFF', type: 'line', data: diff,
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none', lineStyle: { color: '#f5c842', width: 1.2 },
    },
    {
      name: 'DEA', type: 'line', data: dea,
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none', lineStyle: { color: '#3b82f6', width: 1.2 },
    },
    {
      name: 'MACD', type: 'bar', data: histData,
      xAxisIndex: 2, yAxisIndex: 3, barMaxWidth: 6,
    },
  ]
}

function buildKdjSeries() {
  const { k, d, j } = kdjData.value
  if (!k?.length) return []
  return [
    {
      name: 'K', type: 'line', data: k,
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none', lineStyle: { color: '#f5c842', width: 1.2 },
    },
    {
      name: 'D', type: 'line', data: d,
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none', lineStyle: { color: '#3b82f6', width: 1.2 },
    },
    {
      name: 'J', type: 'line', data: j,
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none', lineStyle: { color: '#ec4899', width: 1.2 },
    },
  ]
}

function buildRsiSeries() {
  if (!rsiData.value?.length) return []
  const times = bars.value.map(b => b.time)
  const { overbought, oversold, period } = indicatorStore.rsiParams
  return [
    {
      name: `RSI${period}`, type: 'line', data: rsiData.value,
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none', lineStyle: { color: '#8b5cf6', width: 1.5 },
    },
    {
      name: `RSI${overbought}`, type: 'line',
      data: times.map(() => overbought),
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none',
      lineStyle: { color: '#ef4444', width: 1, type: 'dashed' },
      tooltip: { show: false },
    },
    {
      name: `RSI${oversold}`, type: 'line',
      data: times.map(() => oversold),
      xAxisIndex: 2, yAxisIndex: 3,
      symbol: 'none',
      lineStyle: { color: '#22c55e', width: 1, type: 'dashed' },
      tooltip: { show: false },
    },
  ]
}

// ── Tooltip formatter ─────────────────────────────────────────────────────
function formatTooltip(params, times) {
  if (!params?.length) return ''
  const idx = params[0]?.dataIndex ?? 0
  const bar = bars.value[idx]
  if (!bar) return ''

  // 更新十字光标信息栏
  const info = {
    visible: true,
    time:    bar.time,
    open:    bar.open?.toFixed(2) ?? '-',
    high:    bar.high?.toFixed(2) ?? '-',
    low:     bar.low?.toFixed(2)  ?? '-',
    close:   bar.close?.toFixed(2) ?? '-',
    vol:     fmtVol(bar.volume),
    dir:     bar.close >= bar.open ? 'up' : 'down',
  }
  for (const ma of indicatorStore.maList) {
    const arr = maData.value[`ma${ma.n}`]
    info[`ma${ma.n}`] = arr?.[idx]?.toFixed(2) ?? '-'
  }
  // 指标
  if (activeIndicator.value === 'macd') {
    info.diff = macdData.value.diff?.[idx]?.toFixed(4) ?? '-'
    info.dea  = macdData.value.dea?.[idx]?.toFixed(4) ?? '-'
    info.hist = macdData.value.hist?.[idx]?.toFixed(4) ?? '-'
  } else if (activeIndicator.value === 'kdj') {
    info.k = kdjData.value.k?.[idx]?.toFixed(2) ?? '-'
    info.d = kdjData.value.d?.[idx]?.toFixed(2) ?? '-'
    info.j = kdjData.value.j?.[idx]?.toFixed(2) ?? '-'
  } else if (activeIndicator.value === 'rsi') {
    info.rsi = rsiData.value?.[idx]?.toFixed(2) ?? '-'
  }
  crossInfo.value = info

  // 不在此返回 HTML（使用上方信息栏替代浮动 tooltip）
  return ''
}

function fmtVol(v) {
  if (!v) return '-'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return String(v)
}

// ── 画图工具 ──────────────────────────────────────────────────────────────

function bindChartEvents() {
  if (!chart) return

  chart.getZr().on('click', onCanvasClick)
  chart.getZr().on('mousemove', onCanvasMousemove)
  chart.getZr().on('dblclick', onCanvasDblclick)

  // 向左拖到边缘时自动加载更多历史数据
  chart.on('datazoom', (params) => {
    const opt   = chart.getOption()?.dataZoom?.[0]
    const start = opt?.startValue ?? 0
    if (start <= 5) {
      triggerLoadMore()
    }
  })

  // 十字光标离开时隐藏信息栏
  chart.getZr().on('mouseout', () => {
    crossInfo.value = { visible: false }
  })
}

function pixelToData(x, y) {
  if (!chart || !bars.value.length) return null
  try {
    const pt = chart.convertFromPixel({ seriesIndex: 0 }, [x, y])
    if (!pt) return null
    const xIdx  = Math.round(pt[0])
    const price = pt[1]
    const time  = bars.value[Math.max(0, Math.min(xIdx, bars.value.length - 1))]?.time
    return { xIdx, price, time }
  } catch {
    return null
  }
}

function onCanvasClick(e) {
  if (drawMode.value === 'pointer') return

  const dp = pixelToData(e.offsetX, e.offsetY)
  if (!dp) return

  if (drawMode.value === 'hLine') {
    drawnLines.value.push({ type: 'hLine', price: dp.price })
    applyDrawings()
    return
  }

  if (drawMode.value === 'vLine') {
    drawnLines.value.push({ type: 'vLine', xIdx: dp.xIdx, time: dp.time })
    applyDrawings()
    return
  }

  if (drawMode.value === 'trend') {
    if (!drawingLine) {
      // 起点
      drawingLine = { type: 'trend', x1: dp.xIdx, y1: dp.price }
    } else {
      // 终点
      drawnLines.value.push({ ...drawingLine, x2: dp.xIdx, y2: dp.price })
      drawingLine = null
      applyDrawings()
    }
  }
}

function onCanvasMousemove(e) {
  if (drawMode.value === 'trend' && drawingLine) {
    const dp = pixelToData(e.offsetX, e.offsetY)
    if (!dp) return
    applyDrawings(dp)
  }
}

function onCanvasDblclick(e) {
  if (drawMode.value === 'pointer') {
    // 双击重置视图
    chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100, dataZoomIndex: 0 })
    chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100, dataZoomIndex: 1 })
    return
  }
  // 双击删除最近的水平线/垂直线
  const dp = pixelToData(e.offsetX, e.offsetY)
  if (!dp) return
  const THRESHOLD = 5
  const idx = drawnLines.value.findIndex(l => {
    if (l.type === 'hLine') {
      const py = chart.convertToPixel({ seriesIndex: 0 }, [0, l.price])?.[1]
      return Math.abs(py - e.offsetY) < THRESHOLD
    }
    if (l.type === 'vLine') {
      const px = chart.convertToPixel({ seriesIndex: 0 }, [l.xIdx, 0])?.[0]
      return Math.abs(px - e.offsetX) < THRESHOLD
    }
    return false
  })
  if (idx >= 0) {
    drawnLines.value.splice(idx, 1)
    applyDrawings()
  }
}

function applyDrawings(previewPoint = null) {
  if (!chart || !bars.value.length) return

  const graphics = []

  for (const line of drawnLines.value) {
    if (line.type === 'hLine') {
      const y = chart.convertToPixel({ seriesIndex: 0 }, [0, line.price])?.[1]
      if (y == null) continue
      const w = chartRef.value?.offsetWidth ?? 800
      graphics.push({
        type: 'line',
        shape: { x1: 60, y1: y, x2: w - 60, y2: y },
        style: { stroke: '#f5c842', lineWidth: 1, lineDash: [4, 4] },
        z: 10,
      })
      graphics.push({
        type: 'text',
        x: w - 55,
        y: y - 8,
        style: { text: line.price?.toFixed(2), fill: '#f5c842', fontSize: 11 },
        z: 11,
      })
    } else if (line.type === 'vLine') {
      const x = chart.convertToPixel({ seriesIndex: 0 }, [line.xIdx, 0])?.[0]
      if (x == null) continue
      const h = chartRef.value?.offsetHeight ?? 600
      graphics.push({
        type: 'line',
        shape: { x1: x, y1: 8, x2: x, y2: h - 50 },
        style: { stroke: '#3b82f6', lineWidth: 1, lineDash: [4, 4] },
        z: 10,
      })
    } else if (line.type === 'trend') {
      const p1 = chart.convertToPixel({ seriesIndex: 0 }, [line.x1, line.y1])
      const p2 = chart.convertToPixel({ seriesIndex: 0 }, [line.x2, line.y2])
      if (!p1 || !p2) continue
      graphics.push({
        type: 'line',
        shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
        style: { stroke: '#ec4899', lineWidth: 1.5 },
        z: 10,
      })
    }
  }

  // 预览线（趋势线绘制中）
  if (previewPoint && drawingLine) {
    const p1 = chart.convertToPixel({ seriesIndex: 0 }, [drawingLine.x1, drawingLine.y1])
    const p2 = chart.convertToPixel({ seriesIndex: 0 }, [previewPoint.xIdx, previewPoint.price])
    if (p1 && p2) {
      graphics.push({
        type: 'line',
        shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
        style: { stroke: '#ec4899', lineWidth: 1.5, lineDash: [4, 2], opacity: 0.7 },
        z: 10,
      })
    }
  }

  chart.setOption({ graphic: { elements: graphics } })
}

function clearDrawings() {
  drawnLines.value = []
  drawingLine = null
  chart?.setOption({ graphic: { elements: [] } })
}

// ── 全屏 ─────────────────────────────────────────────────────────────────
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    wrapRef.value?.requestFullscreen().then(() => {
      isFullscreen.value = true
      nextTick(() => chart?.resize())
    })
  } else {
    document.exitFullscreen().then(() => {
      isFullscreen.value = false
      nextTick(() => chart?.resize())
    })
  }
}

document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement) isFullscreen.value = false
})

// ── 保存图片 ──────────────────────────────────────────────────────────────
function saveImage() {
  if (!chart) return
  const url = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#0f0f1a' })
  const a = document.createElement('a')
  a.href = url
  a.download = `kline_${props.symbol}_${currentInterval.value}_${Date.now()}.png`
  a.click()
}

// ── ResizeObserver ────────────────────────────────────────────────────────
let resizeObserver = null

function setupResizeObserver() {
  if (!wrapRef.value) return
  resizeObserver = new ResizeObserver(() => {
    isMobile.value = (wrapRef.value?.offsetWidth ?? window.innerWidth) < 768
    chart?.resize()
  })
  resizeObserver.observe(wrapRef.value)
}

// ── 生命周期 ──────────────────────────────────────────────────────────────
onMounted(async () => {
  setupResizeObserver()
  if (props.symbol) {
    await reload()
    historyStore.addVisit({ symbol: props.symbol, name: props.name }, currentInterval.value)
  }
})

onUnmounted(() => {
  clearTimeout(_loadMoreDebounceTimer)
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})

watch(() => props.symbol, async (val, oldVal) => {
  if (val) {
    // 切换品种时恢复该品种上次使用的周期
    const lastIv = historyStore.getLastInterval(val, props.defaultInterval)
    if (lastIv !== currentInterval.value) {
      currentInterval.value = lastIv
    }
    await reload()
    historyStore.addVisit({ symbol: val, name: props.name }, currentInterval.value)
  } else {
    bars.value = []
    chart?.clear()
  }
})

// 实时K线更新：监听WebSocket推送的kline_update，更新最后一根K线
watch(() => watchWs.getCurrentBar(props.symbol, currentInterval.value), (newBar) => {
  if (!newBar || !bars.value.length || !chart) return
  const lastBar = bars.value[bars.value.length - 1]
  if (lastBar.time !== newBar.time && newBar.time) {
    bars.value.push({
      time: newBar.time,
      open: newBar.open,
      high: newBar.high,
      low: newBar.low,
      close: newBar.close,
      volume: newBar.volume,
    })
  } else {
    lastBar.open = newBar.open || lastBar.open
    lastBar.high = Math.max(lastBar.high, newBar.high || lastBar.high)
    lastBar.low = Math.min(lastBar.low, newBar.low || lastBar.low)
    lastBar.close = newBar.close || lastBar.close
    lastBar.volume = newBar.volume || lastBar.volume
  }
  chart.setOption({
    series: [
      { data: bars.value.map(b => [b.open, b.close, b.low, b.high]) },
      { data: bars.value.map((b) => {
        const up = b.close >= b.open
        const cfg = chartStore.resolvedConfig(props.symbol)
        return { value: b.volume, itemStyle: { color: up ? (cfg.upColor + 'aa') : (cfg.downColor + 'aa') } }
      })},
    ]
  }, { notMerge: false, silent: true })
}, { deep: true })

// ── 快捷键（仅在图表区域存在时生效） ──────────────────────────────────────
useHotkeys([
  // ← → 切换周期
  {
    key: 'ArrowLeft',
    handler() {
      const idx = INTERVALS.findIndex(iv => iv.value === currentInterval.value)
      if (idx > 0) setInterval(INTERVALS[idx - 1].value)
    },
  },
  {
    key: 'ArrowRight',
    handler() {
      const idx = INTERVALS.findIndex(iv => iv.value === currentInterval.value)
      if (idx < INTERVALS.length - 1) setInterval(INTERVALS[idx + 1].value)
    },
  },
  // 数字键 1~8 直接选周期
  ...INTERVALS.map((iv, i) => ({
    key: String(i + 1),
    handler: () => setInterval(iv.value),
  })),
  // R — 刷新
  { key: ['r', 'R'], handler: () => reload() },
  // F — 全屏
  { key: ['f', 'F'], handler: () => toggleFullscreen() },
  // + / = — 放大（缩小时间范围）
  {
    key: ['+', '='],
    handler() {
      const opt = chart?.getOption()?.dataZoom?.[0]
      if (!opt) return
      const range = (opt.endValue - opt.startValue) * 0.3
      chart.setOption({ dataZoom: [{ startValue: opt.startValue + range, endValue: opt.endValue }] })
    },
  },
  // - — 缩小
  {
    key: '-',
    handler() {
      const opt = chart?.getOption()?.dataZoom?.[0]
      if (!opt) return
      const range = (opt.endValue - opt.startValue) * 0.3
      const newStart = Math.max(0, opt.startValue - range)
      chart.setOption({ dataZoom: [{ startValue: newStart, endValue: opt.endValue }] })
    },
  },
  // Home — 回到最新
  {
    key: 'Home',
    handler() {
      if (!chart || !bars.value.length) return
      const total = bars.value.length
      const show  = Math.min(150, total)
      chart.setOption({ dataZoom: [{ startValue: total - show, endValue: total - 1 }] })
    },
  },
  // ? — 快捷键帮助
  { key: '?', handler: () => { showShortcutsHelp.value = !showShortcutsHelp.value } },
])
</script>

<style scoped>
/* ── 容器 ───────────────────────────────────────────────────────────────── */
.kline-wrap {
  display: flex;
  flex-direction: column;
  background: #0f0f1a;
  border: 1px solid #222;
  border-radius: 6px;
  overflow: hidden;
  height: 100%;
  min-height: 500px;
  color: #ccc;
  font-size: 13px;
}

.kline-wrap.fullscreen {
  border-radius: 0;
  border: none;
}

/* ── 工具栏 ─────────────────────────────────────────────────────────────── */
.kline-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: #13131f;
  border-bottom: 1px solid #222;
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 6px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.contract-tag {
  font-size: 14px;
  font-weight: 600;
  color: #e2e2e2;
  margin-right: 2px;
}

.symbol-tag {
  font-size: 11px;
  color: #888;
  background: #1e1e2e;
  border-radius: 3px;
  padding: 1px 5px;
}

.period-btns {
  display: flex;
  gap: 2px;
}

.period-btn {
  padding: 2px 8px;
  background: transparent;
  border: 1px solid #333;
  border-radius: 3px;
  color: #999;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.period-btn:hover {
  border-color: #555;
  color: #ddd;
}

.period-btn.active {
  border-color: #3b82f6;
  color: #3b82f6;
  background: rgba(59,130,246,0.1);
}

.custom-period {
  display: flex;
  gap: 4px;
  align-items: center;
}

.bar-count {
  font-size: 11px;
  color: #555;
  margin-left: 4px;
}

.toolbar-divider {
  width: 1px;
  height: 18px;
  background: #333;
  margin: 0 4px;
}

.tool-btn {
  display: flex;
  align-items: center;
  gap: 3px;
}

.icon-btn {
  padding: 4px 8px;
  background: transparent;
  border: 1px solid #333;
  border-radius: 3px;
  color: #888;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.15s;
}

.icon-btn:hover {
  border-color: #555;
  color: #ddd;
}

/* ── 画图工具 ────────────────────────────────────────────────────────────── */
.draw-tools {
  display: flex;
  gap: 2px;
  border: 1px solid #333;
  border-radius: 4px;
  padding: 1px;
}

.draw-btn {
  padding: 3px 7px;
  background: transparent;
  border: none;
  border-radius: 3px;
  color: #888;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s;
  display: flex;
  align-items: center;
}

.draw-btn:hover {
  background: #1e1e2e;
  color: #ddd;
}

.draw-btn.active {
  background: rgba(59,130,246,0.15);
  color: #3b82f6;
}

/* ── 旋转动画 ────────────────────────────────────────────────────────────── */
@keyframes spin {
  to { transform: rotate(360deg); }
}

.spinning {
  animation: spin 1s linear infinite;
}

/* ── 十字光标信息栏 ──────────────────────────────────────────────────────── */
.crosshair-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 3px 12px;
  background: #0d0d1a;
  border-bottom: 1px solid #1a1a2a;
  font-size: 12px;
  flex-shrink: 0;
  min-height: 24px;
}

.ci-time {
  color: #666;
  margin-right: 4px;
}

.ci-item {
  color: #aaa;
}

.ci-item.open  { color: #ccc; }
.ci-item.high  { color: #ef4444; }
.ci-item.low   { color: #22c55e; }
.ci-item.close.up   { color: #ef4444; }
.ci-item.close.down { color: #22c55e; }
.ci-item.vol   { color: #888; }

/* ── 图表区域 ────────────────────────────────────────────────────────────── */
.chart-area {
  flex: 1;
  position: relative;
  min-height: 0;
}

.chart-dom {
  width: 100%;
  height: 100%;
}

.chart-skeleton {
  position: absolute;
  inset: 0;
  z-index: 5;
  border: none;
  border-radius: 0;
}

.chart-loading,
.chart-error,
.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #555;
  font-size: 14px;
  pointer-events: none;
  z-index: 5;
}

.chart-error {
  pointer-events: auto;
  color: #ef4444;
}

/* ── 分页加载更多提示 ────────────────────────────────────────────────────── */
.load-more-tip {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(15, 15, 26, 0.88);
  border: 1px solid #2a2a4a;
  border-radius: 14px;
  padding: 4px 12px;
  font-size: 11px;
  color: #888;
  pointer-events: none;
  z-index: 10;
  white-space: nowrap;
}

.load-more-tip.no-more {
  color: #555;
  border-color: #1e1e30;
}

.lm-spinner {
  width: 10px;
  height: 10px;
  border: 1.5px solid #333;
  border-top-color: #6366f1;
  border-radius: 50%;
  animation: lm-spin 0.7s linear infinite;
  flex-shrink: 0;
}

@keyframes lm-spin { to { transform: rotate(360deg) } }

.sk-fade-enter-active,
.sk-fade-leave-active { transition: opacity 0.25s }
.sk-fade-enter-from,
.sk-fade-leave-to     { opacity: 0 }

/* ── 工具栏右侧已加载条数 ──────────────────────────────────────────────── */
.bar-count-right {
  font-size: 11px;
  color: #555;
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.has-more-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #6366f1;
  opacity: 0.7;
}

/* ── 快捷键帮助面板 ──────────────────────────────────────────────────────── */
.shortcuts-panel {
  position: absolute;
  inset: 0;
  z-index: 100;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  padding: 48px 16px 0;
}

.shortcuts-inner {
  background: #161622;
  border: 1px solid #2e2e4a;
  border-radius: 8px;
  min-width: 280px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}

.shortcuts-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px 8px;
  border-bottom: 1px solid #222;
  font-size: 13px;
  color: #ccc;
  font-weight: 600;
}

.icon-btn-sm {
  background: none;
  border: none;
  color: #666;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 0 2px;
}
.icon-btn-sm:hover { color: #aaa }

.shortcuts-list {
  padding: 8px 0;
  max-height: 380px;
  overflow-y: auto;
}

.sk-entry {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 14px;
}

.sk-kbd {
  display: inline-block;
  background: #1e1e30;
  border: 1px solid #333;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 11px;
  font-family: monospace;
  color: #a0a0c0;
  white-space: nowrap;
  min-width: 68px;
  text-align: center;
}

.sk-desc {
  font-size: 12px;
  color: #777;
  flex: 1;
}

/* ── 均线面板 ────────────────────────────────────────────────────────────── */
.ma-panel {
  padding: 4px 0;
}

.ma-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.ma-label {
  font-size: 12px;
  color: #ccc;
  min-width: 40px;
}

.ma-add-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid #333;
  margin-top: 4px;
}

/* ── 移动端适配 ──────────────────────────────────────────────────────────── */
.kline-wrap.mobile .kline-toolbar {
  padding: 4px 8px;
}

.kline-wrap.mobile .period-btns {
  gap: 1px;
}

.kline-wrap.mobile .period-btn {
  padding: 2px 5px;
  font-size: 11px;
}

.kline-wrap.mobile .crosshair-bar {
  font-size: 11px;
  gap: 6px;
  padding: 2px 8px;
}

/* ── 全局 popover 样式（scoped 无法穿透，用 :global） */
:global(.ma-popover) {
  background: #1a1a2a !important;
  border-color: #333 !important;
  color: #ccc !important;
}
</style>
