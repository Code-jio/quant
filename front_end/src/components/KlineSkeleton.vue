<template>
  <div class="kline-skeleton">

    <!-- 工具栏骨架 -->
    <div class="sk-toolbar">
      <div class="sk-row">
        <div class="sk-block sk-tag" />
        <div class="sk-block sk-tag w80" />
        <div class="sk-period-group">
          <div v-for="i in 8" :key="i" class="sk-block sk-period" />
        </div>
      </div>
      <div class="sk-row">
        <div class="sk-block sk-btn" />
        <div class="sk-block sk-btn w90" />
        <div class="sk-block sk-btn" />
        <div class="sk-block sk-btn" />
      </div>
    </div>

    <!-- 十字光标数据行骨架 -->
    <div class="sk-crosshair">
      <div v-for="i in 6" :key="i" class="sk-block sk-crosshair-item" />
    </div>

    <!-- 主图骨架（K线区域） -->
    <div class="sk-main">
      <div class="sk-y-axis">
        <div v-for="i in 6" :key="i" class="sk-block sk-y-label" />
      </div>
      <div class="sk-chart-body">
        <!-- 模拟 K 线蜡烛 -->
        <div class="sk-candles">
          <div
            v-for="i in candleCount"
            :key="i"
            class="sk-candle"
            :style="candleStyle(i)"
          />
        </div>
        <!-- 模拟均线波浪 -->
        <svg class="sk-waves" viewBox="0 0 100 100" preserveAspectRatio="none">
          <polyline
            v-for="(wave, wi) in waves"
            :key="wi"
            :points="wave.points"
            :stroke="wave.color"
            stroke-width="0.4"
            fill="none"
            opacity="0.4"
          />
        </svg>
      </div>
    </div>

    <!-- 分隔线 -->
    <div class="sk-divider" />

    <!-- 成交量区骨架 -->
    <div class="sk-volume">
      <div class="sk-y-axis">
        <div v-for="i in 3" :key="i" class="sk-block sk-y-label" />
      </div>
      <div class="sk-vol-bars">
        <div
          v-for="i in candleCount"
          :key="i"
          class="sk-vol-bar"
          :style="volStyle(i)"
        />
      </div>
    </div>

    <!-- 分隔线 -->
    <div class="sk-divider" />

    <!-- 技术指标区骨架 -->
    <div class="sk-indicator">
      <div class="sk-y-axis">
        <div v-for="i in 3" :key="i" class="sk-block sk-y-label" />
      </div>
      <svg class="sk-waves full" viewBox="0 0 100 100" preserveAspectRatio="none">
        <polyline
          v-for="(wave, wi) in indicatorWaves"
          :key="wi"
          :points="wave.points"
          :stroke="wave.color"
          stroke-width="0.5"
          fill="none"
          opacity="0.5"
        />
      </svg>
    </div>

    <!-- X 轴 -->
    <div class="sk-x-axis">
      <div v-for="i in 6" :key="i" class="sk-block sk-x-label" />
    </div>

    <!-- 加载提示 -->
    <div class="sk-tip">
      <span class="sk-spinner" />
      <span class="sk-tip-text">{{ message }}</span>
    </div>

  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  message: { type: String, default: '加载中…' },
})

const candleCount = 60

/** 生成类 K 线随机高度和颜色（纯视觉，不代表真实数据） */
function candleStyle(i) {
  const seed   = (i * 137 + 31) % 97
  const height = 10 + seed % 50
  const top    = 10 + (i * 53) % 40
  const up     = seed % 2 === 0
  return {
    height: `${height}%`,
    top:    `${top}%`,
    background: up ? 'rgba(239,68,68,0.25)' : 'rgba(34,197,94,0.25)',
    width: `${100 / candleCount - 0.5}%`,
  }
}

function volStyle(i) {
  const seed   = (i * 173 + 59) % 89
  const height = 5 + seed % 90
  const up     = seed % 2 === 0
  return {
    height: `${height}%`,
    background: up ? 'rgba(239,68,68,0.3)' : 'rgba(34,197,94,0.3)',
    width: `${100 / candleCount - 0.5}%`,
  }
}

