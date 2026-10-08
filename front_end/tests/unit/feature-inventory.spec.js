import {beforeEach,afterEach,expect,test,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {defineComponent,effectScope} from 'vue'
import {mount} from '@vue/test-utils'
import {useWatchStore} from '@/stores/watch.js'
import {useHistoryStore} from '@/stores/history.js'
import {useIndicatorStore} from '@/stores/indicator.js'
import {useChartStore} from '@/stores/chart.js'
import {useHotkeys} from '@/composables/useHotkeys.js'

beforeEach(()=>{localStorage.clear();sessionStorage.clear();setActivePinia(createPinia())})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()})

test('watchlist, selection and interval persist with duplicate and capacity limits',()=>{
  const store=useWatchStore()
  for(let i=0;i<25;i++)store.addToWatchList({symbol:`FIX${i}`,name:'fixture'})
  store.addToWatchList({symbol:'FIX24'})
  expect(store.watchList).toHaveLength(20)
  store.setSymbol({symbol:'FIX24'});store.setInterval('5m')
  setActivePinia(createPinia())
  const restored=useWatchStore()
  expect(restored.currentSymbol.symbol).toBe('FIX24')
  expect(restored.currentInterval).toBe('5m')
  expect(restored.isWatched('FIX24')).toBe(true)
  restored.toggleWatchList({symbol:'FIX24'})
  expect(restored.isWatched('FIX24')).toBe(false)
})

test('history preserves the latest interval, visit counts, and explicit clear',()=>{
  const history=useHistoryStore()
  history.addVisit({symbol:'A'},'1m');history.addVisit({symbol:'B'},'1d');history.addVisit({symbol:'A'},'5m')
  expect(history.recentSymbols).toHaveLength(2)
  expect(history.mostVisited[0].symbol).toBe('A')
  expect(history.getLastInterval('A')).toBe('5m')
  setActivePinia(createPinia())
  const restored=useHistoryStore()
  expect(restored.visitCounts.A).toBe(2)
  restored.clearHistory()
  setActivePinia(createPinia())
  expect(useHistoryStore().recentSymbols).toEqual([])
})

test('historical chart cache separates periods and expires old data',()=>{
  vi.useFakeTimers();vi.setSystemTime(new Date('2026-10-08T08:00:00Z'))
  const store=useWatchStore()
  store.setCacheEntry('A','1d',{bars:[1]});store.setCacheEntry('A','1m',{bars:[2]})
  expect(store.getCacheEntry('A','1d').bars).toEqual([1])
  expect(store.getCacheEntry('A','1m').bars).toEqual([2])
  vi.advanceTimersByTime(15*60*1000+1)
  expect(store.getCacheEntry('A','1d')).toBeNull()
})

test('indicator and per-symbol style preferences survive recreation and reset',()=>{
  const indicators=useIndicatorStore(),chart=useChartStore()
  expect(indicators.addMa(60)).toBe(true)
  expect(indicators.addMa(60)).toBe(false)
  indicators.updateMa(60,{visible:false});indicators.setActiveIndicator('rsi')
  indicators.updateRsiParams({period:21})
  chart.setGlobalConfig({upColor:'#abcdef'});chart.setSymbolConfig('A',{upColor:'#123456'})
  setActivePinia(createPinia())
  const restored=useIndicatorStore(),styles=useChartStore()
  expect(restored.activeIndicator).toBe('rsi')
  expect(restored.rsiParams.period).toBe(21)
  expect(restored.maList.find(m=>m.n===60).visible).toBe(false)
  expect(styles.resolvedConfig('A').upColor).toBe('#123456')
  expect(styles.resolvedConfig('B').upColor).toBe('#abcdef')
  styles.resetSymbolConfig('A')
  expect(styles.resolvedConfig('A').upColor).toBe('#abcdef')
  restored.removeMa(60)
  expect(restored.maList.some(m=>m.n===60)).toBe(false)
})

test('chart hotkeys ignore typing and unregister when the component leaves',()=>{
  const handler=vi.fn()
  const wrapper=mount(defineComponent({setup(){useHotkeys([{key:'r',handler}]);return{}},template:'<input />'}),{attachTo:document.body})
  window.dispatchEvent(new KeyboardEvent('keydown',{key:'r'}));expect(handler).toHaveBeenCalledTimes(1)
  wrapper.find('input').element.focus()
  window.dispatchEvent(new KeyboardEvent('keydown',{key:'r'}));expect(handler).toHaveBeenCalledTimes(1)
  wrapper.unmount()
  window.dispatchEvent(new KeyboardEvent('keydown',{key:'r'}));expect(handler).toHaveBeenCalledTimes(1)
})

test.each([100,600])('tick alerts detect price, jump and volume at %i ms cadence, apply cooldown, and clear',async(cadence)=>{
  vi.useFakeTimers();vi.setSystemTime(new Date('2026-10-08T08:00:00Z'))
  vi.resetModules()
  let socket
  class OfflineSocket{
    static OPEN=1
    static CONNECTING=0
    constructor(){this.readyState=0;socket=this}
    send(){}
    close(){this.readyState=3;this.onclose?.({code:1000})}
  }
  vi.stubGlobal('WebSocket',OfflineSocket)
  sessionStorage.setItem('quant_session_active','1')
  const {useWatchWs}=await import('@/composables/useWatchWs.js')
  const scope=effectScope(),watch=scope.run(useWatchWs)
  try{
    socket.readyState=1;socket.onopen()
    watch.subscribe('FIX',['tick'])
    const tick=(last,volume,change_rate)=>{socket.onmessage({data:JSON.stringify({type:'tick',symbol:'FIX',last,volume,change_rate})});vi.advanceTimersByTime(cadence)}
    for(let i=0;i<5;i++)tick(100,10,0)
    tick(104,100,4)
    expect(watch.alerts.map(a=>a.type)).toEqual(expect.arrayContaining(['price','price_jump','volume']))
    const count=watch.alerts.length
    tick(108,200,8)
    expect(watch.alerts).toHaveLength(count)
    watch.markAlertsRead();expect(watch.unreadCount.value).toBe(0)
    watch.clearAlerts();expect(watch.alerts).toHaveLength(0)
  }finally{scope.stop()}
})
