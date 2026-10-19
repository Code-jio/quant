<template>
  <el-dialog v-model="open" title="下载历史行情" width="min(680px, 94vw)" class="history-download"
    :close-on-click-modal="!loading" :close-on-press-escape="!loading" :show-close="!loading" @open="initialize">
    <div class="history-intro">
      <strong>天勤 EDB · 免费历史行情</strong>
      <p>无需天勤账号。日线按服务已有历史提供；1 分钟线支持最近一年，单次最多 31 个自然日，可分段下载。</p>
      <p>下载至北京时间昨日。数据可能延迟更新，实际覆盖以返回结果为准；仅支持国内期货月份合约。</p>
      <a href="https://doc.shinnytech.com/edb/latest/md_server.html" target="_blank" rel="noopener noreferrer">查看数据服务说明</a>
    </div>
    <el-form label-position="top" :disabled="loading || initializing" @submit.prevent="download">
      <div class="history-fields">
        <el-form-item label="交易所">
          <el-select v-model="form.exchange" aria-label="历史数据交易所" placeholder="选择交易所">
            <el-option v-for="(name, key) in exchanges" :key="key" :value="key" :label="name" />
          </el-select>
        </el-form-item>
        <el-form-item label="月份合约">
          <el-input v-model="form.symbol" aria-label="历史数据合约" placeholder="例如 au2612 或 SHFE.au2612" maxlength="32" />
        </el-form-item>
        <el-form-item label="周期">
          <el-select v-model="form.timeframe" aria-label="历史数据周期">
            <el-option label="日线" value="1d" /><el-option label="1 分钟" value="1m" />
          </el-select>
        </el-form-item>
        <el-form-item label="开始日期（北京时间）">
          <el-date-picker v-model="form.start_date" type="date" value-format="YYYY-MM-DD" aria-label="历史数据开始日期" :disabled-date="disabledDate" />
        </el-form-item>
        <el-form-item label="结束日期（北京时间）">
          <el-date-picker v-model="form.end_date" type="date" value-format="YYYY-MM-DD" aria-label="历史数据结束日期" :disabled-date="disabledDate" />
        </el-form-item>
      </div>
    </el-form>
    <p class="history-note">导入后会更新本地同合约、同周期、同时间的记录，并保存 EDB 来源。不会生成模拟数据。</p>
    <div ref="feedback">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="loading" title="正在下载并校验历史行情，请保持此窗口打开…" type="info" :closable="false" />
    <div v-if="result" class="history-receipt" role="status">
      <strong>{{ result.status === 'imported' ? `已导入 ${result.rows} 根 K 线` : '该范围没有数据' }}</strong>
      <p>{{ result.vendor_symbol }} · {{ result.timeframe === '1m' ? '1 分钟' : '日线' }} · 天勤 EDB</p>
      <p v-if="result.rows">实际范围：{{ result.first_datetime?.replace('T', ' ') }} ～ {{ result.last_datetime?.replace('T', ' ') }}</p>
      <p>{{ result.message }}</p>
      <p v-if="result.rows > 10000">当前回测单次最多 10000 根 K 线，运行前请缩短回测日期范围。</p>
    </div>
    </div>
    <template #footer>
      <el-button :disabled="loading" @click="open = false">关闭</el-button>
      <el-button v-if="result?.status === 'imported'" :disabled="loading" @click="apply">{{ actionLabel }}</el-button>
      <el-button type="primary" :loading="loading" :disabled="initializing || !limits" @click="download">下载并保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref, watch, nextTick, onUnmounted } from 'vue'
import { fetchEdbCapabilities, importEdbHistory } from '@/api/index.js'

const open = defineModel({ type: Boolean, default: false })
const props = defineProps({ symbol: { type: String, default: '' }, exchange: { type: String, default: '' },
  timeframe: { type: String, default: '1d' }, startDate: { type: String, default: '' },
  endDate: { type: String, default: '' }, actionLabel: { type: String, default: '查看 K 线' } })
