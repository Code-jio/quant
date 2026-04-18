<script setup>
/**
 * TradingPanel.vue — 手动交易面板
 *
 * 功能：手动下单（开仓/平仓）、一键撤单、快捷平仓
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  placeOrder, cancelAllOrders, closePosition,
  fetchPositions, searchContracts,
} from '@/api/index.js'

// ── 下单表单 ─────────────────────────────────────────────────────────────────
const form = ref({
  symbol:     '',
  direction:  'long',
  offset:     'open',
  price:      0,
  volume:     1,
  order_type: 'market',
})

const submitting   = ref(false)
const cancellingAll = ref(false)

// ── 合约搜索 ─────────────────────────────────────────────────────────────────
const contractOptions = ref([])
const searchLoading   = ref(false)
let searchTimer = null

function onSymbolSearch(query) {
  if (searchTimer) clearTimeout(searchTimer)
  if (!query || query.length < 1) {
    contractOptions.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    searchLoading.value = true
    try {
      const res = await searchContracts({ query, limit: 20 })
      contractOptions.value = (res.data || []).map(c => ({
        value: c.symbol || c.code,
        label: `${c.symbol || c.code} — ${c.name || ''}`,
      }))
    } catch { contractOptions.value = [] }
    finally { searchLoading.value = false }
  }, 300)
}

// ── 持仓列表（用于快捷平仓）─────────────────────────────────────────────────
const positions      = ref([])
const posLoading     = ref(false)
const closingSymbol  = ref('')

async function loadPositions() {
  posLoading.value = true
  try {
    positions.value = await fetchPositions()
  } catch { /* ignore */ }
  finally { posLoading.value = false }
}

// ── 工具函数 ─────────────────────────────────────────────────────────────────
const OFFSET_LABELS = { open: '开仓', close: '平仓', close_today: '平今', close_yesterday: '平昨' }
function offsetLabel(v) { return OFFSET_LABELS[v] || v }
function pnlCss(v) { return Number(v) > 0 ? 'c-green' : Number(v) < 0 ? 'c-red' : 'c-muted' }

