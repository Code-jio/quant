import { expect, test } from '@playwright/test'

test('domestic scope applies to discovery, saved choices, search and the order form', async ({ page }) => {
  const future = (symbol, name, exchange) => ({symbol,name,exchange,product:'FUTURES',source:'ctp'})
  const gold = future('au2612','黄金筛选验收','SHFE')
  const rows = [
    future('AF2610','澳元兑美元仿真','CFFEX'),
    {...future('au2612C1000','黄金期权','SHFE'),product:'OPTION'},
    future('GC2612','境外黄金','COMEX'),
    gold, future('IF2612','沪深300筛选验收','CFFEX'),
  ]
  await page.route('**/api/watch/search?**', route => {
    const params = new URL(route.request().url()).searchParams
    const q = (params.get('query') || '').toLowerCase()
    const exchange = params.get('exchange')
    const data = rows.filter(row => (!exchange || row.exchange === exchange)
      && (!q || `${row.symbol} ${row.name}`.toLowerCase().includes(q)))
    return route.fulfill({json:{code:0,data,total:data.length}})
  })
  await page.goto('/login')
  expect((await page.request.post('/api/auth/login',{data:{username:'E2E_ONLY',password:'fixture'}})).status()).toBe(200)
  await page.evaluate(({rows}) => {
    sessionStorage.setItem('quant_session_active','1')
    localStorage.setItem('quant_contract_favorites',JSON.stringify(rows))
    localStorage.setItem('quant_contract_recent',JSON.stringify(rows))
  }, {rows})
  try {
    await page.goto('/')
    await page.locator('.desk-search').click()
    const dialog = page.locator('.cs-dialog')
    await expect(dialog).toContainText('仅显示国内期货')
    await expect(dialog.locator('.hot-chip')).toHaveCount(2)
    await expect(dialog.locator('.mini-row')).toHaveCount(4)
    await expect(dialog).not.toContainText('AF2610')
    await expect(dialog).not.toContainText('GC2612')
    await expect(dialog).not.toContainText('au2612C1000')
    const input = page.getByPlaceholder('搜索期货合约：代码、中文名称、拼音首字母…')
    await input.fill('AF')
    await expect(dialog).toContainText('未找到匹配的国内期货合约')
    await input.fill('黄金')
    await expect(dialog.locator('.result-row')).toHaveCount(1)
    await expect(dialog.locator('.result-row')).toContainText('au2612')
    await dialog.screenshot({path:'test-results/domestic-contract-search.png'})
    await input.fill('IF')
    await expect(dialog.locator('.result-row')).toContainText('IF2612')
    await dialog.locator('.cs-close').click()
    const orderInput = page.locator('.tp-wrap .el-select input').first()
    await orderInput.fill('黄金')
    await expect(page.getByRole('option',{name:'au2612 - 黄金筛选验收',exact:true})).toBeVisible()
    await expect(page.getByRole('option',{name:/期权|境外/})).toHaveCount(0)
  } finally {
    await page.request.post('/api/auth/logout')
  }
})
