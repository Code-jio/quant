import { expect, test } from '@playwright/test'

async function loginThroughPage(page, password = 'fixture') {
  await page.goto('/login')
  await expect(page.getByPlaceholder('如 2071')).toHaveValue('E2E_BROKER')
  await page.getByPlaceholder('账号', { exact: true }).fill('E2E_ONLY')
  await page.getByPlaceholder('密码', { exact: true }).fill(password)
  const finished = page.waitForResponse(response => response.url().endsWith('/api/auth/login'))
  await page.getByRole('button', { name: '连接交易账户', exact: true }).click()
  return finished
}

async function sessionAction(page, all = false) {
  await page.getByRole('button', { name: '账户会话' }).click()
  await page.getByRole('menuitem', { name: all ? '断开全部终端' : '退出当前端', exact: true }).click()
  await expect(page.locator('.el-message-box')).toContainText(all ? '所有终端退出' : '其他设备和后台策略继续运行')
  await page.locator('.el-message-box').getByRole('button', { name: all ? '确认断开全部' : '确认退出当前端', exact: true }).click()
}

test('independent devices join one account, restore tabs, leave individually and disconnect explicitly', async ({ browser, baseURL }) => {
  const first = await browser.newContext({ baseURL, viewport: { width: 1920, height: 1080 } })
  const second = await browser.newContext({ baseURL, viewport: { width: 1920, height: 1080 } })
  const a = await first.newPage()
  const b = await second.newPage()
  const errors = []
  for (const page of [a, b]) page.on('pageerror', error => errors.push(error.message))
  try {
    expect((await loginThroughPage(a)).status()).toBe(200)
    await expect(a.locator('.trading-desk')).toBeVisible()
    const original = (await first.cookies()).find(cookie => cookie.name === 'quant_session')
    expect((await loginThroughPage(b, 'wrong-fixture')).status()).toBe(401)
    await expect(b.locator('.error-banner')).toContainText('账号或密码不正确')
    expect((await a.request.get('/api/system/status')).status()).toBe(200)
    const joined = await loginThroughPage(b)
    expect(joined.status()).toBe(200)
    expect((await joined.json()).connection_reused).toBe(true)
    await expect(b.locator('.trading-desk')).toBeVisible()
    const peer = (await second.cookies()).find(cookie => cookie.name === 'quant_session')
    expect(peer.value).not.toBe(original.value)
    expect(peer.httpOnly).toBe(true)
    expect((await (await a.request.get('/api/auth/status')).json()).active_sessions).toBe(2)

    const tab = await first.newPage()
    await tab.goto('/')
    await expect(tab.locator('.trading-desk')).toBeVisible()
    expect((await (await a.request.get('/api/auth/status')).json()).active_sessions).toBe(2)
    await tab.close()
    await b.reload()
    await expect(b.locator('.trading-desk')).toBeVisible()

    await a.getByRole('button', { name: '账户会话' }).click()
    await a.getByRole('menuitem', { name: '退出当前端', exact: true }).click()
    await expect(a.locator('.el-message-box')).toBeVisible()
    await a.screenshot({ path: 'test-results/multi-device-session.png', fullPage: true })
    await a.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click()
    await sessionAction(a)
    await expect(a).toHaveURL(/\/login$/)
    expect((await a.request.get('/api/system/status')).status()).toBe(401)
    expect((await b.request.get('/api/system/status')).status()).toBe(200)
    expect((await (await b.request.get('/api/auth/status')).json()).active_sessions).toBe(1)
    await expect(b.locator('.account-identity')).toContainText('账户推送已连接')

    expect((await (await loginThroughPage(a)).json()).connection_reused).toBe(true)
    await expect(a.locator('.trading-desk')).toBeVisible()
    await sessionAction(a, true)
    await expect(a).toHaveURL(/\/login$/)
    expect((await b.request.get('/api/system/status')).status()).toBe(401)
    await b.reload()
    await expect(b).toHaveURL(/\/login$/)
    expect(errors).toEqual([])
  } finally {
    // Cleanup only the isolated fixture service, even after a failed assertion.
    for (const context of [first, second]) {
      await context.request.post('/api/auth/logout')
      await context.close()
    }
  }
})

test('failed per-device logout never silently falls back to global disconnect', async ({ page }) => {
  let disconnects = 0
  try {
    expect((await loginThroughPage(page)).status()).toBe(200)
    await expect(page.locator('.trading-desk')).toBeVisible()
    await page.route('**/api/auth/session/logout', route => route.fulfill({ status: 404, json: { detail: '旧后端尚未更新' } }))
    await page.route('**/api/auth/logout', route => { disconnects++; return route.abort() })
    await sessionAction(page)
    await expect(page.getByText(/退出未完成：旧后端尚未更新/)).toBeVisible()
    await expect(page.locator('.trading-desk')).toBeVisible()
    expect(disconnects).toBe(0)
    expect((await page.request.get('/api/system/status')).status()).toBe(200)
  } finally { await page.request.post('/api/auth/logout') }
})
