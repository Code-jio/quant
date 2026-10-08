<script setup>
import { computed, watch, defineAsyncComponent } from 'vue'
import { useWatchWs } from '@/composables/useWatchWs.js'
import { useWatchStore } from '@/stores/watch.js'
import KlineSkeleton from '@/components/KlineSkeleton.vue'

const props = defineProps({ contract: { type: Object, default: null } })
defineEmits(['search'])
const KlineChart = defineAsyncComponent({ loader: () => import('@/components/KlineChart.vue'), loadingComponent: KlineSkeleton })
const store = useWatchStore()
const stream = useWatchWs()
const tick = computed(() => stream.getTick(props.contract?.symbol || ''))
watch(() => props.contract?.symbol, (current, previous) => {
  if (previous) stream.unsubscribe(previous, ['tick'])
  if (current) stream.subscribe(current, ['tick'])
}, { immediate: true })
const price = value => Number(value) > 0 ? String(value) : '—'
</script>

<template>
  <div class="trading-market">
    <div class="market-heading">
      <button class="market-contract" @click="$emit('search')"><strong>{{ contract?.symbol || '选择交易合约' }}</strong><span>{{ contract?.name || '搜索代码或名称' }}</span><el-icon><ArrowDown /></el-icon></button>
      <button v-if="contract" class="market-star" :aria-label="store.isWatched(contract.symbol) ? '移出自选' : '加入自选'" :aria-pressed="store.isWatched(contract.symbol)" @click="store.toggleWatchList(contract)"><el-icon><StarFilled v-if="store.isWatched(contract.symbol)" /><Star v-else /></el-icon></button>
      <span class="market-exchange">{{ contract?.exchange || '—' }}</span>
      <span class="market-channel" :class="{ connected: stream.connected.value }">{{ stream.connected.value ? '行情通道已连接' : '行情通道未连接' }}</span>
    </div>
    <div class="market-quote" aria-label="当前合约报价快照">
      <div class="last-quote"><span>最新价 · 快照</span><strong>{{ price(tick.last) }}</strong></div>
      <div><span>买一 / 量</span><strong>{{ price(tick.bid1) }} <small>/ {{ tick.bid1 > 0 ? tick.bid1Vol : '—' }}</small></strong></div>
      <div><span>卖一 / 量</span><strong>{{ price(tick.ask1) }} <small>/ {{ tick.ask1 > 0 ? tick.ask1Vol : '—' }}</small></strong></div>
      <div class="quote-range"><span>最高 / 最低</span><strong>{{ price(tick.high) }} <small>/ {{ price(tick.low) }}</small></strong></div>
      <div class="quote-time"><span>行情时间</span><strong>{{ tick.time || '等待报价' }}</strong></div>
    </div>
    <div v-if="!contract" class="market-empty"><el-icon><TrendCharts /></el-icon><h1>选择合约，开始交易</h1><p>查看行情、填写委托、跟踪成交，都在这一屏。</p><button @click="$emit('search')">搜索合约 <kbd>Ctrl K</kbd></button></div>
    <KlineChart v-else :symbol="contract.symbol" :name="contract.name || ''" :default-limit="500" />
  </div>
</template>

<style scoped>
.trading-market { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.market-heading { display: flex; align-items: center; min-height: 47px; padding: 8px 15px; gap: 12px; border-bottom: 1px solid var(--q-border); flex-wrap: wrap; }
.market-contract { display: flex; align-items: center; gap: 10px; color: var(--q-text); background: none; border: 0; cursor: pointer; padding: 0; }.market-contract strong { font: 600 18px var(--q-font-mono); }.market-contract span { font-size: 12px; color: var(--q-muted); }
.market-star { border: 0; background: none; color: var(--q-muted); cursor: pointer; display: flex; padding: 4px; }.market-star[aria-pressed=true] { color: var(--q-yellow); }
.market-exchange { color: var(--q-muted); font: 10px var(--q-font-mono); border: 1px solid var(--q-border); border-radius: 3px; padding: 3px 5px; }
.market-channel { margin-left: auto; color: var(--q-yellow); font-size: 10px; }.market-channel.connected { color: var(--q-muted); }
.market-quote { display: flex; align-items: center; gap: 24px; min-height: 69px; padding: 10px 15px; border-bottom: 1px solid var(--q-border); overflow-x: auto; flex-shrink: 0; }.market-quote > div { flex-shrink: 0; }.market-quote span { color: var(--q-muted); font-size: 10px; }
.market-quote strong { display: block; margin-top: 6px; font: 600 14px var(--q-font-mono); white-space: nowrap; }.market-quote small { color: var(--q-muted); font-size: 11px; font-weight: 400; }.last-quote strong { font-size: 24px; color: var(--q-text); }.quote-time { margin-left: auto; }.quote-time strong { font-size: 11px; font-weight: 400; color: var(--q-muted); }
.trading-market > :deep(.kline-wrap) { flex: 1; min-height: 0; height: auto; border: 0; border-radius: 0; }.trading-market :deep(.kline-source) { padding: 5px 12px; }
.trading-market :deep(.contract-tag), .trading-market :deep(.symbol-tag) { display: none; }
.market-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px; text-align: center; }.market-empty > .el-icon { font-size: 36px; color: var(--q-blue); margin-bottom: 12px; }
.market-empty h1 { font-size: 19px; font-weight: 500; margin: 5px 0; }.market-empty p { color: var(--q-muted); font-size: 12px; margin: 8px 0 24px; }.market-empty button { border: 1px solid var(--q-border); background: var(--q-panel); padding: 9px 15px; color: var(--q-blue); cursor: pointer; border-radius: 4px; } kbd { margin-left: 16px; color: var(--q-muted); font-size: 10px; }
button:focus-visible { outline: 2px solid var(--q-blue); outline-offset: 3px; }
@media (max-width: 1500px) { .market-quote { gap: 16px; min-height: 58px; padding: 7px 12px; }.quote-range { display: none; }.trading-market :deep(.custom-period), .trading-market :deep(.bar-count) { display: none; } }
@media (max-width: 700px) { .market-heading { gap: 8px; padding: 8px 10px; }.market-channel { font-size: 9px; }.market-quote { padding: 8px 10px; gap: 16px; }.quote-time { display: none; } }
</style>
