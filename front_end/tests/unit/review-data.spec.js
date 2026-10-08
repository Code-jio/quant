import { ref } from 'vue'
import { vi, expect, test, beforeEach } from 'vitest'
vi.mock('@/api/index.js', () => ({fetchKline:vi.fn()}))
import { fetchKline } from '@/api/index.js'
import { useKlineData } from '@/composables/useKlineData.js'
import { useIndicatorWorker } from '@/composables/useIndicatorWorker.js'

const row=(time,close=100)=>({timestamp:time,open:close,high:close,low:close,close,volume:1})
beforeEach(()=>vi.clearAllMocks())

test('late history response cannot overwrite a newly selected symbol',async()=>{
  let finishOld
  fetchKline.mockImplementationOnce(()=>new Promise(resolve=>{finishOld=resolve}))
    .mockResolvedValueOnce({data:[row('2024-01-02',200)],has_more:false})
  const data=useKlineData()
  const old=data.load('OLD')
  await data.load('NEW')
  finishOld({data:[row('2024-01-01',100)]})
  await old
  expect(data.bars.value[0].close).toBe(200)
})

test('pagination deduplicates bars and their indicators together',async()=>{
  fetchKline.mockResolvedValueOnce({data:[{...row('2024-01-03'),ma5:3}],has_more:true})
    .mockResolvedValueOnce({data:[{...row('2024-01-02'),ma5:2},{...row('2024-01-03'),ma5:3}],has_more:false})
  const data=useKlineData()
  await data.load('rb','1d',1,[5]); await data.loadMore('rb','1d',2,[5])
  expect(data.bars.value).toHaveLength(2)
  expect(data.maData.value.ma5).toEqual([2,3])
  expect(fetchKline.mock.calls[1][0].before).toBe('2024-01-03')
})

test('worker fallback calculates reactive data and invalidates same-length changes',async()=>{
  vi.stubGlobal('Worker',undefined)
  const worker=useIndicatorWorker();worker.clearCache()
  const bars=ref([row('2024-01-01')])
  const params={maParams:{periods:[1]}}
  expect((await worker.calcAll('rb','1m',bars.value,params)).ma.ma1).toEqual([100])
  bars.value[0].close=200
  expect((await worker.calcAll('rb','1m',bars.value,params)).ma.ma1).toEqual([200])
  vi.unstubAllGlobals()
})
