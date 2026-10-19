<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchTradingReconcile, queryBrokerReconcile, importBrokerReconcile, resolveExecutionIntent,
  reviewConditionalOrder, emergencyStop } from '@/api/index.js'

const visible = ref(false), state = ref(null), busy = ref(false), error = ref(''), note = ref('')
const matches = ref({})
const directions = { long: '买入', short: '卖出' }
const offsets = { open: '开仓', close: '平仓', close_today: '平今', close_yesterday: '平昨' }
const statuses = { '0': '全部成交', '1': '部分成交', '2': '部分成交已撤', '3': '未成交挂单', '4': '未成交不挂单', '5': '已撤单' }
const conditionStates = { pending: '等待触发', paused: '等待复核', triggering: '正在发送', unknown: '发送结果未知', triggered: '已触发', cancelled: '已取消', rejected: '已拒绝', expired: '已过期' }
const conditionTypes = { stop_entry: '突破入场', limit_entry: '回落入场', stop_loss: '止损', take_profit: '止盈', trailing_stop: '移动止损' }
const broker = computed(() => state.value?.broker_snapshot || {})
const ready = computed(() => broker.value.status === 'ready' && state.value?.snapshot_ready)
let timer, version = 0
async function refresh() {
  const current = ++version
  try {
    const data = await fetchTradingReconcile()
    if (current !== version || !visible.value) return
    state.value = data; error.value = ''
  } catch (e) { if (current === version) { error.value = e.message; state.value = null } }
}
watch(visible, value => {
  version++; clearInterval(timer)
  if (value) { refresh(); timer = setInterval(refresh, 2000) }
})
onUnmounted(() => { version++; clearInterval(timer) })
async function act(operation) {
  busy.value = true
  try { await operation(); await refresh() } catch (e) { ElMessage.error(e.message) }
  finally { busy.value = false }
}
async function pause() {
  await act(() => emergencyStop({ cancel_orders: false, stop_strategies: false, reason: '人工对账暂停交易' }))
}
async function confirm(operation, text) {
  if (note.value.trim().length < 12) { ElMessage.warning('请填写至少 12 个字符的核对依据'); return }
  const body = { generation: state.value?.generation, snapshot_id: broker.value.id, note: note.value.trim() }
  try { await ElMessageBox.confirm(text, '确认人工核对', { type: 'warning', confirmButtonText: '确认记录', cancelButtonText: '返回核对' }) }
  catch { return }
  await act(() => operation(body))
}
function resolve(intent) {
  const orderId = matches.value[intent.id] || ''
  confirm(body => resolveExecutionIntent({ ...body, intent_id: intent.id, order_id: orderId }),
    `核对 ${intent.symbol} ${directions[intent.direction] || intent.direction} ${offsets[intent.offset] || intent.offset} ${intent.volume} 手 @ ${intent.price}。${orderId ? `关联柜台委托 ${orderId}` : '确认已通过柜台凭证核实未发送；仅查询不到记录不足以证明未发送'}。记录后仍保持急停。`)
}
function review(order, action) {
  confirm(body => reviewConditionalOrder(order.pre_order_id, { generation: body.generation, note: body.note, action }),
    `${action === 'resume' ? '重新启用后，行情满足条件会经风控提交委托' : '取消本地条件单'}：${order.symbol} ${order.volume} 手，触发价 ${order.trigger_price}。`)
}
</script>

<template>
  <el-button size="small" plain @click="visible = true">对账与恢复</el-button>
  <el-dialog v-model="visible" title="柜台对账与本地恢复" width="min(1000px, 94vw)" class="recovery-dialog">
    <p>柜台查询仅获取本交易日的委托与成交。核对不会自动重报、撤单或解除急停。历史未决记录需历史柜台凭证。</p>
    <p>本地条件单依赖服务持续运行；重启后暂停，发送结果未知的记录不可直接重新启用。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="recovery-actions">
      <el-button :loading="busy" @click="act(queryBrokerReconcile)">查询完整柜台快照</el-button>
      <el-button :disabled="busy || !state" @click="pause">暂停交易并核对（保留挂单）</el-button>
      <el-button :disabled="busy || !ready || !state?.risk?.emergency_stop"
        @click="confirm(importBrokerReconcile, '将核对后的柜台委托和成交同步到本地记录，保持急停，不重复执行策略回调。')">同步核对记录</el-button>
    </div>
    <p role="status">{{ broker.reason || '尚未查询柜台' }} · 本地差异 {{ state?.differences?.length ?? '未知' }} 项 · {{ state?.reconciled ? '本次快照核对一致' : '尚未完成核对' }}</p>
    <el-input v-model="note" type="textarea" :rows="2" maxlength="1000" placeholder="核对依据 / 柜台凭证编号（至少 12 个字符，请勿填写密码）" />
    <details v-if="broker.orders?.length"><summary>柜台委托（{{ broker.orders.length }}）</summary>
      <el-table :data="broker.orders" max-height="220">
        <el-table-column prop="order_id" label="柜台委托" /><el-table-column prop="InstrumentID" label="合约" />
        <el-table-column prop="LimitPrice" label="限价" /><el-table-column prop="VolumeTotalOriginal" label="手数" />
        <el-table-column prop="VolumeTraded" label="已成交" /><el-table-column label="柜台状态"><template #default="{row}">{{ row.OrderSubmitStatus === '4' ? '已拒绝' : (statuses[row.OrderStatus] || '未知') }}</template></el-table-column>
      </el-table>
    </details>
    <div v-for="intent in state?.pending_intents || []" :key="intent.id" class="recovery-item">
      <p>未决 {{ intent.symbol }} · {{ directions[intent.direction] }} / {{ offsets[intent.offset] }} · {{ intent.volume }} 手 @ {{ intent.price }}</p>
      <el-select v-model="matches[intent.id]" clearable placeholder="已通过柜台凭证确认未发送">
        <el-option v-for="row in (broker.orders || []).filter(row => row.InstrumentID === intent.symbol)" :key="row.order_id" :label="row.order_id" :value="row.order_id" />
      </el-select>
      <el-button :disabled="busy || !ready || !state.risk?.emergency_stop" @click="resolve(intent)">记录人工核对结果</el-button>
    </div>
    <div v-for="order in state?.conditional_orders || []" :key="order.pre_order_id" class="recovery-item">
      <p>本地条件单 {{ order.symbol }} · {{ conditionTypes[order.type] || order.type }} · {{ conditionStates[order.status] || order.status }} · {{ order.volume }} 手 · 触发价 {{ order.trigger_price }}</p>
      <el-button v-if="order.status === 'paused'" :disabled="busy" @click="review(order, 'resume')">复核后启用</el-button>
      <el-button v-if="['paused','pending'].includes(order.status)" :disabled="busy" @click="review(order, 'cancel')">取消本地条件单</el-button>
    </div>
  </el-dialog>
</template>

<style scoped>
.recovery-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.recovery-item { padding: 12px 0; border-bottom: 1px solid var(--el-border-color); }
p { line-height: 1.6; }
</style>
