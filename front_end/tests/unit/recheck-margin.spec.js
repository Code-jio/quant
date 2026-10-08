import {test,expect} from 'vitest'
import {marginRatesFromRows} from '@/utils/marginRates.js'

test('explicit percentages are converted without inventing missing rates',()=>{
  expect(marginRatesFromRows([{symbol:' A ',percent:12},{symbol:'',percent:undefined}])).toEqual({A:.12})
  expect(marginRatesFromRows([])).toEqual({})
})
test.each([0,-1,101,NaN,Infinity,undefined,true])('invalid margin %s is rejected',percent=>{
  expect(()=>marginRatesFromRows([{symbol:'A',percent}])).toThrow()
})
test('partial and duplicate rows are rejected',()=>{
  expect(()=>marginRatesFromRows([{symbol:'',percent:12}])).toThrow()
  expect(()=>marginRatesFromRows([{symbol:'A',percent:12},{symbol:'A',percent:13}])).toThrow()
})
