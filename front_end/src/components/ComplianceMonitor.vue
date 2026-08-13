<template>
  <div class="compliance-monitor">
    <div class="summary-row">
      <div>
        <span class="summary-label">交易日</span>
        <strong>{{ compliance.trading_day || '—' }}</strong>
      </div>
      <div>
        <span class="summary-label">网关</span>
        <el-tag :type="gatewayTagType" size="small" effect="plain">
          {{ gatewayStatusText }}
        </el-tag>
      </div>
      <div class="refresh-state">
        <span :class="['refresh-dot', errorMessage ? 'is-error' : 'is-ok']"></span>
        {{ errorMessage || (loading ? '刷新中' : '每 2 秒刷新') }}
      </div>
      <el-button size="small" @click="openThresholdSettings">阈值设置</el-button>
    </div>

    <el-alert
      v-if="lastRejectReason"
      class="reject-alert"
      type="warning"
      :closable="false"
      show-icon
      :title="`最近一次风控拒绝：${lastRejectReason}`"
    />

    <div class="counter-grid">
      <div
        v-for="item in counterItems"
        :key="item.key"
        :class="['counter-card', item.state]"
      >
        <span class="counter-label">{{ item.label }}</span>
        <strong class="counter-value">{{ item.value }}</strong>
        <span class="counter-threshold">阈值：{{ item.thresholdText }}</span>
      </div>
    </div>

    <div class="alerts-block">
      <div class="block-title">
        阈值告警
        <el-tag v-if="alerts.length" type="danger" size="small">{{ alerts.length }}</el-tag>
      </div>
      <el-table v-if="alerts.length" :data="alerts" size="small" max-height="240">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
        </el-table-column>
        <el-table-column label="指标" min-width="130">
          <template #default="{ row }">{{ counterLabel(row.counter) }}</template>
        </el-table-column>
        <el-table-column label="当前/阈值" width="110">
          <template #default="{ row }">
            <span class="danger-text">{{ row.count }} / {{ row.threshold }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="说明" min-width="260" show-overflow-tooltip />
      </el-table>
      <el-empty v-else description="当前交易日暂无合规阈值告警" :image-size="48" />
    </div>

    <el-dialog v-model="thresholdSettingsVisible" title="合规阈值设置" width="520px" :close-on-click-modal="false">
      <p class="threshold-settings-hint">设置为 0 表示停用该项阈值告警。</p>
      <el-form label-position="top">
        <el-form-item v-for="field in thresholdSettingsFields" :key="field.key" :label="field.label">
          <el-input-number
            v-model="thresholdDraft[field.key]"
            :min="0"
            :step="1"
            :precision="0"
            controls-position="right"
          />
          <span class="threshold-disabled-text">{{ thresholdDraft[field.key] === 0 ? '已停用' : `阈值：${thresholdDraft[field.key]}` }}</span>
        </el-form-item>
      </el-form>
      <el-alert v-if="thresholdSaveError" type="error" :title="thresholdSaveError" :closable="false" show-icon />
      <template #footer>
        <el-button :disabled="thresholdSaving" @click="cancelThresholdSettings">取消</el-button>
        <el-button type="primary" :loading="thresholdSaving" @click="saveThresholdSettings">保存设置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchRiskStatus, updateRiskConfig } from '@/api/index.js'
import {
  createComplianceThresholdDraft,
  normalizeComplianceThresholdPatch,
} from '@/utils/complianceRisk.js'

const POLL_INTERVAL_MS = 2_000
const riskStatus = ref({})
const loading = ref(false)
const errorMessage = ref('')
const thresholdSettingsVisible = ref(false)
const thresholdSaving = ref(false)
const thresholdSaveError = ref('')
const thresholdDraft = ref(createComplianceThresholdDraft())
let timer = null

const risk = computed(() => riskStatus.value?.risk || {})
const compliance = computed(() => risk.value?.compliance || {})
const counters = computed(() => compliance.value?.counters || {})
const thresholds = computed(() => compliance.value?.thresholds || {})
const alerts = computed(() => [...(compliance.value?.alerts || [])].reverse())
const lastRejectReason = computed(() => riskStatus.value?.last_reject_reason || '')

const COUNTER_DEFINITIONS = [
  { key: 'orders_submitted', label: '已提交委托' },
  { key: 'cancel_requests', label: '撤单请求' },
  { key: 'cancels_accepted', label: '已受理撤单', thresholdKey: '' },
  { key: 'duplicate_open', label: '重复开仓' },
  { key: 'duplicate_close', label: '重复平仓' },
  { key: 'duplicate_cancel', label: '重复撤单' },
]

const thresholdSettingsFields = [
  { key: 'orders_submitted', label: '已提交委托' },
  { key: 'cancel_requests', label: '撤单请求' },
  { key: 'duplicate_open', label: '重复开仓' },
  { key: 'duplicate_close', label: '重复平仓' },
  { key: 'duplicate_cancel', label: '重复撤单' },
]

