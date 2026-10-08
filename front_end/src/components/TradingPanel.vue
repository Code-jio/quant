<script setup>
/**
 * TradingPanel.vue — 手动交易面板
 *
 * 面向实盘 CTP 账户：下单前做本地校验、确认预览和快捷平仓方向锁定。
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  placeOrder, cancelAllOrders, closePosition,
  fetchPositions, searchContracts, fetchTicks,
  fetchRiskStatus, emergencyStop, resumeTrading, fetchTradingReconcile,
  fetchMarginRate,
} from '@/api/index.js'

const props = defineProps({ compact: Boolean, contract: { type: Object, default: null } })
const emit = defineEmits(['select-contract'])

// ── 下单表单 ─────────────────────────────────────────────────────────────────
const form = ref({
  symbol:     '',
  direction:  'long',
  offset:     'open',
  price:      0,
  volume:     1,
  order_type: 'limit',
})

const submitting    = ref(false)
const cancellingAll = ref(false)
const emergencyLoading = ref(false)
const quickCloseOffset = ref('close')
const riskState = ref(null)
const reconcileState = ref(null)

const QUICK_VOLUMES = [1, 3, 5, 10]
const OFFSET_LABELS = {
  open: '开仓',
  close: '平仓',
  close_today: '平今',
  close_yesterday: '平昨',
}
const ORDER_TYPE_LABELS = { market: '市价', limit: '限价' }
const CLOSE_OFFSET_OPTIONS = [
  { value: 'close', label: '平仓' },
  { value: 'close_today', label: '平今' },
  { value: 'close_yesterday', label: '平昨' },
]

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
        label: `${c.symbol || c.code} - ${c.name || ''}`,
        contract: { ...c, symbol: c.symbol || c.code },
      }))
    } catch {
      contractOptions.value = []
    } finally {
      searchLoading.value = false
    }
  }, 300)
}

// ── 持仓列表（用于快捷平仓）─────────────────────────────────────────────────
const positions      = ref([])
const posLoading     = ref(false)
const positionError = ref('')
const closingKey     = ref('')

async function loadPositions() {
  posLoading.value = true
  try {
    positions.value = await fetchPositions()
    positionError.value=''
  } catch (error) {
    positionError.value=error.message
  } finally {
    posLoading.value = false
  }
}

// ── 工具函数 ─────────────────────────────────────────────────────────────────
const normalizedSymbol = computed(() => String(form.value.symbol || '').trim())
const normalizedVolume = computed(() => Number(form.value.volume) || 0)
const normalizedPrice  = computed(() => Number(form.value.price) || 0)
const marginState = ref(null)
let marginRequest = 0
const marginSummary = computed(() => {
  const state = marginState.value
  if (state?.status !== 'ready') return state?.reason || '正在查询账户适用保证金；结果未知时开仓受限'
  const rate = (value) => `${(Number(value) * 100).toFixed(2)}%`
  const fixed = (value) => Number(value) > 0 ? ` + ${Number(value).toFixed(2)}元/手` : ''
  return `柜台保证金：多头 ${rate(state.long_margin_rate)}${fixed(state.long_margin_per_lot)}，空头 ${rate(state.short_margin_rate)}${fixed(state.short_margin_per_lot)}；交易日 ${state.trading_day}`
})
async function loadMarginStatus() {
  const symbol = normalizedSymbol.value
  const requestId = ++marginRequest
  if (!symbol) { marginState.value = null; return }
  try {
    const state = await fetchMarginRate(symbol)
    if (requestId === marginRequest) marginState.value = state
  } catch (error) {
    if (requestId === marginRequest) marginState.value = { status: 'error', reason: error.message }
  }
}
watch(normalizedSymbol, (symbol) => {
  // A price entered for the previous instrument must never carry into a new ticket.
  form.value.price = 0
  marginState.value = null
  loadMarginStatus()
  if (symbol !== (props.contract?.symbol || '')) {
    emit('select-contract', symbol ? contractOptions.value.find(item => item.value === symbol)?.contract || { symbol, name: '' } : null)
  }
})
watch(() => props.contract?.symbol, symbol => {
  if ((symbol || '') !== form.value.symbol) form.value.symbol = symbol || ''
}, { immediate: true })

const orderValidation = computed(() => {
  if (!normalizedSymbol.value) return { valid: false, message: '请输入合约代码' }
  if (!Number.isInteger(normalizedVolume.value) || normalizedVolume.value <= 0) {
    return { valid: false, message: '数量必须是大于 0 的整数' }
  }
  if (form.value.order_type === 'limit' && normalizedPrice.value <= 0) {
    return { valid: false, message: '限价单价格必须大于 0' }
  }
  return { valid: true, message: '订单参数有效' }
})

const canSubmit = computed(() => orderValidation.value.valid && !submitting.value)
const emergencyActive = computed(() => Boolean(riskState.value?.risk?.emergency_stop))
const riskSummary = computed(() => {
  const risk = riskState.value?.risk
  if (!risk) return '风控未连接'
  if (risk.emergency_stop) return `急停中${risk.emergency_reason ? `：${risk.emergency_reason}` : ''}`
  return `单笔≤${risk.max_order_volume}手 / 日亏损≤${Math.round((risk.max_daily_loss_ratio || 0) * 100)}%`
})

const orderPreview = computed(() => {
  const isLong = form.value.direction === 'long'
  const isMarket = form.value.order_type === 'market'
  return {
    symbol: normalizedSymbol.value || '--',
    sideText: isLong ? '买入' : '卖出',
    sideClass: isLong ? 'is-long' : 'is-short',
    offsetText: offsetLabel(form.value.offset),
    typeText: ORDER_TYPE_LABELS[form.value.order_type] || form.value.order_type,
    priceText: isMarket ? '市价' : fmtPrice(normalizedPrice.value),
    volume: normalizedVolume.value > 0 ? normalizedVolume.value : 0,
  }
})

watch(() => form.value.order_type, (orderType) => {
  if (orderType === 'market') form.value.price = 0
})

function offsetLabel(v) { return OFFSET_LABELS[v] || v }
function pnlCss(v) { return Number(v) > 0 ? 'c-green' : Number(v) < 0 ? 'c-red' : 'c-muted' }
function dirLabel(v) { return v === 'long' ? '多' : v === 'short' ? '空' : '净' }
function dirType(v) { return v === 'long' ? 'success' : v === 'short' ? 'danger' : 'info' }
function fmtPrice(v) {
  const n = Number(v) || 0
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
}
function fmtSigned(v) {
  const n = Number(v) || 0
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`
}
function positionKey(pos) {
  return `${pos.symbol}-${pos.direction || 'net'}`
}
function absVolume(pos) {
  return Math.abs(Number(pos.volume) || 0)
}
function availableVolume(pos) {
  const frozen = Math.abs(Number(pos.frozen) || 0)
  return Math.max(0, absVolume(pos) - frozen)
}
function closeActionText(pos) {
  if (pos.direction === 'long') return '卖出平多'
  if (pos.direction === 'short') return '买入平空'
  return '平仓'
}
function normalizeOrderForm() {
  form.value.symbol = normalizedSymbol.value
  form.value.volume = Math.max(1, Math.trunc(normalizedVolume.value || 1))
  form.value.price = form.value.order_type === 'market' ? 0 : Number(normalizedPrice.value.toFixed(4))
}
function applyQuickVolume(volume) {
  form.value.volume = volume
}
function nudgeVolume(delta) {
  form.value.volume = Math.max(1, Math.trunc(normalizedVolume.value || 1) + delta)
}
function validateOrder(showMessage = true) {
  if (orderValidation.value.valid) return true
  if (showMessage) ElMessage.warning(orderValidation.value.message)
  return false
}

// ── 提交下单 ─────────────────────────────────────────────────────────────────
async function handleSubmit() {
  if (!validateOrder()) return
  normalizeOrderForm()

  const preview = orderPreview.value
  try {
    await ElMessageBox.confirm(
      `确认${preview.sideText}${preview.offsetText} ${preview.symbol} ${preview.volume}手，${preview.priceText}？`,
      '下单确认',
      { confirmButtonText: '确认下单', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

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
    ElMessage.success(`委托请求已发送，委托号: ${res.order_id}，请核对柜台回报`)
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
  } catch {
    return
  }

  cancellingAll.value = true
  try {
    const res = await cancelAllOrders()
    ElMessage.success(`已发送 ${res.cancelled} 笔撤单请求，发送失败 ${res.failed || 0} 笔，请核对柜台回报`)
  } catch (err) {
    ElMessage.error(`撤单失败: ${err.message}`)
  } finally {
    cancellingAll.value = false
  }
}

async function loadRiskStatus() {
  try {
    riskState.value = await fetchRiskStatus()
    reconcileState.value = await fetchTradingReconcile().catch(() => null)
  } catch {
    riskState.value = null
    reconcileState.value = null
  }
}

async function handleEmergencyStop() {
  try {
    await ElMessageBox.confirm('确认立即开启交易急停并撤销所有活跃委托？', '交易急停', {
      confirmButtonText: '立即急停', cancelButtonText: '取消', type: 'error',
    })
  } catch {
    return
  }

  emergencyLoading.value = true
  try {
    const res = await emergencyStop({ reason: 'operator_panel', cancel_orders: true, stop_strategies: false })
    ElMessage.success(`急停已开启，撤单 ${res.cancelled || 0} 笔，失败 ${res.failed || 0} 笔`)
    await loadRiskStatus()
  } catch (err) {
    ElMessage.error(`急停失败: ${err.message}`)
  } finally {
    emergencyLoading.value = false
  }
}

async function handleResumeTrading() {
  try {
    await ElMessageBox.confirm('确认解除交易急停？解除后策略和手动下单会继续受风控约束。', '解除急停', {
      confirmButtonText: '解除急停', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }

  emergencyLoading.value = true
  try {
    await resumeTrading()
    ElMessage.success('交易急停已解除')
    await loadRiskStatus()
  } catch (err) {
    ElMessage.error(`解除失败: ${err.message}`)
  } finally {
    emergencyLoading.value = false
  }
}

// ── 快捷平仓 ─────────────────────────────────────────────────────────────────
async function handleClosePosition(pos) {
  const available = availableVolume(pos)
  if (available <= 0) {
    ElMessage.warning(`${pos.symbol} 当前无可平数量`)
    return
  }

  let closePrice
  try {
    const data=await fetchTicks([pos.symbol])
    const tick=data.ticks?.[pos.symbol]
    const age=Date.now()-new Date(tick?.timestamp).getTime()
    closePrice=Number(pos.direction==='short' ? tick?.ask1 : tick?.bid1)
    if (!Number.isFinite(age) || age>10000 || age< -5000 || !(closePrice>0)) throw new Error('缺少新鲜买卖报价，请使用手动限价平仓')
  } catch(error) { ElMessage.warning(error.message); return }
  const key = positionKey(pos)
  const direction = pos.direction === 'long' || pos.direction === 'short' ? pos.direction : ''
  try {
    await ElMessageBox.confirm(
      `确认${closeActionText(pos)} ${pos.symbol} ${available}手，${offsetLabel(quickCloseOffset.value)}，限价 ${closePrice}？`,
      '平仓确认',
      { confirmButtonText: '确认平仓', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  closingKey.value = key
  try {
    await closePosition(pos.symbol, {
      direction,
      offset: quickCloseOffset.value,
      volume: 0,
      price: closePrice,
      order_type: 'limit',
    })
    ElMessage.success(`${pos.symbol} 平仓指令已发送`)
    setTimeout(loadPositions, 500)
  } catch (err) {
    ElMessage.error(`平仓失败: ${err.message}`)
  } finally {
    closingKey.value = ''
  }
}

// ── 定时刷新持仓 ─────────────────────────────────────────────────────────────
let posTimer = null
let riskTimer = null
onMounted(() => {
  loadPositions()
  loadRiskStatus()
  posTimer = setInterval(loadPositions, 5000)
  riskTimer = setInterval(() => { loadRiskStatus(); loadMarginStatus() }, 5000)
})
onUnmounted(() => {
  marginRequest++
  clearInterval(posTimer)
  clearInterval(riskTimer)
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <div class="tp-wrap" :class="{ 'tp-compact': compact }">
    <div class="tp-statusbar">
      <div class="status-left">
        <span class="live-dot"></span>
        <span class="fw-600">实盘委托</span>
      </div>
      <div class="status-right">
        <span
          class="validation-pill"
          :class="emergencyActive ? 'pill-danger' : riskState ? 'pill-ok' : 'pill-warn'"
        >
          {{ riskSummary }}
        </span>
        <span v-if="reconcileState" class="c-muted">
          活跃委托 {{ reconcileState.orders?.active_count ?? 0 }}
        </span>
        <el-button
          v-if="!emergencyActive"
          size="small"
          type="danger"
          plain
          :loading="emergencyLoading"
          @click="handleEmergencyStop"
        >
          <el-icon><CloseBold /></el-icon>
          急停
        </el-button>
        <el-button
          v-else
          size="small"
          type="success"
          plain
          :loading="emergencyLoading"
          @click="handleResumeTrading"
        >
          恢复
        </el-button>
      </div>
    </div>

    <div class="tp-body">
      <!-- ── 左侧：下单表单 ──────────────────────────────────────────── -->
      <div class="tp-form">
        <div class="tp-form-head">
          <span class="tp-form-title">委托录入</span>
          <span class="c-muted">发送前二次确认</span>
        </div>

        <div class="tp-grid">
          <div class="tp-row tp-row-wide">
            <label class="tp-label" for="trade-symbol">合约</label>
            <el-select
              v-model="form.symbol"
              filterable
              remote
              :remote-method="onSymbolSearch"
              :loading="searchLoading"
              placeholder="输入合约代码"
              size="small"
              class="tp-input"
              id="trade-symbol"
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

          <div class="tp-row">
            <label class="tp-label">方向</label>
            <el-radio-group v-model="form.direction" size="small" class="tp-input direction-group" aria-label="买卖方向">
              <el-radio-button value="long">
                <span class="dir-long">买入</span>
              </el-radio-button>
              <el-radio-button value="short">
                <span class="dir-short">卖出</span>
              </el-radio-button>
            </el-radio-group>
          </div>

          <div class="tp-row">
            <label class="tp-label">开平</label>
            <el-radio-group v-model="form.offset" size="small" class="tp-input offset-group" aria-label="开平方向">
              <el-radio-button value="open">开仓</el-radio-button>
              <el-radio-button value="close">平仓</el-radio-button>
              <el-radio-button value="close_today">平今</el-radio-button>
              <el-radio-button value="close_yesterday">平昨</el-radio-button>
            </el-radio-group>
          </div>

          <div v-if="!compact" class="tp-row">
            <label class="tp-label">类型</label>
            <el-radio-group v-model="form.order_type" size="small" class="tp-input">
              <el-radio-button value="market" disabled>市价（当前网关不支持）</el-radio-button>
              <el-radio-button value="limit">限价</el-radio-button>
            </el-radio-group>
          </div>

          <div class="tp-row" :class="{ 'is-disabled': form.order_type === 'market' }">
            <label class="tp-label" for="trade-price">限价</label>
            <el-input-number
              id="trade-price"
              v-model="form.price"
              :min="0"
              :step="1"
              :precision="2"
              :disabled="form.order_type === 'market'"
              size="small"
              class="tp-input"
              controls-position="right"
            />
          </div>

          <div class="tp-row tp-row-wide">
            <label class="tp-label" for="trade-volume">数量</label>
            <div class="tp-vol-group">
              <el-button size="small" plain aria-label="减少一手" @click="nudgeVolume(-1)">
                <el-icon><Minus /></el-icon>
              </el-button>
              <el-input-number
                id="trade-volume"
                v-model="form.volume"
                :min="1"
                :max="9999"
                :step="1"
                :precision="0"
                size="small"
                controls-position="right"
                class="volume-input"
              />
              <el-button size="small" plain aria-label="增加一手" @click="nudgeVolume(1)">
                <el-icon><Plus /></el-icon>
              </el-button>
              <el-button
                v-for="volume in QUICK_VOLUMES"
                :key="volume"
                size="small"
                plain
                :type="form.volume === volume ? 'primary' : ''"
                @click="applyQuickVolume(volume)"
              >
                {{ volume }}
              </el-button>
            </div>
          </div>
        </div>

        <el-alert v-if="normalizedSymbol" class="margin-status" :title="marginSummary" :type="marginState?.status === 'ready' ? 'info' : 'warning'" :closable="false" />
        <div class="tp-preview" :class="orderPreview.sideClass">
          <div class="preview-main">
            <span class="mono fw-600">{{ orderPreview.symbol }}</span>
            <span>{{ orderPreview.sideText }}{{ orderPreview.offsetText }}</span>
          </div>
          <div class="preview-grid">
            <span>类型</span>
            <strong>{{ orderPreview.typeText }}</strong>
            <span>价格</span>
            <strong>{{ orderPreview.priceText }}</strong>
            <span>数量</span>
            <strong>{{ orderPreview.volume }} 手</strong>
          </div>
        </div>

        <p class="ticket-validation" :class="{ valid: orderValidation.valid }">{{ orderValidation.valid ? '限价委托 · 点击后核对并确认' : orderValidation.message }}</p>
        <div class="tp-actions">
          <el-button
            :type="form.direction === 'long' ? 'success' : 'danger'"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="handleSubmit"
            class="submit-btn"
          >
            <el-icon><Check /></el-icon>
            {{ form.direction === 'long' ? '买入' : '卖出' }}{{ offsetLabel(form.offset) }}
          </el-button>
          <el-button
            type="warning"
            plain
            :loading="cancellingAll"
            @click="handleCancelAll"
          >
            <el-icon><CloseBold /></el-icon>
            一键撤单
          </el-button>
        </div>
      </div>

      <!-- ── 右侧：快捷平仓 ──────────────────────────────────────────── -->
      <div class="tp-positions">
        <el-alert v-if="positionError" :title="'持仓数据可能过期：'+positionError" type="warning" :closable="false" />
        <div class="tp-pos-header">
          <div>
            <span class="tp-form-title">快捷平仓</span>
            <span class="c-muted pos-count">{{ positions.length }} 个持仓</span>
          </div>
          <el-button size="small" plain aria-label="刷新可平持仓" :loading="posLoading" @click="loadPositions">
            <el-icon><RefreshRight /></el-icon>
          </el-button>
        </div>

        <el-radio-group v-model="quickCloseOffset" size="small" class="close-offset-group" aria-label="快捷平仓开平类型">
          <el-radio-button
            v-for="item in CLOSE_OFFSET_OPTIONS"
            :key="item.value"
            :value="item.value"
          >
            {{ item.label }}
          </el-radio-button>
        </el-radio-group>

        <div v-if="positions.length === 0" class="tp-empty">
          <span class="c-muted">当前空仓</span>
        </div>

        <div v-else class="tp-pos-list">
          <div
            v-for="pos in positions"
            :key="positionKey(pos)"
            class="tp-pos-item"
          >
            <div class="tp-pos-info">
              <div class="pos-line">
                <span class="mono fw-600">{{ pos.symbol }}</span>
                <el-tag
                  :type="dirType(pos.direction)"
                  size="small"
                  effect="dark"
                >
                  {{ dirLabel(pos.direction) }}
                </el-tag>
              </div>
              <div class="pos-line pos-meta">
                <span>持仓 <strong class="mono">{{ absVolume(pos) }}</strong></span>
                <span>可平 <strong class="mono">{{ availableVolume(pos) }}</strong></span>
                <span class="mono" :class="pnlCss(pos.pnl)">
                  {{ fmtSigned(pos.pnl) }}
                </span>
              </div>
            </div>
            <el-button
              type="danger"
              size="small"
              plain
              :disabled="availableVolume(pos) <= 0"
              :loading="closingKey === positionKey(pos)"
              @click="handleClosePosition(pos)"
            >
              {{ closeActionText(pos) }}
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
  border-radius: 8px;
  overflow: hidden;
}
.ticket-validation { margin: 0; font-size: 11px; color: var(--q-yellow); }
.ticket-validation.valid { color: var(--q-muted); }

.tp-statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 16px;
  background: rgba(0,0,0,.2);
  border-bottom: 1px solid var(--q-border);
  flex-wrap: wrap;
}

.status-left,
.status-right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--q-red);
  box-shadow: 0 0 8px rgba(248,81,73,.75);
  flex-shrink: 0;
}

.validation-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-size: 12px;
  white-space: nowrap;
}

.pill-ok {
  color: var(--q-green);
  border-color: rgba(63,185,80,.35);
  background: rgba(63,185,80,.08);
}

.pill-warn {
  color: var(--q-yellow);
  border-color: rgba(210,153,34,.35);
  background: rgba(210,153,34,.08);
}

.pill-danger {
  color: var(--q-red);
  border-color: rgba(248,81,73,.4);
  background: rgba(248,81,73,.1);
}

.tp-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  min-height: 292px;
}

.tp-form {
  padding: 16px 18px;
  border-right: 1px solid var(--q-border);
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.tp-form-head,
.tp-pos-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 12px;
}

.tp-form-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--q-text);
}

.tp-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1.1fr) minmax(220px, 1fr);
  gap: 10px 14px;
}

.tp-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.tp-row-wide {
  grid-column: 1 / -1;
}

.tp-row.is-disabled {
  opacity: .72;
}

.tp-label {
  width: 42px;
  font-size: 12px;
  color: var(--q-muted);
  flex-shrink: 0;
  text-align: right;
}

.tp-input {
  flex: 1;
  min-width: 0;
}

.tp-vol-group {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  flex-wrap: wrap;
  min-width: 0;
}

.volume-input {
  width: 116px;
}

.tp-preview {
  display: grid;
  grid-template-columns: minmax(150px, .7fr) minmax(260px, 1fr);
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--q-border);
  border-radius: 8px;
  background: rgba(13,17,23,.5);
}

.tp-preview.is-long {
  border-left: 3px solid var(--q-green);
}

.tp-preview.is-short {
  border-left: 3px solid var(--q-red);
}

.preview-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-size: 13px;
}

.preview-grid {
  display: grid;
  grid-template-columns: repeat(3, max-content minmax(54px, 1fr));
  align-items: center;
  gap: 4px 8px;
  font-size: 12px;
  color: var(--q-muted);
}

.preview-grid strong {
  color: var(--q-text);
  font-weight: 600;
}

.tp-actions {
  display: flex;
  gap: 8px;
}

.submit-btn {
  flex: 1;
}

.dir-long  { color: var(--q-green); font-weight: 600; }
.dir-short { color: var(--q-red);   font-weight: 600; }

.tp-positions {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.pos-count {
  margin-left: 8px;
  font-size: 12px;
}

.close-offset-group {
  width: 100%;
}

.close-offset-group :deep(.el-radio-button) {
  flex: 1;
}

.close-offset-group :deep(.el-radio-button__inner) {
  width: 100%;
}

.tp-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-height: 140px;
  font-size: 12px;
}

.tp-pos-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
  overflow-y: auto;
  max-height: 252px;
  padding-right: 2px;
}

.tp-pos-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  background: rgba(0,0,0,.16);
  border: 1px solid rgba(48,54,61,.75);
  border-radius: 8px;
}

.tp-pos-info {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.pos-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  flex-wrap: wrap;
}

.pos-meta {
  color: var(--q-muted);
}

.mono    { font-family: var(--q-font-mono); font-variant-numeric: tabular-nums; }
.fw-600  { font-weight: 600; }
.c-green { color: var(--q-green); }
.c-red   { color: var(--q-red); }
.c-muted { color: var(--q-muted); }

@media (max-width: 1120px) {
  .tp-body {
    grid-template-columns: 1fr;
  }

  .tp-form {
    border-right: 0;
    border-bottom: 1px solid var(--q-border);
  }
}

@media (max-width: 760px) {
  .tp-grid,
  .tp-preview {
    grid-template-columns: 1fr;
  }

  .preview-grid {
    grid-template-columns: repeat(2, max-content minmax(54px, 1fr));
  }

  .tp-actions,
  .tp-statusbar {
    align-items: stretch;
    flex-direction: column;
  }

  .status-left,
  .status-right,
  .tp-actions > * {
    width: 100%;
  }

  .tp-label {
    width: 38px;
  }
}
.tp-compact { border-radius: 5px; min-height: 100%; }
.tp-compact .tp-statusbar { padding: 12px; gap: 10px; align-items: flex-start; }
.tp-compact .status-left { font-size: 14px; padding: 2px 0; }
.tp-compact .status-right { flex-wrap: wrap; width: 100%; gap: 8px; }
.tp-compact .status-right > .el-button { margin-left: auto; }
.tp-compact .validation-pill { border: 0; border-radius: 3px; font-size: 10px; padding: 2px 5px; white-space: normal; overflow-wrap: anywhere; }
.tp-compact .tp-body { display: flex; flex-direction: column; }
.tp-compact .tp-form { padding: 15px 14px; gap: 13px; border-right: 0; border-bottom: 1px solid var(--q-border); }
.tp-compact .tp-grid { grid-template-columns: minmax(0, 1fr); gap: 13px; }
.tp-compact .tp-label { width: 30px; text-align: left; font-size: 11px; }
.tp-compact .tp-form-head { margin-bottom: 2px; }
.tp-compact .tp-form-head > .c-muted { font-size: 10px; }
.tp-compact :deep(.el-radio-button__inner) { padding: 8px 9px; }
.tp-compact .tp-input:deep(.el-radio-button) { flex: 1; }
.tp-compact .tp-input:deep(.el-radio-button__inner) { width: 100%; }
.tp-compact .direction-group:deep(.el-radio-button:first-child.is-active .el-radio-button__inner) { background: #173c32; border-color: var(--q-green); box-shadow: none; }
.tp-compact .direction-group:deep(.el-radio-button:last-child.is-active .el-radio-button__inner) { background: #472526; border-color: var(--q-red); box-shadow: none; }
.tp-compact .tp-input:deep(.el-select__wrapper), .tp-compact .tp-input:deep(.el-input__wrapper) { min-height: 32px; }
.tp-compact .tp-vol-group { gap: 4px; }.tp-compact .tp-vol-group .el-button { margin-left: 0; padding: 5px 8px; }
.tp-compact .volume-input { width: 85px; }.tp-compact .volume-input:deep(.el-input__wrapper) { padding-left: 4px; }
.tp-compact .tp-preview { grid-template-columns: minmax(0, 1fr); padding: 11px; gap: 10px; border-radius: 4px; }
.tp-compact .preview-grid { grid-template-columns: repeat(3, auto 1fr); gap: 4px 6px; font-size: 10px; }
.tp-compact .preview-grid strong { font: 600 12px var(--q-font-mono); white-space: nowrap; }
.tp-compact .tp-actions { display: grid; grid-template-columns: 1fr; gap: 10px; }
.tp-compact .tp-actions > .el-button { margin: 0; height: 35px; }
.tp-compact .tp-actions > .submit-btn { height: 40px; font-size: 14px; font-weight: 600; }
.tp-compact .margin-status { padding: 8px 10px; border-radius: 3px; }
.tp-compact .margin-status:deep(.el-alert__title) { font-size: 10px; line-height: 1.6; overflow-wrap: anywhere; }
.tp-compact .tp-positions { padding: 15px 14px; gap: 12px; }
.tp-compact .tp-empty { min-height: 72px; }
.tp-compact .tp-pos-list { max-height: 230px; }
.tp-compact .tp-pos-item { padding: 8px; border-radius: 4px; }
.tp-compact .pos-line { font-size: 11px; gap: 6px; }
.tp-compact .status-right .c-muted { font-size: 10px; }
</style>
