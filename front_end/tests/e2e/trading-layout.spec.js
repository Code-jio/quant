import { expect, test } from '@playwright/test'

test.afterEach(async ({ page }) => { await page.request.post('/api/auth/logout') })

async function enterDesk(page) {
  await page.goto('/login')
  expect((await page.request.post('/api/auth/login', { data: { username: 'E2E_ONLY', password: 'fixture' } })).status()).toBe(200)
  await page.evaluate(() => {
    sessionStorage.setItem('quant_session_active', '1')
    const contracts = [{ symbol: 'E2E2026', name: '交易台验收样本', exchange: 'TEST' }, { symbol: 'E2E2027', name: '切换验收样本', exchange: 'TEST' }]
    localStorage.setItem('quant_cur_symbol', JSON.stringify(contracts[0]))
    localStorage.setItem('quant_watch_list', JSON.stringify(contracts))
  })
  await page.goto('/')
  await expect(page.locator('.desk-market .chart-dom canvas').first()).toBeVisible()
  await expect(page.locator('.tp-form .el-select')).toContainText('E2E2026')
}

test('desktop desk keeps market ticket and order book in one screen', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.routeWebSocket('**/ws/watch', socket => {
    socket.onMessage(raw => {
      if (raw === 'ping') { socket.send('pong'); return }
      const message = JSON.parse(String(raw))
      if (message.type !== 'subscribe') return
      for (const symbol of message.symbols) {
        const quote = { type: 'tick', symbol, last: 199.5, pre_close: 198, change: 1.5, change_rate: .76, open: 198, high: 201, low: 197, volume: 12345, open_interest: 56789, turnover: 2462828, time: '10:00:00（测试）' }
        for (let level = 1; level <= 5; level++) {
          quote[`ask${level}`] = 199.5 + level * .5
          quote[`bid${level}`] = 199.5 - level * .5
          quote[`ask${level}_vol`] = level * 10
          quote[`bid${level}_vol`] = level * 15
        }
        socket.send(JSON.stringify(quote))
      }
    })
  })
  await enterDesk(page)
  for (const size of [{ width: 1920, height: 1080 }, { width: 2560, height: 1440 }, { width: 1366, height: 768 }]) {
    await page.setViewportSize(size)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1)).toBe(true)
    for (const selector of ['.desk-market', '.desk-orders', '.desk-ticket', '.submit-btn']) {
      await expect(page.locator(selector)).toBeInViewport()
    }
    await expect.poll(async () => (await page.locator('.desk-market .chart-dom').boundingBox()).height).toBeGreaterThan(150)
    if (size.width >= 1850) {
      await expect(page.locator('.desk-depth')).toBeInViewport()
      await expect(page.locator('.desk-depth .ask-side .depth-row').first()).toContainText('卖5')
      await expect(page.locator('.desk-depth .ask-side .depth-row').first()).toContainText('202')
      await expect(page.locator('.desk-depth .ask-side .depth-row').last()).toContainText('卖1')
      await expect(page.locator('.desk-depth .ask-side .depth-row').last()).toContainText('200')
      const ask5 = await page.locator('.desk-depth .ask-side .depth-row').first().boundingBox()
      const ask1 = await page.locator('.desk-depth .ask-side .depth-row').last().boundingBox()
      const lastPrice = await page.locator('.desk-depth .depth-mid').boundingBox()
      expect(ask5.y).toBeLessThan(ask1.y)
      expect(ask1.y + ask1.height).toBeLessThanOrEqual(lastPrice.y)
    }
    const market = await page.locator('.desk-market').boundingBox()
    const ticket = await page.locator('.desk-ticket').boundingBox()
    const orders = await page.locator('.desk-orders').boundingBox()
    expect(market.x + market.width).toBeLessThanOrEqual(ticket.x)
    expect(market.y + market.height).toBeLessThanOrEqual(orders.y)
    await page.screenshot({ path: `test-results/trading-desk-${size.width}.png`, fullPage: true })
  }
  expect(errors).toEqual([])
})

