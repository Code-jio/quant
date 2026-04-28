<template>
  <div class="kline-toolbar" :class="{ mobile: isMobile }">
    <div class="toolbar-left">
      <span v-if="name" class="contract-tag">{{ name }}</span>
      <span v-if="symbol" class="symbol-tag">{{ symbol }}</span>

      <div class="period-btns">
        <button
          v-for="iv in INTERVALS"
          :key="iv.value"
          class="period-btn"
          :class="{ active: currentInterval === iv.value }"
          @click="emit('set-interval', iv.value)"
        >{{ iv.label }}</button>
      </div>

      <div v-if="!isMobile" class="custom-period">
        <el-input-number
          v-model="customNModel"
          :min="1"
          :max="9999"
          :controls="false"
          style="width:64px"
          size="small"
          placeholder="N"
        />
        <el-select v-model="customUnitModel" size="small" style="width:70px">
          <el-option label="分" value="m" />
          <el-option label="时" value="h" />
          <el-option label="日" value="d" />
          <el-option label="周" value="w" />
        </el-select>
        <el-button size="small" @click="emit('apply-custom-period')">确认</el-button>
      </div>

      <span class="bar-count" v-if="barsCount">共 {{ barsCount }} 根</span>
    </div>

    <div class="toolbar-right">
      <el-popover
        placement="bottom-end"
        :width="280"
        trigger="click"
        popper-class="ma-popover"
      >
        <template #reference>
          <el-button size="small" class="tool-btn">
            均线
            <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
        </template>
        <div class="ma-panel">
          <div v-for="(ma, idx) in maConfig" :key="ma.n" class="ma-row">
            <el-switch v-model="ma.visible" size="small" @change="emit('refresh-chart')" />
            <span class="ma-label">MA{{ ma.n }}</span>
            <el-color-picker
              v-model="ma.color"
              size="small"
              @change="emit('refresh-chart')"
              :predefine="MA_COLORS"
            />
            <el-select
              v-model="ma.dashType"
              size="small"
              style="width:70px"
              @change="emit('refresh-chart')"
            >
              <el-option label="实线" value="solid" />
              <el-option label="虚线" value="dashed" />
              <el-option label="点线" value="dotted" />
            </el-select>
            <el-button
              size="small"
              type="danger"
              text
              @click="emit('remove-ma', idx)"
            >×</el-button>
          </div>
          <div class="ma-add-row">
            <el-input-number
              v-model="newMaNModel"
              :min="1"
              :max="500"
              :controls="false"
              size="small"
              style="width:70px"
              placeholder="周期"
            />
            <el-button size="small" type="primary" @click="emit('add-ma')">+ 添加均线</el-button>
          </div>
        </div>
      </el-popover>

      <el-select
        v-model="activeIndicatorModel"
        size="small"
        style="width:90px"
        @change="emit('refresh-chart')"
      >
        <el-option label="MACD" value="macd" />
        <el-option label="KDJ" value="kdj" />
        <el-option label="RSI" value="rsi" />
      </el-select>

      <div v-if="!isMobile" class="draw-tools">
        <el-tooltip content="选择" placement="bottom">
          <button
            class="draw-btn"
            :class="{ active: drawMode === 'pointer' }"
            @click="emit('update:drawMode', 'pointer')"
          >
            <el-icon><Pointer /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="趋势线" placement="bottom">
          <button
            class="draw-btn"
            :class="{ active: drawMode === 'trend' }"
            @click="emit('update:drawMode', 'trend')"
          >
            <el-icon><Edit /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="水平线" placement="bottom">
          <button
            class="draw-btn"
            :class="{ active: drawMode === 'hLine' }"
            @click="emit('update:drawMode', 'hLine')"
          >
            <el-icon><Minus /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip content="垂直线" placement="bottom">
          <button
            class="draw-btn"
            :class="{ active: drawMode === 'vLine' }"
            @click="emit('update:drawMode', 'vLine')"
          >
            <el-icon><SemiSelect /></el-icon>
          </button>
        </el-tooltip>
        <el-tooltip v-if="drawnLinesCount" content="清除画线" placement="bottom">
          <button class="draw-btn" @click="emit('clear-drawings')">
            <el-icon><Delete /></el-icon>
          </button>
        </el-tooltip>
      </div>

      <div class="toolbar-divider" />

      <el-tooltip :content="isFullscreen ? '退出全屏' : '全屏'" placement="bottom">
        <button class="tool-btn icon-btn" @click="emit('toggle-fullscreen')">
          <el-icon><FullScreen /></el-icon>
        </button>
      </el-tooltip>

      <el-tooltip content="保存图片" placement="bottom">
        <button class="tool-btn icon-btn" @click="emit('save-image')">
          <el-icon><Download /></el-icon>
        </button>
      </el-tooltip>

      <el-tooltip content="刷新数据 (R)" placement="bottom">
        <button class="tool-btn icon-btn" :class="{ spinning: loading }" @click="emit('reload')">
          <el-icon><Refresh /></el-icon>
        </button>
      </el-tooltip>

      <el-tooltip content="快捷键帮助 (?)" placement="bottom">
        <button class="tool-btn icon-btn" @click="emit('update:showShortcutsHelp', !showShortcutsHelp)">
          <span style="font-size:13px;font-weight:600;line-height:1">?</span>
        </button>
      </el-tooltip>

      <span v-if="barsCount" class="bar-count-right">
        {{ barsCount }} 根
        <span v-if="hasMore" class="has-more-dot" title="向左拖拽可加载更多历史" />
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import {
  ArrowDown,
  Delete,
  Download,
  Edit,
  FullScreen,
  Minus,
  Pointer,
  Refresh,
  SemiSelect,
} from '@element-plus/icons-vue'

