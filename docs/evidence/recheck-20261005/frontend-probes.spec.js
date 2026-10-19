import {defineComponent} from 'vue'
import {mount,flushPromises} from '@vue/test-utils'
import {vi,test,expect,afterEach} from 'vitest'
import {computeIndicators} from '@/workers/indicatorMath.js'
const {snapshot}=vi.hoisted(()=>({snapshot:vi.fn()}))
vi.mock('@/api/index.js',()=>({fetchTradingSnapshot:snapshot}))

test('review evidence: identical closes have inconsistent server/client RSI',()=>{
  const closes=Array.from({length:30},(_,i)=>100+i)
  const rsi=computeIndicators('calc_rsi',{closes,period:14})
  console.log('RSI_UPTREND_FRONTEND',rsi.at(-1))
  expect(rsi.at(-1)).toBeGreaterThan(99.9)
  const mixed=[100,101,104,99,102,101,107,109,106,110,108,111,107,109,113,112,110,109,108,107]
  console.log('RSI_MIXED_FRONTEND',computeIndicators('calc_rsi',{closes:mixed,period:14}).at(-1))
})

afterEach(()=>vi.unstubAllGlobals())

test('review reproduction: refresh failure must not strand a filled-order event',async()=>{
  const sockets=[]
  class FakeWs {
    constructor(url){this.url=url;this.readyState=0;sockets.push(this)}
    close(){this.readyState=3;this.onclose?.({code:1000})}
    send(){}
  }
  sessionStorage.setItem('quant_session_active','1')
  vi.stubGlobal('WebSocket',FakeWs)
  const {useOrderBookWs}=await import('@/composables/useOrderBookWs.js')
  snapshot.mockResolvedValueOnce({revision:1,orders:[{order_id:'O1',status:'submitted',traded_volume:0}],trades:[],positions:[]})
  let book
  const wrapper=mount(defineComponent({setup(){book=useOrderBookWs();return()=>null}}))
  try {
    await flushPromises()
    let reject
    snapshot.mockImplementationOnce(()=>new Promise((_,r)=>{reject=r}))
    const pending=book.reload()
    sockets[0].onmessage({data:JSON.stringify({type:'order_update',revision:2,order_id:'O1',status:'filled',traded_volume:1})})
    reject(new Error('offline timeout'))
    await pending
    console.log('AFTER_FAILED_REFRESH',JSON.stringify({status:book.ordersArray.value[0].status,stale:book.stale.value}))
    // Desired invariant. This is intentionally a failing regression probe.
    expect(book.ordersArray.value[0].status).toBe('filled')
  } finally {wrapper.unmount()}
})
