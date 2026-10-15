import {expect,test} from '@playwright/test'

test('offline backtest runs with explicit synthetic data and renders result charts',async({page})=>{
  const errors=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.goto('/backtest')
  await page.locator('.el-switch__core').click()
  const finished=page.waitForResponse(r=>r.url().endsWith('/api/backtest/run'))
  await page.getByRole('button',{name:'运行回测',exact:true}).click()
  const response=await finished
  expect(response.status()).toBe(200)
  const result=await response.json()
  expect(result.success).toBe(true)
  if (result.metrics.volatility == null) {
    await expect(page.locator('.el-table__row').filter({hasText:'年化波动率'})).toContainText('不可计算')
  }
  await expect(page.getByText(/数据来源：.*模拟数据/)).toBeVisible()
  await expect(page.locator('.chart-equity canvas')).toBeVisible()
  await page.screenshot({path:'test-results/backtest-offline.png',fullPage:true})
  expect(errors).toEqual([])
})

test('stored historical bars render through the real worker without auth sockets',async({page})=>{
  const errors=[],warnings=[],sockets=[],workers=[]
  page.on('pageerror',e=>errors.push(e.message))
  page.on('console',m=>{if(m.type()==='warning')warnings.push(m.text())})
  page.on('websocket',ws=>{if(new URL(ws.url()).pathname.startsWith('/ws/'))sockets.push(ws.url())})
  page.on('worker',w=>workers.push(w.url()))
  await page.addInitScript(()=>localStorage.setItem('quant_cur_symbol',JSON.stringify({symbol:'E2E2026',name:'浏览器验收样本',exchange:'TEST'})))
  await page.goto('/watch')
  await expect(page.locator('.chart-dom canvas')).toBeVisible()
  await expect(page.locator('.chart-error')).toHaveCount(0)
  await page.screenshot({path:'test-results/watch-history.png',fullPage:true})
  expect(workers.length).toBeGreaterThan(0)
  expect(sockets).toEqual([])
  expect(errors).toEqual([])
  expect(warnings.filter(w=>w.includes('no active component'))).toEqual([])
})

test('authenticated sockets close on logout and trading data loses access',async({page})=>{
  await page.goto('/login')
  const response=await page.request.post('/api/auth/login',{data:{username:'E2E_ONLY',password:'fixture',broker_id:'fixture',td_server:'tcp://127.0.0.1:1',md_server:'tcp://127.0.0.1:1',auto_start_strategy:false}})
  expect(response.status()).toBe(200)
  await page.evaluate(()=>{sessionStorage.setItem('quant_session_active','1');sessionStorage.setItem('quant_account_id','E2E_ONLY')})
  const sockets=[]
  const messages=[]
  page.on('websocket',ws=>{if(new URL(ws.url()).pathname.startsWith('/ws/')){
    sockets.push(ws);ws.on('framereceived',frame=>messages.push(frame.payload))
  }})
  await page.goto('/')
  await expect.poll(()=>sockets.length).toBeGreaterThan(0)
  await expect.poll(()=>messages.length).toBeGreaterThan(0)
  expect((await page.request.post('/api/auth/logout')).status()).toBe(200)
  await page.evaluate(()=>{sessionStorage.clear();window.dispatchEvent(new CustomEvent('quant-auth-change'))})
  await expect.poll(()=>sockets.every(ws=>ws.isClosed())).toBe(true)
  expect((await page.request.get('/api/trading/snapshot')).status()).toBe(401)
})
