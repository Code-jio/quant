<script setup>
import { computed } from 'vue'
import { useDashboardWs } from '@/composables/useDashboardWs.js'

defineEmits(['details'])
const { connected, data } = useDashboardWs()
const hasSnapshot = computed(() => Boolean(data.timestamp))
const number = (value, digits = 2) => hasSnapshot.value && value != null && Number.isFinite(Number(value))
  ? Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—'
const metrics = computed(() => [
  { label: '账户余额', value: number(data.balance) },
  { label: '可用资金', value: number(data.available), primary: true },
  { label: '占用保证金', value: number(data.margin) },
  { label: '浮动盈亏', value: number(data.totalPnl), tone: data.totalPnl > 0 ? 'positive' : data.totalPnl < 0 ? 'negative' : '' },
  { label: '仓位暴露', value: hasSnapshot.value ? `${number(data.exposurePct)}%` : '—' },
])
</script>

<template>
  <section class="account-strip" aria-label="账户资金概览">
    <div class="account-identity">
      <div><span class="account-dot" :class="{ online: connected && hasSnapshot }" />{{ hasSnapshot ? data.accountId : '等待账户快照' }}</div>
      <span>{{ connected ? (hasSnapshot ? '账户推送已连接' : '账户数据同步中') : (hasSnapshot ? '连接中断 · 保留上次快照' : '账户推送未连接') }}</span>
    </div>
    <div v-for="metric in metrics" :key="metric.label" class="account-metric" :class="{ primary: metric.primary }">
      <span>{{ metric.label }}<small v-if="metric.label !== '仓位暴露'"> / 元</small></span>
      <strong :class="metric.tone">{{ metric.value }}</strong>
    </div>
    <button class="account-details" @click="$emit('details')">资金分析 <span aria-hidden="true">↗</span></button>
  </section>
</template>

<style scoped>
.account-strip { display: grid; grid-template-columns: 210px repeat(5, minmax(0, 1fr)) auto; align-items: center; min-height: 84px; padding: 12px 18px; border-bottom: 1px solid var(--q-border); gap: 20px; background: var(--q-panel); flex-shrink: 0; }
.account-identity { min-width: 0; font: 13px var(--q-font-mono); }.account-identity > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-identity > span { display: block; color: var(--q-muted); font: 11px 'Microsoft YaHei', sans-serif; margin-top: 9px; }
.account-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--q-yellow); margin-right: 7px; }.account-dot.online { background: var(--q-green); }
.account-metric { border-left: 1px solid var(--q-border); padding-left: 20px; min-width: 0; }.account-metric > span { color: var(--q-muted); font-size: 11px; }.account-metric small { font-size: 10px; }
.account-metric strong { display: block; margin-top: 8px; font: 600 clamp(16px, 1.25vw, 23px) var(--q-font-mono); font-variant-numeric: tabular-nums; white-space: nowrap; }.account-metric.primary strong { color: var(--q-blue); }
.positive { color: var(--q-green); }.negative { color: var(--q-red); }
.account-details { background: none; border: 0; color: var(--q-muted); font-size: 11px; cursor: pointer; white-space: nowrap; padding: 8px; }.account-details:hover { color: var(--q-blue); }
button:focus-visible { outline: 2px solid var(--q-blue); outline-offset: 2px; }
@media (max-width: 1500px) { .account-strip { grid-template-columns: 172px repeat(5, minmax(0, 1fr)); gap: 12px; padding: 10px 12px; min-height: 78px; }.account-metric { padding-left: 12px; }.account-details { display: none; } }
@media (max-width: 1000px) { .account-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px 8px; }.account-identity { padding-left: 12px; }.account-metric strong { font-size: 17px; } }
@media (max-width: 440px) { .account-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
