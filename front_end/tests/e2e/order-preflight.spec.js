import { expect, test } from '@playwright/test'

test.afterEach(async ({ page }) => { await page.request.post('/api/auth/logout') })

function preview(allowed = true) {
  return { allowed, snapshot_only: true, reason: allowed ? '' : 'Gateway snapshot is not ready',
    checked_at: '2026-10-15T10:00:00+08:00',
    checks: [{ key: 'snapshot', label: '账户快照', passed: allowed, reason: allowed ? '' : '等待账户同步' }],
    margin: { required: 120, reserved: 240, available: 1000, remaining: 640 } }
}

async function enterDesk(page) {
  await page.setViewportSize({ width: 2560, height: 1440 })
  await page.goto('/login')
  expect((await page.request.post('/api/auth/login', { data: { username: 'E2E_ONLY', password: 'fixture' } })).status()).toBe(200)
  await page.evaluate(() => {
    sessionStorage.setItem('quant_session_active', '1')
    localStorage.setItem('quant_cur_symbol', JSON.stringify({ symbol: 'E2E2026', name: '预检查隔离样本' }))
  })
  await page.goto('/')
  await expect(page.locator('.tp-form .el-select')).toContainText('E2E2026')
  await page.locator('#trade-price').fill('100')
  await page.locator('#trade-price').blur()
}

test('preflight blocks submission, explains unknown data and network failures', async ({ page }) => {
  const writes = []
  await page.route('**/api/orders', route => { writes.push(route.request().postDataJSON()); return route.fulfill({ status: 503, json: {} }) })
  let offline = false
  await page.route('**/api/orders/preflight', route => offline
    ? route.fulfill({ status: 503, json: { detail: '预检查服务不可用（隔离测试）' } })
    : route.fulfill({ json: { ...preview(false), margin: { required: null, reserved: null, available: null, remaining: null } } }))
  await enterDesk(page)
  await expect(page.locator('.preflight-panel summary')).toContainText('暂不可下单')
  await page.locator('.preflight-panel summary').click()
  await expect(page.locator('.preflight-money')).toContainText('未知')
  await page.locator('.submit-btn').click()
  await expect(page.locator('.el-message-box')).toHaveCount(0)
  await expect(page.locator('.el-message')).toContainText('检查未通过')
  await page.screenshot({ path: 'test-results/preflight-blocked-2560.png', fullPage: true })
  offline = true
  await page.getByRole('button', { name: '刷新检查', exact: true }).click()
  await expect(page.locator('.preflight-panel summary')).toContainText('检查不可用')
  await page.locator('.submit-btn').click()
  await expect(page.locator('.el-message-box')).toHaveCount(0)
  expect(writes).toEqual([])
})

test('confirmation submits exactly the frozen ticket once despite form updates', async ({ page }) => {
  const writes = []
  await page.route('**/api/orders/preflight', route => route.fulfill({ json: preview() }))
  await page.route('**/api/orders', route => {
    writes.push(route.request().postDataJSON())
    return route.fulfill({ json: { success: true, order_id: 'BROWSER_ONLY' } })
  })
  await enterDesk(page)
  await page.locator('.preflight-panel summary').click()
  await expect(page.locator('.preflight-money')).toContainText('¥120.00')
  await page.screenshot({ path: 'test-results/preflight-ready-2560.png', fullPage: true })
  await page.locator('.submit-btn').click()
  await expect(page.locator('.el-message-box')).toContainText('100.00')
  // Simulate a reactive external form update while the confirmation is open.
  await page.locator('#trade-price').evaluate(input => {
    input.value = '200'; input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await expect(page.locator('.tp-preview')).toContainText('200.00')
  await expect(page.locator('.submit-btn')).toBeDisabled()
  await page.locator('.el-message-box').getByRole('button', { name: '确认下单', exact: true }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0]).toEqual({ symbol: 'E2E2026', direction: 'long', offset: 'open', price: 100, volume: 1, order_type: 'limit' })
})

test('late preview cannot replace the result for the latest ticket', async ({ page }) => {
  let release
  let delayed = false
  await page.route('**/api/orders/preflight', async route => {
    if (route.request().postDataJSON().price === 100) {
      delayed = true
      await new Promise(resolve => { release = resolve })
      return route.fulfill({ json: preview(true) })
    }
    return route.fulfill({ json: preview(false) })
  })
  await enterDesk(page)
  await expect.poll(() => delayed).toBe(true)
  await page.locator('#trade-price').fill('200')
  await page.locator('#trade-price').blur()
  await expect(page.locator('.preflight-panel summary')).toContainText('暂不可下单')
  const delayedResponse = page.waitForResponse(response => response.url().includes('/orders/preflight') && response.request().postDataJSON().price === 100)
  release()
  await (await delayedResponse).finished()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)))
  await expect(page.locator('.preflight-panel summary')).toContainText('账户快照')
})

test('quick close freezes explicit quantity and offset before confirmation', async ({ page }) => {
  const writes = []
  let volume = 3
  await page.route('**/api/positions', route => route.fulfill({ json: [{ symbol: 'E2E2026', direction: 'long', volume, frozen: 0, pnl: 0 }] }))
  await page.route('**/api/watch/tick?**', route => route.fulfill({ json: { ticks: { E2E2026: { bid1: 100, ask1: 101, timestamp: new Date().toISOString() } } } }))
  await page.route('**/api/orders/preflight', route => route.fulfill({ json: preview() }))
  await page.route('**/api/positions/E2E2026/close', route => {
    writes.push(route.request().postDataJSON())
    return route.fulfill({ json: { success: true } })
  })
  await enterDesk(page)
  await page.getByRole('button', { name: '卖出平多', exact: true }).click()
  await expect(page.locator('.el-message-box')).toContainText('3手，平仓')
  volume = 10
  await page.getByRole('button', { name: '刷新可平持仓', exact: true }).evaluate(button => button.click())
  await expect(page.locator('.tp-pos-item')).toContainText('10')
  await page.locator('.close-offset-group').getByText('平今', { exact: true }).evaluate(label => label.click())
  await expect(page.locator('.close-offset-group .is-active')).toHaveText('平今')
  await page.locator('.el-message-box').getByRole('button', { name: '确认平仓', exact: true }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0]).toEqual({ direction: 'long', offset: 'close', price: 100, volume: 3, order_type: 'limit' })
})
