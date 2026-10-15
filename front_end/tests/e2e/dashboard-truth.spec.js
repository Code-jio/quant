import {expect,test} from '@playwright/test'

test.afterEach(async({page})=>{await page.request.post('/api/auth/logout')})

test('account views distinguish broker zero, unknown metrics, equity and cost notional',async({page})=>{
  const errors=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.setViewportSize({width:1920,height:1080})
  const sockets=[]
  let payload={type:'dashboard_metrics',account_id:'E2E_FIXTURE',account_known:true,account_source:'gateway',
    trading_day:'20261015',balance:100000,available:90000,margin:10000,commission:5,
    total_pnl:0,position_pnl:-300,return_rate:null,today_return:null,initial_capital:null,
    session_balance_change_pct:0,sharpe_ratio:null,max_drawdown_pct:null,positions_known:true,
    total_exposure:1600000,exposure_pct:1600,exposure_basis:'cost_notional',
    positions:[{symbol:'FIXTURE',direction:'long',volume:2,cost:800,pnl:-300,notional:1600000}],
    equity_curve:[{ts:'10:00:00',v:100000},{ts:'10:00:01',v:99000}],timestamp:'2026-10-15T10:00:01+08:00',
    performance_note:'隔离测试样本；会话权益变动及窗口回撤未剔除出入金'}
  await page.routeWebSocket('**/ws/dashboard',socket=>{
    sockets.push(socket)
    socket.send(JSON.stringify(payload))
    socket.onMessage(message=>{if(message==='ping')socket.send('pong')})
  })
  await page.goto('/login')
  expect((await page.request.post('/api/auth/login',{data:{username:'E2E_ONLY',password:'fixture'}})).status()).toBe(200)
  await page.evaluate(()=>sessionStorage.setItem('quant_session_active','1'))
  await page.goto('/')
  const strip=page.getByRole('region',{name:'账户资金概览'})
  await expect(strip).toContainText('-300.00')
  await expect(strip).toContainText('1,600.00%')
  await strip.getByRole('button',{name:'资金分析'}).click()
  const dashboard=page.locator('.gd-wrap')
  await expect(dashboard).toContainText('持仓 + 平仓盈亏')
  await expect(dashboard.locator('.kpi-card').nth(2)).toContainText('+0.00')
  await expect(dashboard).toContainText('有效样本不足')
  await expect(dashboard.locator('.pos-table')).toContainText('1,600,000')
  await expect(dashboard).not.toContainText('累计收益率')
  await expect(dashboard).not.toContainText('保证金占用')
  await expect.poll(()=>dashboard.locator('.chart-canvas canvas').count()).toBe(2)
  await page.screenshot({path:'test-results/dashboard-truth-known-fixture.png',fullPage:true})
  payload={...payload,account_known:false,balance:null,total_pnl:null,position_pnl:null,
    positions_known:false,positions:[],equity_curve:[],exposure_pct:null}
  sockets.forEach(socket=>socket.send(JSON.stringify(payload)))
  await expect(strip.locator('strong')).toHaveText(['—','—','—','—','—'])
  await expect(dashboard).toContainText('柜台数据未就绪')
  await expect(dashboard.locator('.kpi-val')).toHaveText(['—','—','—','—','—','—'])
  await expect(dashboard).toContainText('持仓快照未就绪')
  await page.screenshot({path:'test-results/dashboard-truth-unknown-fixture.png',fullPage:true})
  expect(errors).toEqual([])
  await page.request.post('/api/auth/logout')
})