test('contract linking clears the old limit price and trading actions retain confirmation', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const requests = []
  await page.route('**/api/trading/**', route => {
    if (route.request().method() !== 'GET') {
      requests.push(route.request().url())
      return route.fulfill({ status: 503, json: { detail: 'Browser fixture blocks all trading writes' } })
    }
    return route.continue()
  })
  await enterDesk(page)
  await page.locator('#trade-price').fill('123.45')
  await page.locator('#trade-price').blur()
  await page.locator('.watchlist-row').filter({ hasText: 'E2E2027' }).click()
  await expect(page.locator('.market-contract')).toContainText('E2E2027')
  await expect(page.locator('.tp-form .el-select')).toContainText('E2E2027')
  await expect(page.locator('#trade-price')).toHaveValue('0.00')
  await expect(page.locator('.submit-btn')).toBeDisabled()
  await page.locator('.watchlist-row').filter({ hasText: 'E2E2026' }).click()
  await page.locator('#trade-price').fill('123.45')
  await page.locator('#trade-price').blur()
  await page.locator('.submit-btn').click()
  await expect(page.locator('.el-message-box')).toContainText('E2E2026')
  await expect(page.locator('.el-message-box')).toContainText('123.45')
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('button', { name: '一键撤单', exact: true }).click()
  await expect(page.locator('.el-message-box')).toContainText('所有活跃委托')
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('button', { name: '急停', exact: true }).click()
  await expect(page.locator('.el-message-box')).toContainText('交易急停')
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click()
  expect(requests).toEqual([])
})

test('search keyboard navigation and analysis panels preserve the trading ticket', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1920, height: 1080 })
  await enterDesk(page)
  await page.locator('#trade-price').fill('123.45')
  await page.locator('#trade-price').blur()
  await page.locator('.desk-nav').getByRole('button', { name: '资金分析', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '资金分析' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '资金分析' })).not.toBeVisible()
  await expect(page.locator('#trade-price')).toHaveValue('123.45')
  const fullscreen = page.locator('.desk-market .toolbar-right > button.icon-btn').first()
  await fullscreen.click()
  await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement))).toBe(true)
  await expect.poll(async () => (await page.locator('.desk-market .chart-dom').boundingBox()).height).toBeGreaterThan(800)
  await fullscreen.click()
  await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement))).toBe(false)
  await page.locator('.desk-search').focus()
  await page.keyboard.press('Control+k')
  await expect(page.locator('.cs-dialog')).toBeVisible()
  await expect(page.getByPlaceholder('搜索期货合约：代码、中文名称、拼音首字母…')).toBeFocused()
  await page.keyboard.press('Escape')
  await page.route('**/api/strategies', route => route.fulfill({ status: 503, json: { detail: '验收：策略服务不可用' } }))
  await page.locator('.desk-nav').getByRole('button', { name: '策略管理', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '策略管理' })).toContainText('策略数据暂不可用')
  await expect(page.getByRole('dialog', { name: '策略管理' }).getByText('MA双均线', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: 'test-results/trading-strategy-error.png', fullPage: true })
  expect(errors).toEqual([])
})

test('dense order rows scroll inside the dock while the ticket stays visible', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.route('**/api/trading/snapshot', route => route.fulfill({ json: {
    revision: 999999, trades: [], positions: [],
    orders: Array.from({ length: 60 }, (_, index) => ({ order_id: `LAYOUT_${index}`, symbol: 'E2E2026', direction: 'long', offset: 'open', order_type: 'limit', price: 123.45, volume: 1, traded_volume: 0, status: 'submitted', create_time: '10:00:00', create_ts: `2026-10-08T10:00:${String(index).padStart(2, '0')}` })),
  } }))
  await enterDesk(page)
  await expect(page.locator('.desk-orders .filter-bar')).toContainText('60 条')
  const scroll = page.locator('.desk-orders .el-table__body-wrapper .el-scrollbar__wrap').first()
  await expect.poll(() => scroll.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await scroll.evaluate(element => { element.scrollTop = element.scrollHeight })
  await expect(page.locator('.submit-btn')).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBe(true)
  await page.screenshot({ path: 'test-results/trading-orders-scroll.png', fullPage: true })
})

test('tablet and narrow screens have reachable controls without page-wide overflow', async ({ page }) => {
  await enterDesk(page)
  for (const width of [768, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.submit-btn').scrollIntoViewIfNeeded()
    await expect(page.locator('.submit-btn')).toBeInViewport()
    await page.screenshot({ path: `test-results/trading-desk-${width}.png`, fullPage: true })
  }
})