const emit = defineEmits(['use'])
const exchanges = { SHFE: '上期所 SHFE', DCE: '大商所 DCE', CZCE: '郑商所 CZCE', CFFEX: '中金所 CFFEX', INE: '上期能源 INE', GFEX: '广期所 GFEX' }
const form = reactive({ exchange: '', symbol: '', timeframe: '1d', start_date: '', end_date: '' })
const loading = ref(false), initializing = ref(false), error = ref(''), result = ref(null), limits = ref(null)
const feedback = ref(null)
watch([error, result, loading], async values => {
  if (!values.some(Boolean)) return
  await nextTick()
  feedback.value?.scrollIntoView({ block: 'nearest' })
})
let controller, generation = 0
onUnmounted(() => { generation++; controller?.abort() })

async function initialize() {
  const epoch = ++generation
  controller?.abort(); controller = new AbortController()
  error.value = ''; result.value = null; limits.value = null; initializing.value = true
  try {
    const data = await fetchEdbCapabilities(controller.signal)
    if (epoch !== generation) return
    limits.value = data
    const timeframe = props.timeframe === '1m' ? '1m' : '1d'
    const end = props.endDate && props.endDate <= data.max_end_date ? props.endDate : data.max_end_date
    const start = new Date(`${end}T00:00:00Z`)
    start.setUTCDate(start.getUTCDate() - (timeframe === '1m' ? 6 : 29))
    const prefix = props.symbol.includes('.') ? props.symbol.split('.')[0] : ''
    Object.assign(form, { exchange: props.exchange || (exchanges[prefix] ? prefix : ''),
      symbol: props.symbol, timeframe, start_date: props.startDate || start.toISOString().slice(0, 10), end_date: end })
  } catch (e) { if (epoch === generation) error.value = e.status === 404
    ? '后端尚未提供 EDB 接口，请确认已重启并加载新版后端' : e.message }
  finally { if (epoch === generation) initializing.value = false }
}

function disabledDate(value) {
  if (!limits.value) return false
  const day = `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
  return day > limits.value.max_end_date || (form.timeframe === '1m' && day < limits.value.minute_start_date)
}

async function download() {
  if (loading.value || !limits.value) return
  error.value = ''; result.value = null
  if (!form.exchange || !form.symbol.trim() || !form.start_date || !form.end_date) {
    error.value = '请填写交易所、月份合约和起止日期'; return
  }
  if (form.start_date > form.end_date) { error.value = '结束日期不能早于开始日期'; return }
  const days = (Date.parse(form.end_date) - Date.parse(form.start_date)) / 86400000 + 1
  if (form.timeframe === '1m' && form.start_date < limits.value.minute_start_date) {
    error.value = '免费分钟线仅支持最近一年，请调整开始日期'; return
  }
  const maxDays = form.timeframe === '1m' ? limits.value.minute_max_days : limits.value.daily_max_days
  if (days > maxDays) { error.value = `单次最多下载 ${maxDays} 个自然日，请分段下载`; return }
  const epoch = generation
  loading.value = true
  try {
    const data = await importEdbHistory({ ...form }, controller.signal)
    if (epoch === generation) result.value = data
  } catch (e) { if (epoch === generation) error.value = e.message }
  finally { if (epoch === generation) loading.value = false }
}

function apply() {
  if (result.value?.status !== 'imported') return
  emit('use', result.value)
  open.value = false
}
</script>

<style scoped>
.history-intro { margin-bottom: 18px; padding: 14px; border: 1px solid var(--el-border-color); border-radius: 8px; }
.history-intro strong { color: var(--el-text-color-primary); }
.history-intro p, .history-note, .history-receipt p { font-size: 12px; line-height: 1.7; margin: 6px 0; overflow-wrap: anywhere; }
.history-intro a { color: var(--el-color-primary); font-size: 12px; }
.history-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.history-fields :deep(.el-date-editor) { width: 100%; }
.history-receipt { padding: 14px; margin-top: 14px; border: 1px solid var(--el-color-success); border-radius: 8px; }
.history-note { color: var(--el-text-color-secondary); margin-bottom: 12px; }
@media (max-width: 520px) { .history-fields { grid-template-columns: minmax(0, 1fr); } }
</style>

<style>
.history-download.el-dialog { margin-top: 4vh; max-height: 92dvh; display: flex; flex-direction: column; }
.history-download .el-dialog__body { min-height: 0; overflow-y: auto; }
.history-download .el-dialog__footer { flex-shrink: 0; padding-top: 16px; }
</style>
