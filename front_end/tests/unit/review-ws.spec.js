import {effectScope,defineComponent} from 'vue'
import {mount,flushPromises} from '@vue/test-utils'
import {vi,test,expect,beforeEach,afterEach} from 'vitest'
const {snapshot}=vi.hoisted(()=>({snapshot:vi.fn()}))
vi.mock('@/api/index.js',()=>({fetchTradingSnapshot:snapshot}))
let sockets=[]
class FakeWebSocket {
  static OPEN=1
  static CONNECTING=0
  constructor(url) { this.url=url;this.readyState=0;this.sent=[];sockets.push(this) }
  send(data) { this.sent.push(data) }
  close() { this.readyState=3;this.onclose?.({code:1000}) }
  open() { this.readyState=1;this.onopen?.() }
}
beforeEach(()=>{vi.resetModules();sockets=[];sessionStorage.clear();vi.stubGlobal('WebSocket',FakeWebSocket);snapshot.mockReset()})
afterEach(()=>vi.unstubAllGlobals())

test('watch import creates no sockets; consumer release preserves shared subscription',async()=>{
  const {useWatchWs}=await import('@/composables/useWatchWs.js')
  expect(sockets).toHaveLength(0)
  sessionStorage.setItem('quant_session_active','1')
  const one=effectScope(),two=effectScope()
  const a=one.run(useWatchWs),b=two.run(useWatchWs)
  expect(sockets).toHaveLength(1);sockets[0].open()
  a.subscribe('rb',['tick']);b.subscribe('rb',['tick'])
  a.unsubscribe('rb')
  expect(a.subscribedSymbols.value).toEqual(['rb'])
  one.stop();expect(sockets[0].readyState).toBe(1)
  two.stop();expect(sockets[0].readyState).toBe(3)
})

test('unauthenticated watch consumers do not reconnect',async()=>{
  const {useWatchWs}=await import('@/composables/useWatchWs.js')
  const scope=effectScope();const watch=scope.run(useWatchWs)
  watch.reconnect();expect(sockets).toHaveLength(0)
  scope.stop()
})

test('failed account refresh retains the last snapshot and displays stale state',async()=>{
  const {useOrderBookWs}=await import('@/composables/useOrderBookWs.js')
  sessionStorage.setItem('quant_session_active','1')
  snapshot.mockResolvedValueOnce({revision:1,orders:[],trades:[],positions:[{symbol:'rb',volume:2}]})
  let book
  const wrapper=mount(defineComponent({setup(){book=useOrderBookWs();return()=>null}}))
  await flushPromises()
  expect(book.positions[0].volume).toBe(2)
  snapshot.mockRejectedValueOnce(new Error('网络超时'))
  await book.reload()
  expect(book.positions[0].volume).toBe(2)
  expect(book.stale.value).toBe(true)
  expect(book.error.value).toBe('网络超时')
  wrapper.unmount()
})

test('order websocket reconnect requests a fresh snapshot',async()=>{
  const {useOrderBookWs}=await import('@/composables/useOrderBookWs.js')
  sessionStorage.setItem('quant_session_active','1')
  snapshot.mockResolvedValue({revision:1,orders:[],trades:[],positions:[]})
  const wrapper=mount(defineComponent({setup(){useOrderBookWs();return()=>null}}))
  await flushPromises();const count=snapshot.mock.calls.length
  sockets[0].open();await flushPromises()
  expect(snapshot.mock.calls.length).toBe(count+1)
  wrapper.unmount()
})

test('stream revisions cannot roll back positions and trade identity includes trading day',async()=>{
  const {useOrderBookWs}=await import('@/composables/useOrderBookWs.js')
  sessionStorage.setItem('quant_session_active','1')
  snapshot.mockResolvedValue({revision:1,orders:[],trades:[],positions:[]})
  let book
  const wrapper=mount(defineComponent({setup(){book=useOrderBookWs();return()=>null}}))
  await flushPromises()
  const send=(i,msg)=>sockets[i].onmessage({data:JSON.stringify(msg)})
  send(1,{type:'positions_update',revision:5,positions:[{symbol:'rb',volume:2}]})
  send(1,{type:'positions_update',revision:3,positions:[{symbol:'rb',volume:1}]})
  expect(book.positions[0].volume).toBe(2)
  send(0,{type:'trade_event',revision:6,trade_id:'T1',trading_day:'20261008'})
  send(0,{type:'trade_event',revision:7,trade_id:'T1',trading_day:'20261009'})
  expect(book.trades).toHaveLength(2)
  wrapper.unmount()
})
