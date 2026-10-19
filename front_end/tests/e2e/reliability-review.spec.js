import { expect, test } from '@playwright/test'

async function desk(page) {
  await page.goto('/login')
  expect((await page.request.post('/api/auth/login', { data: { username: 'E2E_ONLY', password: 'fixture' } })).status()).toBe(200)
  await page.evaluate(() => sessionStorage.setItem('quant_session_active', '1'))
  await page.goto('/')
}
test.afterEach(async ({ page }) => { await page.request.post('/api/auth/logout') })

test('manual search ignores obsolete responses and clear invalidates pending results', async ({ page }) => {
  const pending = new Map()
  await page.route('**/api/watch/search?**', async route => {
    const q = new URL(route.request().url()).searchParams.get('query') || ''
    if (q === 'au' || q === 'cu') await new Promise(resolve => pending.set(q, resolve))
    return route.fulfill({ json: { code: 0, data: q ? [{ symbol: `${q}2612`, name: '隔离搜索样本', product: 'FUTURES', exchange: 'SHFE' }] : [] } })
  })
  await desk(page)
  const input = page.locator('.tp-wrap .el-select input').first()
  await input.fill('au')
  await expect.poll(() => pending.has('au')).toBe(true)
  await input.fill('ag')
  await expect(page.getByRole('option', { name: 'ag2612 - 隔离搜索样本', exact: true })).toBeVisible()
  let response = page.waitForResponse(r => r.url().includes('query=au'))
  pending.get('au')(); await (await response).finished()
  await expect(page.getByRole('option', { name: 'ag2612 - 隔离搜索样本', exact: true })).toBeVisible()
  await expect(page.getByRole('option', { name: 'au2612 - 隔离搜索样本', exact: true })).toHaveCount(0)
  await input.fill('cu')
  await expect.poll(() => pending.has('cu')).toBe(true)
  await input.fill('')
  response = page.waitForResponse(r => r.url().includes('query=cu'))
  pending.get('cu')(); await (await response).finished()
  await expect(page.getByRole('option', { name: 'cu2612 - 隔离搜索样本', exact: true })).toHaveCount(0)
})

test('domestic catalog pages and product filters retain total and lifecycle context', async ({ page }) => {
  await page.setViewportSize({ width: 2560, height: 1440 })
  const rows = Array.from({ length: 65 }, (_, i) => ({ symbol: `au${27 + Math.floor(i / 12)}${String(i % 12 + 1).padStart(2, '0')}`,
    name: '黄金目录隔离样本', product: 'FUTURES', product_root: 'AU', exchange: 'SHFE', expiry_date: null, tradable: null }))
  await page.route('**/api/watch/search?**', route => {
    const p = new URL(route.request().url()).searchParams
    const start = Number(p.get('offset') || 0), limit = Number(p.get('limit') || 60)
    return route.fulfill({ json: { code: 0, data: rows.slice(start, start + limit), total: rows.length, paginated: true, products: { AU: '黄金' } } })
  })
  await desk(page)
  await page.locator('.desk-search').click()
  const dialog = page.locator('.cs-dialog')
  await expect(dialog.locator('.hot-chip')).toHaveCount(60)
  await expect(dialog).toContainText('共 65 条')
  await dialog.locator('.el-pagination .btn-next').click()
  await expect(dialog.locator('.hot-chip')).toHaveCount(5)
  await expect(dialog.locator('.hot-chip').first()).toContainText(rows[60].symbol)
  await dialog.getByRole('combobox', { name: '品种筛选' }).click()
  await page.getByRole('option', { name: 'AU · 黄金', exact: true }).click()
  await expect(dialog.locator('.result-row')).toHaveCount(60)
  await expect(dialog.locator('.result-row').first()).toContainText('交易状态未知')
  await dialog.screenshot({ path: 'test-results/reliability-catalog-2560.png' })
})

test('recovery requires evidence and confirmation and never resumes automatically', async ({ page }) => {
  const writes = [], forbidden = []
  await page.route('**/api/risk/resume', route => { forbidden.push('resume'); return route.fulfill({json:{}}) })
  await page.route('**/api/orders', route => {
    if (route.request().method() === 'POST') forbidden.push('order')
    return route.fulfill({json:[]})
  })
  await page.route('**/api/trading/reconcile', route => route.fulfill({json:{ generation: 7, snapshot_ready: true, risk: {emergency_stop:true},
    orders: {active_count:0}, differences:[], pending_intents:[{id:'I1',symbol:'au2612',direction:'long',offset:'open',volume:1,price:100}],
    broker_snapshot:{id:'FIXTURE_SNAPSHOT',status:'ready',reason:'隔离柜台查询完整，待人工核对',orders:[],trades:[]}}}))
  await page.route('**/api/trading/reconcile/resolve', route => {
    writes.push(route.request().postDataJSON()); return route.fulfill({json:{success:true,emergency_stop:true}})
  })
  await desk(page)
  await page.getByRole('button', { name:'对账与恢复', exact:true }).click()
  const dialog=page.locator('.recovery-dialog')
  await dialog.getByRole('button',{name:'记录人工核对结果'}).click()
  expect(writes).toHaveLength(0)
  await dialog.getByPlaceholder('核对依据 / 柜台凭证编号（至少 12 个字符，请勿填写密码）').fill('隔离验收柜台凭证 FIXTURE-001')
  await dialog.getByRole('button',{name:'记录人工核对结果'}).click()
  await expect(page.locator('.el-message-box')).toContainText('仅查询不到记录不足以证明未发送')
  await page.locator('.el-message-box').getByRole('button',{name:'返回核对'}).click()
  expect(writes).toHaveLength(0)
  await dialog.getByRole('button',{name:'记录人工核对结果'}).click()
  await page.locator('.el-message-box').getByRole('button',{name:'确认记录'}).click()
  await expect.poll(()=>writes.length).toBe(1)
  await expect(page.locator('.el-message-box')).not.toBeVisible()
  expect(writes[0]).toMatchObject({generation:7,snapshot_id:'FIXTURE_SNAPSHOT',intent_id:'I1',order_id:''})
  expect(forbidden).toEqual([])
  await dialog.screenshot({path:'test-results/reliability-recovery.png'})
})
