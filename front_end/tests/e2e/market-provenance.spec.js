import {expect,test} from '@playwright/test'

test.afterEach(async({page})=>{await page.request.post('/api/auth/logout')})

test('missing history and quote provenance are visible without invented bars or popularity rankings',async({page})=>{
  await page.setViewportSize({width:1920,height:1080})
  await page.route('**/api/watch/kline?**',route=>route.fulfill({json:{code:0,data:[],total:0,
    has_more:false,data_source:'missing',synthetic_data_used:false}}))
  const sockets=[]
  await page.routeWebSocket('**/ws/watch',socket=>{
    sockets.push(socket)
    socket.onMessage(raw=>{
      if(raw==='ping'){socket.send('pong');return}
      const message=JSON.parse(String(raw))
      if(message.type==='subscribe')socket.send(JSON.stringify({type:'tick',symbol:'E2E2026',
        last:100,bid1:99,ask1:101,time:'10:00:00（隔离样本）',
        timestamp:new Date(Date.now()-120000).toISOString(),source:'vnpy'}))
    })
  })
  await page.goto('/login')
  expect((await page.request.post('/api/auth/login',{data:{username:'E2E_ONLY',password:'fixture'}})).status()).toBe(200)
  await page.evaluate(()=>{
    sessionStorage.setItem('quant_session_active','1')
    localStorage.setItem('quant_cur_symbol',JSON.stringify({symbol:'E2E2026',name:'来源验收样本',exchange:'TEST'}))
  })
  await page.goto('/')
  await expect(page.locator('.market-channel')).toContainText('vn.py 柜台回报')
  await expect(page.locator('.quote-time')).toContainText('距行情 2 分钟')
  await expect(page.locator('.kline-source')).toContainText('未导入历史数据')
  await expect(page.locator('.chart-empty')).toContainText('暂无 K 线数据')
  await expect(page.locator('.kline-source')).toContainText('非完整交易时段')
  await page.screenshot({path:'test-results/market-provenance-fixture.png',fullPage:true})
  sockets.forEach(socket=>socket.send(JSON.stringify({type:'tick',symbol:'E2E2026',last:100})))
  await expect(page.locator('.market-channel')).toContainText('来源未标注')
  await expect(page.locator('.quote-time')).toContainText('行情时间未知')
  await page.getByRole('button',{name:'搜索合约 Ctrl K'}).click()
  await expect(page.locator('.cs-dialog')).toContainText('合约目录（非成交量排名）')
  await expect(page.locator('.cs-dialog')).not.toContainText('热门品种')
})
