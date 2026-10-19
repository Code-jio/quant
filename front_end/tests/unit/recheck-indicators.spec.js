import {expect,test} from 'vitest'
import {computeIndicators} from '@/workers/indicatorMath.js'
import cases from '../../../test_fixtures/rsi_cases.json'

test.each(cases)('Wilder RSI matches shared fixture: $name',({closes,period,expected})=>{
  const actual=computeIndicators('calc_rsi',{closes,period})
  expect(actual).toHaveLength(expected.length)
  expected.forEach((value,i)=>{
    if(value===null) expect(actual[i]).toBeNull()
    else expect(actual[i]).toBeCloseTo(value,10)
  })
})