const counterItems = computed(() => COUNTER_DEFINITIONS.map(definition => {
  const value = Number(counters.value[definition.key] || 0)
  const thresholdKey = definition.thresholdKey === '' ? '' : definition.key
  const threshold = thresholdKey ? Number(thresholds.value[thresholdKey] || 0) : 0
  const isDuplicate = definition.key.startsWith('duplicate_')
  return {
    ...definition,
    value,
    thresholdText: threshold > 0 ? threshold : '未启用',
    state: value > 0 && (isDuplicate || (threshold > 0 && value >= threshold)) ? 'is-danger' : '',
  }
}))

const gatewayStatusText = computed(() => {
  const status = String(riskStatus.value?.gateway_status || 'stopped')
  return {
    trading: '交易中',
    connected: '已连接',
    connecting: '连接中',
    error: '异常',
    stopped: '未连接',
  }[status] || status
})

const gatewayTagType = computed(() => {
  const status = String(riskStatus.value?.gateway_status || 'stopped')
  if (status === 'trading' || status === 'connected') return 'success'
  if (status === 'connecting') return 'warning'
  return 'danger'
})

async function refresh() {
  if (loading.value) return
  loading.value = true
  try {
    riskStatus.value = await fetchRiskStatus({ redirectOn401: false }) || {}
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value = error?.message || '合规监控数据读取失败'
  } finally {
    loading.value = false
  }
}

function openThresholdSettings() {
  thresholdDraft.value = createComplianceThresholdDraft(thresholds.value)
  thresholdSaveError.value = ''
  thresholdSettingsVisible.value = true
}

function cancelThresholdSettings() {
  thresholdSettingsVisible.value = false
  thresholdSaveError.value = ''
}

async function saveThresholdSettings() {
  thresholdSaving.value = true
  thresholdSaveError.value = ''
  try {
    await updateRiskConfig(normalizeComplianceThresholdPatch(thresholdDraft.value))
    ElMessage.success('合规阈值设置已保存')
    thresholdSettingsVisible.value = false
    await refresh()
  } catch (error) {
    thresholdSaveError.value = error?.message || '合规阈值设置保存失败'
    ElMessage.error(thresholdSaveError.value)
  } finally {
    thresholdSaving.value = false
  }
}

function counterLabel(key) {
  return COUNTER_DEFINITIONS.find(item => item.key === key)?.label || key
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, POLL_INTERVAL_MS)
})

onUnmounted(() => {
  window.clearInterval(timer)
  timer = null
})
</script>

<style scoped>
.compliance-monitor { display: flex; flex-direction: column; gap: 12px; }
.summary-row {
  display: flex; align-items: center; flex-wrap: wrap; gap: 20px;
  padding: 12px 16px; border: 1px solid var(--border-color, #30363d);
  border-radius: 8px; background: var(--bg-base, #0d1117); font-size: 13px;
}
.summary-row > div { display: flex; align-items: center; gap: 8px; }
.summary-label { color: var(--text-muted, #6e7681); }
.refresh-state { margin-left: auto; color: var(--text-muted, #6e7681); font-size: 12px; }
.refresh-dot { width: 7px; height: 7px; border-radius: 50%; background: #3fb950; }
.refresh-dot.is-error { background: #f85149; }
.reject-alert { border: 1px solid #e3b34166; }
.counter-grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 10px; }
.counter-card {
  display: flex; flex-direction: column; gap: 5px; padding: 12px 14px;
  border-radius: 8px; border: 1px solid var(--border-color, #30363d);
  background: var(--bg-surface, #161b22);
}
.counter-card.is-danger { border-color: #f8514988; background: rgba(248, 81, 73, 0.06); }
.counter-label, .counter-threshold { color: var(--text-muted, #6e7681); font-size: 11px; }
.counter-value { color: var(--text-primary, #c9d1d9); font-size: 22px; font-variant-numeric: tabular-nums; }
.counter-card.is-danger .counter-value, .danger-text { color: #f85149; font-weight: 700; }
.alerts-block {
  border: 1px solid var(--border-color, #30363d); border-radius: 8px;
  background: var(--bg-base, #0d1117); padding: 12px;
}
.block-title { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 13px; font-weight: 600; }
.threshold-settings-hint { margin: 0 0 16px; color: var(--text-muted, #6e7681); font-size: 13px; }
.threshold-disabled-text { margin-left: 10px; color: var(--text-muted, #6e7681); font-size: 12px; }
@media (max-width: 1100px) { .counter-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 600px) {
  .counter-grid { grid-template-columns: repeat(2, 1fr); }
  .refresh-state { margin-left: 0; width: 100%; }
}
</style>