/** 生成正弦波折线点（模拟均线） */
function sineWave(phase, amp, base, points = 80) {
  const pts = []
  for (let i = 0; i <= points; i++) {
    const x = i / points * 100
    const y = base + Math.sin(i * 0.25 + phase) * amp
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`)
  }
  return pts.join(' ')
}

const waves = computed(() => [
  { color: '#f5c842', points: sineWave(0,    15, 45) },
  { color: '#f09a00', points: sineWave(0.6,  12, 42) },
  { color: '#f06400', points: sineWave(1.2,  10, 50) },
  { color: '#cc4444', points: sineWave(1.8,  8,  55) },
])

const indicatorWaves = computed(() => [
  { color: '#38bdf8', points: sineWave(0,   20, 50) },
  { color: '#f97316', points: sineWave(1.2, 18, 50) },
  { color: '#a78bfa', points: sineWave(2.4, 16, 50) },
])
</script>

<style scoped>
/* ── 整体容器 ──────────────────────────────────────────────────────────── */
.kline-skeleton {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 500px;
  background: #0f0f1a;
  border: 1px solid #222;
  border-radius: 6px;
  overflow: hidden;
  position: relative;
  color: #333;
}

/* ── 骨架块通用动画 ─────────────────────────────────────────────────────── */
.sk-block {
  border-radius: 3px;
  background: linear-gradient(90deg, #1a1a2a 25%, #252540 50%, #1a1a2a 75%);
  background-size: 200% 100%;
  animation: sk-shimmer 1.5s ease-in-out infinite;
}

@keyframes sk-shimmer {
  0%   { background-position: 200% 0 }
  100% { background-position: -200% 0 }
}

/* ── 工具栏 ─────────────────────────────────────────────────────────────── */
.sk-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid #1a1a2a;
  gap: 8px;
  flex-shrink: 0;
}

.sk-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sk-tag   { width: 48px; height: 20px; }
.w80      { width: 80px; }
.sk-btn   { width: 56px; height: 24px; border-radius: 4px; }
.w90      { width: 90px; }

.sk-period-group {
  display: flex;
  gap: 3px;
}
.sk-period { width: 30px; height: 22px; }

/* ── 十字光标行 ──────────────────────────────────────────────────────────── */
.sk-crosshair {
  display: flex;
  gap: 16px;
  padding: 4px 12px;
  border-bottom: 1px solid #1a1a2a;
  flex-shrink: 0;
}
.sk-crosshair-item { width: 64px; height: 14px; }

/* ── 图表区布局（共用） ──────────────────────────────────────────────────── */
.sk-main,
.sk-volume,
.sk-indicator {
  display: flex;
  position: relative;
  flex-shrink: 0;
}

.sk-main      { flex: 1; min-height: 220px; }
.sk-volume    { height: 90px; }
.sk-indicator { height: 80px; }

.sk-y-axis {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 6px 0;
  width: 52px;
  flex-shrink: 0;
}
.sk-y-label { width: 40px; height: 12px; }

.sk-chart-body {
  flex: 1;
  position: relative;
  overflow: hidden;
}

/* ── K 线蜡烛 ────────────────────────────────────────────────────────────── */
.sk-candles {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: flex-end;
  padding: 4px 0;
  gap: 1px;
}

.sk-candle {
  position: relative;
  border-radius: 1px;
  flex-shrink: 0;
}

/* ── 均线 SVG ────────────────────────────────────────────────────────────── */
.sk-waves {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.sk-waves.full {
  width: calc(100% - 0px);
  height: 100%;
}

/* ── 成交量条 ────────────────────────────────────────────────────────────── */
.sk-vol-bars {
  flex: 1;
  display: flex;
  align-items: flex-end;
  padding: 4px 0 2px;
  gap: 1px;
}
.sk-vol-bar {
  flex-shrink: 0;
  border-radius: 1px 1px 0 0;
}

/* ── 分隔线 ──────────────────────────────────────────────────────────────── */
.sk-divider {
  height: 1px;
  background: #1a1a2a;
  flex-shrink: 0;
}

/* ── X 轴 ────────────────────────────────────────────────────────────────── */
.sk-x-axis {
  display: flex;
  justify-content: space-around;
  padding: 4px 12px;
  flex-shrink: 0;
}
.sk-x-label { width: 60px; height: 12px; }

/* ── 加载提示 ────────────────────────────────────────────────────────────── */
.sk-tip {
  position: absolute;
  bottom: 40px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(15, 15, 26, 0.85);
  border: 1px solid #252540;
  border-radius: 20px;
  padding: 6px 16px;
  pointer-events: none;
}

.sk-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #333;
  border-top-color: #6366f1;
  border-radius: 50%;
  animation: sk-spin 0.8s linear infinite;
  flex-shrink: 0;
}

@keyframes sk-spin {
  to { transform: rotate(360deg) }
}

.sk-tip-text {
  font-size: 12px;
  color: #666;
  white-space: nowrap;
}
</style>