// ── 提交下单 ─────────────────────────────────────────────────────────────────
async function handleSubmit() {
  if (!form.value.symbol) {
    ElMessage.warning('请输入合约代码')
    return
  }
  const dirText = form.value.direction === 'long' ? '买入' : '卖出'
  const offText = offsetLabel(form.value.offset)
  const priceText = form.value.order_type === 'market' ? '市价' : form.value.price
  try {
    await ElMessageBox.confirm(
      `确认 ${dirText}${offText} ${form.value.symbol} ${form.value.volume}手 @${priceText}？`,
      '下单确认',
      { confirmButtonText: '确认下单', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }

  submitting.value = true
  try {
    const res = await placeOrder({
      symbol:     form.value.symbol,
      direction:  form.value.direction,
      offset:     form.value.offset,
      price:      form.value.order_type === 'market' ? 0 : form.value.price,
      volume:     form.value.volume,
      order_type: form.value.order_type,
    })
    ElMessage.success(`下单成功，委托号: ${res.order_id}`)
    loadPositions()
  } catch (err) {
    ElMessage.error(`下单失败: ${err.message}`)
  } finally {
    submitting.value = false
  }
}

// ── 一键撤单 ─────────────────────────────────────────────────────────────────
async function handleCancelAll() {
  try {
    await ElMessageBox.confirm('确认撤销所有活跃委托单？', '一键撤单', {
      confirmButtonText: '全部撤销', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }

  cancellingAll.value = true
  try {
    const res = await cancelAllOrders()
    ElMessage.success(`已撤销 ${res.cancelled} 笔委托`)
  } catch (err) {
    ElMessage.error(`撤单失败: ${err.message}`)
  } finally {
    cancellingAll.value = false
  }
}

// ── 快捷平仓 ─────────────────────────────────────────────────────────────────
async function handleClosePosition(pos) {
  try {
    await ElMessageBox.confirm(
      `确认市价平仓 ${pos.symbol} ${pos.direction === 'long' ? '多' : '空'} ${pos.volume}手？`,
      '平仓确认',
      { confirmButtonText: '确认平仓', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }

  closingSymbol.value = pos.symbol
  try {
    await closePosition(pos.symbol, { volume: 0, price: 0 })
    ElMessage.success(`${pos.symbol} 平仓指令已发送`)
    setTimeout(loadPositions, 500)
  } catch (err) {
    ElMessage.error(`平仓失败: ${err.message}`)
  } finally {
    closingSymbol.value = ''
  }
}

// ── 定时刷新持仓 ─────────────────────────────────────────────────────────────
let posTimer = null
onMounted(() => {
  loadPositions()
  posTimer = setInterval(loadPositions, 5000)
})
onUnmounted(() => clearInterval(posTimer))
</script>

<template>
  <div class="tp-wrap">
    <div class="tp-body">

      <!-- ── 左侧：下单表单 ──────────────────────────────────────────── -->
      <div class="tp-form">
        <div class="tp-form-title">手动下单</div>

        <!-- 合约 -->
        <div class="tp-row">
          <label class="tp-label">合约</label>
          <el-select
            v-model="form.symbol"
            filterable
            remote
            :remote-method="onSymbolSearch"
            :loading="searchLoading"
            placeholder="输入合约代码…"
            size="small"
            class="tp-input"
            allow-create
            default-first-option
          >
            <el-option
              v-for="opt in contractOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </div>

        <!-- 方向 -->
        <div class="tp-row">
          <label class="tp-label">方向</label>
          <el-radio-group v-model="form.direction" size="small" class="tp-input">
            <el-radio-button value="long">
              <span class="dir-long">买入</span>
            </el-radio-button>
            <el-radio-button value="short">
              <span class="dir-short">卖出</span>
            </el-radio-button>
          </el-radio-group>
        </div>

        <!-- 开平 -->
        <div class="tp-row">
          <label class="tp-label">开平</label>
          <el-radio-group v-model="form.offset" size="small" class="tp-input">
            <el-radio-button value="open">开仓</el-radio-button>
            <el-radio-button value="close">平仓</el-radio-button>
            <el-radio-button value="close_today">平今</el-radio-button>
            <el-radio-button value="close_yesterday">平昨</el-radio-button>
          </el-radio-group>
        </div>

        <!-- 订单类型 -->
        <div class="tp-row">
          <label class="tp-label">类型</label>
          <el-radio-group v-model="form.order_type" size="small" class="tp-input">
            <el-radio-button value="market">市价</el-radio-button>
            <el-radio-button value="limit">限价</el-radio-button>
          </el-radio-group>
        </div>

        <!-- 价格 -->
        <div class="tp-row" v-if="form.order_type === 'limit'">
          <label class="tp-label">价格</label>
          <el-input-number
            v-model="form.price"
            :min="0"
            :step="1"
            :precision="2"
            size="small"
            class="tp-input"
            controls-position="right"
          />
        </div>

        <!-- 数量 -->
        <div class="tp-row">
          <label class="tp-label">数量</label>
          <div class="tp-vol-group">
            <el-button size="small" plain @click="form.volume = Math.max(1, form.volume - 1)">-</el-button>
            <el-input-number
              v-model="form.volume"
              :min="1"
              :max="9999"
              :step="1"
              size="small"
              controls-position="right"
              style="width: 100px"
            />
            <el-button size="small" plain @click="form.volume += 1">+</el-button>
            <el-button size="small" plain @click="form.volume = 5">5</el-button>
            <el-button size="small" plain @click="form.volume = 10">10</el-button>
          </div>
        </div>

        <!-- 下单按钮 -->
        <div class="tp-actions">
          <el-button
            :type="form.direction === 'long' ? 'success' : 'danger'"
            :loading="submitting"
            @click="handleSubmit"
            style="flex: 1"
          >
            {{ form.direction === 'long' ? '买入' : '卖出' }}
            {{ offsetLabel(form.offset) }}
          </el-button>
          <el-button
            type="warning"
            plain
            :loading="cancellingAll"
            @click="handleCancelAll"
          >
            一键撤单
          </el-button>
        </div>
      </div>

      <!-- ── 右侧：快捷平仓 ──────────────────────────────────────────── -->
      <div class="tp-positions">
        <div class="tp-pos-header">
          <span class="tp-form-title">快捷平仓</span>
          <el-button size="small" plain :loading="posLoading" @click="loadPositions">
            <el-icon><RefreshRight /></el-icon>
          </el-button>
        </div>

        <div v-if="positions.length === 0" class="tp-empty">
          <span class="c-muted">当前空仓</span>
        </div>

        <div v-else class="tp-pos-list">
          <div
            v-for="pos in positions"
            :key="pos.symbol"
            class="tp-pos-item"
          >
            <div class="tp-pos-info">
              <span class="mono fw-600">{{ pos.symbol }}</span>
              <el-tag
                :type="pos.direction === 'long' ? 'success' : 'danger'"
                size="small"
                effect="dark"
              >
                {{ pos.direction === 'long' ? '多' : '空' }} {{ pos.volume }}手
              </el-tag>
              <span class="mono" :class="pnlCss(pos.pnl)">
                {{ pos.pnl >= 0 ? '+' : '' }}{{ (pos.pnl || 0).toFixed(2) }}
              </span>
            </div>
            <el-button
              type="danger"
              size="small"
              plain
              :loading="closingSymbol === pos.symbol"
              @click="handleClosePosition(pos)"
            >
              平仓
            </el-button>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.tp-wrap {
  background: var(--q-panel);
  border: 1px solid var(--q-border);
  border-radius: 10px;
  overflow: hidden;
}
.tp-body {
  display: flex;
  gap: 0;
  min-height: 280px;
}

/* ── 左侧下单表单 ── */
.tp-form {
  flex: 1;
  padding: 16px 20px;
  border-right: 1px solid var(--q-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tp-form-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--q-text);
  margin-bottom: 2px;
}
.tp-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.tp-label {
  width: 42px;
  font-size: 12px;
  color: var(--q-muted);
  flex-shrink: 0;
  text-align: right;
}
.tp-input { flex: 1; }
.tp-vol-group {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
}
.tp-actions {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}

.dir-long  { color: var(--q-green); font-weight: 600; }
.dir-short { color: var(--q-red);   font-weight: 600; }

/* ── 右侧快捷平仓 ── */
.tp-positions {
  width: 340px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tp-pos-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.tp-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  font-size: 12px;
}
.tp-pos-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  max-height: 240px;
}
.tp-pos-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  background: rgba(0,0,0,.15);
  border-radius: 6px;
  gap: 8px;
}
.tp-pos-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

/* ── 工具类 ── */
.mono    { font-family: var(--q-font-mono); font-variant-numeric: tabular-nums; }
.fw-600  { font-weight: 600; }
.c-green { color: var(--q-green); }
.c-red   { color: var(--q-red); }
.c-muted { color: var(--q-muted); }
</style>
