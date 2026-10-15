import { mount, flushPromises } from '@vue/test-utils'
import { expect, test, vi, afterEach } from 'vitest'
import { searchContracts } from '@/api/index.js'
import { useContractSearch } from '@/composables/useContractSearch.js'

const future = (symbol, exchange = 'SHFE', extra = {}) => ({ symbol, exchange, product: 'FUTURES', source: 'ctp', ...extra })
const good = [future('au2612'), future('ad2612'), future('bz2612','DCE'), future('SF612','CZCE'),
  future('IF2612','CFFEX'), future('TL2612','CFFEX'), future('sc2612','INE'), future('pt2612','GFEX')]
const bad = [future('AF2610','CFFEX'), future('GC2612','COMEX'), future('AP612C6000','CZCE',{product:'OPTION'}),
  future('au8888'), future('au2612','SHFE',{tradable:false}), future('au2612','SHFE',{is_trading:false}),
  future('au2612','SHFE',{source:'historical'}), {symbol:'au2612',exchange:'SHFE'},
  future('SP au2612&au2702','SHFE',{product:'SPREAD'})]
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear() })

test('all search consumers receive domestic futures even from an older unfiltered backend', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({code:0,data:[...bad,...good],total:17}))))
  const result = await searchContracts({query:'黄金'})
  expect(result.data.map(row=>row.symbol)).toEqual(good.map(row=>row.symbol))
  expect(result.total).toBe(good.length)
})

test('saved foreign, option, and simulation choices are hidden without deleting user storage', async () => {
  const saved = JSON.stringify([...bad,...good])
  localStorage.setItem('quant_contract_favorites',saved)
  localStorage.setItem('quant_contract_recent',saved)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({code:0,data:[]}))))
  let search
  const wrapper = mount({ setup() { search=useContractSearch(); return () => null } })
  await flushPromises()
  expect(search.favorites.value.map(row=>row.symbol)).toEqual(good.map(row=>row.symbol))
  expect(search.recent.value.map(row=>row.symbol)).toEqual(good.map(row=>row.symbol))
  expect(localStorage.getItem('quant_contract_favorites')).toBe(saved)
  wrapper.unmount()
})

test('re-adding a legacy favorite refreshes its missing contract metadata', async () => {
  localStorage.setItem('quant_contract_favorites',JSON.stringify([{symbol:'au2612',exchange:'SHFE'}]))
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({code:0,data:[]}))))
  let search
  const wrapper = mount({ setup() { search=useContractSearch(); return () => null } })
  await flushPromises()
  expect(search.isFavorite('au2612')).toBe(false)
  search.toggleFavorite(good[0])
  expect(search.isFavorite('au2612')).toBe(true)
  expect(JSON.parse(localStorage.getItem('quant_contract_favorites'))).toEqual([good[0]])
  wrapper.unmount()
})
