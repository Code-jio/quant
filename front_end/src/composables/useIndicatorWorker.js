import { getCurrentScope, onScopeDispose } from 'vue'
import { computeIndicators } from '@/workers/indicatorMath.js'
let worker = null
let nextId = 0
let users = 0
const callbacks = new Map()
const cache = new Map()
function disposeWorker() {
  worker?.terminate()
  worker = null
  for (const cb of callbacks.values()) cb.reject(new Error('Worker stopped'))
  callbacks.clear()
}
function getWorker() {
  if (worker) return worker
  if (typeof Worker === 'undefined') return null
  worker = new Worker(new URL('../workers/indicatorWorker.js', import.meta.url), {type:'module'})
  worker.onmessage = ({data}) => {
    const cb = callbacks.get(data.id)
    if (!cb) return
    callbacks.delete(data.id)
    data.error ? cb.reject(new Error(data.error)) : cb.resolve(data.result)
  }
  worker.onerror = disposeWorker
  return worker
}
async function dispatch(type, source) {
  // Explicit JSON DTO strips every nested Vue proxy before structured cloning.
  const payload = JSON.parse(JSON.stringify(source))
  const id = ++nextId
  let timer
  try {
    const target = getWorker()
    if (!target) return computeIndicators(type,payload)
    return await new Promise((resolve,reject) => {
      callbacks.set(id,{resolve,reject})
      timer = setTimeout(()=>reject(new Error('Indicator timeout')),5000)
      target.postMessage({id,type,payload})
    })
  } catch {
    return computeIndicators(type,payload)
  } finally {
    clearTimeout(timer)
    callbacks.delete(id)
  }
}
export function useIndicatorWorker() {
  users++
  if (getCurrentScope()) onScopeDispose(()=>{
    if (--users===0) { disposeWorker(); cache.clear() }
  })
  async function calcAll(symbol,interval,bars,params={}) {
    if (!bars?.length) return null
    const payload={bars:bars.slice(-5000),...params}
    const key=JSON.stringify([symbol,interval,payload])
    if (cache.has(key)) return cache.get(key)
    const result=await dispatch('calc_all',payload)
    if (cache.size>=8) cache.delete(cache.keys().next().value)
    cache.set(key,result)
    return result
  }
  return {calcAll,calcMA:(closes,periods)=>dispatch('calc_ma',{closes,periods}),
    calcMACD:(closes,params={})=>dispatch('calc_macd',{closes,...params}),
    clearCache:()=>cache.clear()}
}
