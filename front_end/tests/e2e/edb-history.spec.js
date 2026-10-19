import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  expect((await page.request.post('/api/auth/login', { data: { username: 'E2E_ONLY', password: 'fixture' } })).status()).toBe(200)
  await page.evaluate(() => sessionStorage.setItem('quant_session_active', '1'))
})
test.afterEach(async ({ page }) => { await page.request.post('/api/auth/logout') })

async function openDownload(page, path, symbol = 'au2612', timeframe = '日线') {
  await page.goto(path)
  await page.getByRole('button', { name: '下载历史行情', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '下载历史行情' })
  await expect(dialog.getByRole('button', { name: '下载并保存' })).toBeEnabled()
  await dialog.getByRole('combobox', { name: '历史数据交易所' }).press('Enter')
  await page.getByRole('option', { name: '上期所 SHFE', exact: true }).click()
  await dialog.getByRole('textbox', { name: '历史数据合约', exact: true }).fill(symbol)
  await dialog.getByRole('combobox', { name: '历史数据周期' }).press('Enter')
  await page.getByRole('option', { name: timeframe, exact: true }).click()
  return dialog
}

async function dates(dialog, start, end) {
  await dialog.getByRole('combobox', { name: '历史数据开始日期' }).fill(start)
  await dialog.getByRole('combobox', { name: '历史数据开始日期' }).press('Tab')
  await dialog.getByRole('combobox', { name: '历史数据结束日期' }).fill(end)
  await dialog.getByRole('combobox', { name: '历史数据结束日期' }).press('Tab')
}

test('daily import flows through real parser and database into backtest with provenance', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 2560, height: 1440 })
  const dialog = await openDownload(page, '/backtest')
  await dates(dialog, '2026-09-01', '2026-10-15')
  await dialog.getByRole('button', { name: '下载并保存' }).click()
  await expect(dialog.getByRole('status')).toContainText('已导入 33 根 K 线')
  await expect(dialog.getByRole('status')).toContainText('SHFE.au2612')
  await dialog.screenshot({ path: 'test-results/edb-daily-import.png' })
  await dialog.getByRole('button', { name: '用于回测' }).click()
  await expect(page.getByPlaceholder('IF9999')).toHaveValue('au2612')
  const completed = page.waitForResponse(r => r.url().endsWith('/api/backtest/run'))
  await page.getByRole('button', { name: '运行回测', exact: true }).click()
  const result = await (await completed).json()
  expect(result).toMatchObject({ success: true, data_source: 'tianqin_edb', timeframe: '1d', synthetic_data_used: false })
  await expect(page.locator('.bt-page > .el-alert').first()).toContainText('数据来源：天勤 EDB')
  await page.screenshot({ path: 'test-results/edb-backtest-desktop.png', fullPage: true })
  expect(errors).toEqual([])
})

test('minute import can be applied to chart and source remains visible after refresh', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const dialog = await openDownload(page, '/kline', 'au2612', '1 分钟')
  await dates(dialog, '2026-10-15', '2026-10-15')
  await dialog.getByRole('button', { name: '下载并保存' }).click()
  await expect(dialog.getByRole('status')).toContainText('已导入 60 根 K 线')
  await dialog.getByRole('button', { name: '查看 K 线' }).click()
  await expect(page.locator('.kline-source')).toContainText('天勤 EDB（历史行情）')
  await expect(page.locator('.kline-wrap')).toContainText('60 根')
  await page.reload()
  await expect(page.locator('.kline-source')).toContainText('天勤 EDB（历史行情）')
  await page.screenshot({ path: 'test-results/edb-minute-chart.png', fullPage: true })
  expect(errors).toEqual([])
})

test('upstream refusal and empty history are visible without fabricated fallback', async ({ page }) => {
  const dialog = await openDownload(page, '/backtest', 'ag2612')
  await dates(dialog, '2026-10-15', '2026-10-15')
  await dialog.getByRole('button', { name: '下载并保存' }).click()
  await expect(dialog).toContainText('EDB 拒绝访问')
  await expect(dialog.getByRole('button', { name: '用于回测' })).toHaveCount(0)
  await dialog.getByRole('textbox', { name: '历史数据合约', exact: true }).fill('cu2612')
  await dialog.getByRole('button', { name: '下载并保存' }).click()
  await expect(dialog.getByRole('status')).toContainText('该范围没有数据')
  const chart = await (await page.request.get('/api/watch/kline?symbol=cu2612&interval=1d')).json()
  expect(chart).toMatchObject({ total: 0, data_source: 'missing', synthetic_data_used: false })
})

test('mobile download form stays usable and long minute requests are rejected before download', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  let downloads = 0
  page.on('request', request => { if (request.url().endsWith('/api/data/history/edb/import')) downloads++ })
  const dialog = await openDownload(page, '/backtest', 'au2612', '1 分钟')
  await dates(dialog, '2026-09-01', '2026-10-15')
  await dialog.getByRole('button', { name: '下载并保存' }).click()
  await expect(dialog).toContainText('单次最多下载 31 个自然日')
  await expect(dialog.getByText('单次最多下载 31 个自然日，请分段下载', { exact: true })).toBeInViewport()
  expect(downloads).toBe(0)
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  await expect(dialog.getByRole('button', { name: '关闭', exact: true })).toBeInViewport()
  await dialog.screenshot({ path: 'test-results/edb-mobile.png' })
})

test('older backend is reported explicitly and cannot start an unsupported download', async ({ page }) => {
  await page.route('**/api/data/history/edb', route => route.fulfill({ status: 404, json: { detail: 'Not Found' } }))
  await page.goto('/backtest')
  await page.getByRole('button', { name: '下载历史行情', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '下载历史行情' })
  await expect(dialog).toContainText('请确认已重启并加载新版后端')
  await expect(dialog.getByRole('button', { name: '下载并保存' })).toBeDisabled()
})