import { INTERVALS, MA_COLORS } from '@/config/kline.js'

const props = defineProps({
  name: { type: String, default: '' },
  symbol: { type: String, default: '' },
  currentInterval: { type: String, required: true },
  customN: { type: Number, required: true },
  customUnit: { type: String, required: true },
  barsCount: { type: Number, default: 0 },
  hasMore: { type: Boolean, default: false },
  isMobile: { type: Boolean, default: false },
  maConfig: { type: Array, default: () => [] },
  newMaN: { type: Number, default: null },
  activeIndicator: { type: String, required: true },
  drawMode: { type: String, required: true },
  drawnLinesCount: { type: Number, default: 0 },
  isFullscreen: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  showShortcutsHelp: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:customN',
  'update:customUnit',
  'update:newMaN',
  'update:activeIndicator',
  'update:drawMode',
  'update:showShortcutsHelp',
  'set-interval',
  'apply-custom-period',
  'refresh-chart',
  'remove-ma',
  'add-ma',
  'clear-drawings',
  'toggle-fullscreen',
  'save-image',
  'reload',
])

const customNModel = computed({
  get: () => props.customN,
  set: (value) => emit('update:customN', value),
})

const customUnitModel = computed({
  get: () => props.customUnit,
  set: (value) => emit('update:customUnit', value),
})

const newMaNModel = computed({
  get: () => props.newMaN,
  set: (value) => emit('update:newMaN', value),
})

const activeIndicatorModel = computed({
  get: () => props.activeIndicator,
  set: (value) => emit('update:activeIndicator', value),
})
</script>

<style scoped>
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

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

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

.kline-toolbar.mobile {
  padding: 4px 8px;
}

.kline-toolbar.mobile .period-btns {
  gap: 1px;
}

.kline-toolbar.mobile .period-btn {
  padding: 2px 5px;
  font-size: 11px;
}

:global(.ma-popover) {
  background: #1a1a2a !important;
  border-color: #333 !important;
  color: #ccc !important;
}
</style>
