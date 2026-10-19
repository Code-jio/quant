import { expect, test } from 'vitest'
import { VolumeWindow } from '@/utils/volumeWindow.js'

test('compares incremental one-second windows, independent of cumulative baseline', () => {
  const window = new VolumeWindow()
  let result
  for (let i = 0; i <= 7; i++) result = window.observe(100000 + i, i * 1000, '20261019')
  expect(result.ratio).toBe(1)
  window.observe(101007, 7500, '20261019')
  result = window.observe(101008, 8000, '20261019')
  expect(result.volume).toBe(1001)
  expect(result.ratio).toBeGreaterThan(100)
})

test('ignores duplicates/out-of-order ticks and resets gaps, day changes and counter resets', () => {
  const window = new VolumeWindow()
  for (let i = 0; i <= 7; i++) window.observe(100 + i, i * 1000, '20261019')
  expect(window.observe(10000, 6000, '20261019')).toBeNull()
  expect(window.observe(108, 7000, '20261019')).toBeNull()
  expect(window.observe(20000, 20000, '20261019')).toBeNull()
  expect(window.observe(1, 21000, '20261020')).toBeNull()
  expect(window.observe(0, 22000, '20261020')).toBeNull()
  expect(window.observe(1, NaN, '20261020')).toBeNull()
})
