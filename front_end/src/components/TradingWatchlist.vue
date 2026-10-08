<script setup>
import { computed, ref, watch } from 'vue'
import { useWatchStore } from '@/stores/watch.js'
import { useHistoryStore } from '@/stores/history.js'
import { useWatchWs } from '@/composables/useWatchWs.js'

defineEmits(['select', 'search'])
const store = useWatchStore()
const history = useHistoryStore()
const stream = useWatchWs()
const tab = ref('watch')
const contracts = computed(() => tab.value === 'watch' ? store.watchList : history.recentSymbols)
const symbols = computed(() => contracts.value.map(contract => contract.symbol))
watch(symbols, (current, previous = []) => {
  for (const symbol of previous) if (!current.includes(symbol)) stream.unsubscribe(symbol, ['tick'])
  if (current.length) stream.subscribe(current, ['tick'])
}, { immediate: true })
const quote = symbol => stream.getTick(symbol)
const alerts = computed(() => stream.alerts.slice(0, 5))
</script>

<template>
  <div class="trading-watchlist">
    <div class="watchlist-heading"><span>合约列表</span><button aria-label="搜索并添加合约" @click="$emit('search')">＋</button></div>
    <div class="watchlist-tabs" aria-label="合约列表分类">
      <button :aria-pressed="tab === 'watch'" @click="tab = 'watch'">自选 <span>{{ store.watchList.length }}</span></button>
      <button :aria-pressed="tab === 'recent'" @click="tab = 'recent'">最近</button>
    </div>
    <div class="watchlist-columns"><span>合约 / 名称</span><span>最新</span></div>
    <div class="watchlist-rows">
      <button v-for="item in contracts" :key="item.symbol" class="watchlist-row" :class="{ selected: store.currentSymbol?.symbol === item.symbol }" :aria-pressed="store.currentSymbol?.symbol === item.symbol" @click="$emit('select', item)">
        <span><strong>{{ item.symbol }}</strong><small>{{ item.name || item.exchange || '—' }}</small></span>
        <span class="watchlist-price">{{ quote(item.symbol).last > 0 ? quote(item.symbol).last : '—' }}<small>{{ quote(item.symbol).last > 0 ? '报价快照' : '等待报价' }}</small></span>
      </button>
      <div v-if="!contracts.length" class="watchlist-empty"><el-icon><Star /></el-icon><span>{{ tab === 'watch' ? '把常用合约放在这里' : '暂无最近查看的合约' }}</span><button @click="$emit('search')">搜索合约</button></div>
    </div>
    <section class="watchlist-alerts" aria-label="行情提醒">
      <div class="watchlist-heading"><span>行情提醒</span><span class="alert-count">{{ stream.alerts.length }}</span></div>
      <p v-if="!alerts.length" class="alert-empty">暂无价格或成交量提醒</p>
      <div v-for="alert in alerts" :key="alert.id" class="desk-alert"><span>{{ alert.symbol }} · {{ alert.time }}</span><p>{{ alert.message }}</p></div>
      <button v-if="alerts.length" class="clear-alerts" @click="stream.clearAlerts()">清空提醒</button>
    </section>
    <div class="watchlist-note">选择合约后，行情与委托录入同步切换</div>
  </div>
</template>

<style scoped>
.trading-watchlist { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.watchlist-heading { display: flex; align-items: center; justify-content: space-between; height: 42px; padding: 0 12px; font-size: 12px; font-weight: 600; flex-shrink: 0; }
button { cursor: pointer; font-family: inherit; }.watchlist-heading button { background: none; border: 0; color: var(--q-muted); font-size: 21px; padding: 2px 4px; }
.watchlist-tabs { display: flex; padding: 0 10px; gap: 12px; border-bottom: 1px solid var(--q-border); }.watchlist-tabs button { background: none; color: var(--q-muted); border: 0; border-bottom: 2px solid transparent; padding: 7px 3px 10px; font-size: 12px; }.watchlist-tabs button[aria-pressed=true] { color: var(--q-blue); border-color: var(--q-blue); }.watchlist-tabs span { margin-left: 6px; font: 11px var(--q-font-mono); }
.watchlist-columns { display: flex; justify-content: space-between; padding: 11px 12px 6px; font-size: 10px; color: var(--q-muted); }.watchlist-rows { flex: 1; min-height: 100px; overflow: auto; }
.watchlist-row { display: flex; justify-content: space-between; align-items: center; gap: 6px; padding: 13px 11px; width: 100%; border: 0; border-left: 2px solid transparent; border-bottom: 1px solid var(--q-border); background: none; color: var(--q-text); text-align: left; }.watchlist-row.selected { border-left-color: var(--q-blue); background: rgb(88 166 255 / 9%); }.watchlist-row:hover { background: rgb(88 166 255 / 6%); }
.watchlist-row strong { font: 600 13px var(--q-font-mono); }.watchlist-row small { display: block; color: var(--q-muted); font: 10px 'Microsoft YaHei', sans-serif; margin-top: 7px; overflow-wrap: anywhere; }.watchlist-price { font: 13px var(--q-font-mono); text-align: right; }
.watchlist-empty { display: flex; align-items: center; flex-direction: column; gap: 13px; padding: 30px 10px; color: var(--q-muted); font-size: 11px; text-align: center; }.watchlist-empty .el-icon { font-size: 22px; }
.watchlist-empty button, .clear-alerts { border: 1px solid var(--q-border); background: transparent; color: var(--q-blue); padding: 6px 10px; border-radius: 4px; font-size: 11px; }
.watchlist-alerts { border-top: 1px solid var(--q-border); max-height: 34%; min-height: 110px; overflow: auto; }.alert-empty { color: var(--q-muted); font-size: 11px; padding: 0 12px; }.alert-count { color: var(--q-muted); font: 11px var(--q-font-mono); }
.desk-alert { padding: 9px 12px; border-top: 1px solid var(--q-border); font-size: 11px; }.desk-alert span { color: var(--q-muted); }.desk-alert p { margin: 6px 0 0; color: var(--q-yellow); line-height: 1.6; }.clear-alerts { margin: 6px 12px; }
.watchlist-note { border-top: 1px solid var(--q-border); padding: 12px; color: var(--q-muted); font-size: 10px; line-height: 1.8; }
button:focus-visible { outline: 2px solid var(--q-blue); outline-offset: -3px; }
</style>
