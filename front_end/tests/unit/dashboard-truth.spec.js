import {defineComponent} from 'vue'
import {mount} from '@vue/test-utils'
import {afterEach,beforeEach,expect,test,vi} from 'vitest'
import {formatAmount,formatSigned,formatPercent} from '@/utils/financialData.js'

let sockets=[]
class OfflineSocket {
  static OPEN=1
  constructor(){this.readyState=1;sockets.push(this)}
  send(){}
  close(){}
}
beforeEach(()=>{
  sockets=[]
  sessionStorage.setItem('quant_session_active','1')
  vi.stubGlobal('WebSocket',OfflineSocket)
})
afterEach(()=>vi.unstubAllGlobals())

test('formatters distinguish zero from unknown and invalid amounts',()=>{
  for(const value of [null,undefined,NaN,Infinity,'',false]) {
    expect(formatAmount(value)).toBe('—')
    expect(formatSigned(value)).toBe('—')
    expect(formatPercent(value)).toBe('—')
  }
  expect(formatAmount(0)).toBe('0.00')
  expect(formatSigned(-300)).toBe('-300.00')
  expect(formatPercent(0)).toBe('0.00%')
})

test.each(['Dashboard','System'])('%s snapshots clear stale values when null, missing or disconnected',async(channel)=>{
  const module=channel==='Dashboard' ? await import('@/composables/useDashboardWs.js') : await import('@/composables/useSystemWs.js')
  let state
  const wrapper=mount(defineComponent({setup(){state=module[`use${channel}Ws`]();return()=>null}}))
  try {
    const socket=sockets[0]
    expect(state.data.balance).toBeNull()
    expect(state.data.totalPnl).toBeNull()
    const send=payload=>socket.onmessage({data:JSON.stringify({type:channel==='Dashboard'?'dashboard_metrics':'system_status',...payload})})
    send({account_known:true,account_source:'gateway',balance:100000,total_pnl:0,position_pnl:-300})
    expect(state.data.totalPnl).toBe(0)
    expect(state.data.positionPnl).toBe(-300)
    expect(state.data.accountKnown).toBe(true)
    send({account_known:false,balance:null,total_pnl:null})
    expect(state.data.balance).toBeNull()
    expect(state.data.totalPnl).toBeNull()
    expect(state.data.positionPnl).toBeNull()
    expect(state.data.accountKnown).toBe(false)
    send({account_known:true,balance:100000,total_pnl:0})
    socket.onclose({code:1008})
    expect(state.data.balance).toBeNull()
    expect(state.data.accountKnown).toBe(false)
  } finally {wrapper.unmount()}
})
