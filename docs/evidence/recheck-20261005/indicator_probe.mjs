import {writeFileSync} from 'node:fs'
import {computeIndicators} from '../quant/front_end/src/workers/indicatorMath.js'
const data={
  uptrend:Array.from({length:30},(_,i)=>100+i),
  flat:Array.from({length:30},()=>100),
  mixed:[100,101,104,99,102,101,107,109,106,110,108,111,107,109,113,112,110,109,108,107],
}
const result=Object.fromEntries(Object.entries(data).map(([key,closes])=>[key,computeIndicators('calc_rsi',{closes,period:14}).at(-1)]))
writeFileSync(new URL('./frontend-indicators.json',import.meta.url),JSON.stringify(result,null,2))
console.log(JSON.stringify(result,null,2))
