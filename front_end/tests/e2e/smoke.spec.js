import { expect, test } from '@playwright/test'

const BASE_CONFIG = {
  enabled: true,
  ready: true,
  valid: true,
  config_source: 'config.example.json',
  allowed_symbol: 'rb2610',
  environment: '测试',
  auto_arm: true,
  simulate_fill_enabled: true,
  masked_account_id: 'TEST001',
  strategy: { symbol: 'rb2610', volume: 1 },
  trading: { gateway: 'vnpy', environment: '测试' },
  trial_run: { simulate_fill_enabled: true, no_fill_timeout_seconds: 2, max_hold_seconds: 75 },
  risk: { max_orders_per_minute: 5, allowed_symbols: ['rb2610'] },
}

const BASE_STATUS = {
  state: 'running',
  status: 'running',
  run_id: 'RUN-E2E',
  outcome: 'running',
  current_track: 'simulated',
  current_order_id: '',
  order_chain: [],
  simulation_state: 'not_started',
  simulation_prepare_allowed: false,
  simulate_fill_allowed: false,
  broker_position_volume: 0,
  simulated_position_volume: 0,
  broker_active_order_ids: [],
  reconcile_ok: true,
  rate_limit_remaining: 3,
  rate_limit_retry_after_seconds: 0,
  failure_code: '',
  hold_deadline_at: '',
  symbol: 'rb2610',
  allowed_symbol: 'rb2610',
  connected: true,
  gateway_connected: true,
  prepared: true,
  started: true,
  market_ready: true,
  auto_arm: true,
  tick_count: 2,
  bar_count: 1,
  first_tick_bar_enabled: true,
  first_tick_bar_emitted: true,
  simulate_fill_enabled: true,
}

const SIM_READY = {
  ...BASE_STATUS,
  current_order_id: 'SIM-E-1',
  simulation_state: 'ready',
  simulate_fill_allowed: true,
  order_chain: [{
    order_id: 'SIM-E-1',
    role: 'entry',
    track: 'simulated',
    attempt: 0,
    parent_order_id: '',
    symbol: 'rb2610',
    direction: 'long',
    offset: 'open',
    price: 3130,
    volume: 1,
    status: 'submitted',
    created_at: '2026-08-10T09:30:00+00:00',
    updated_at: '2026-08-10T09:30:00+00:00',
  }],
}

const PASSED_SIMULATED = {
  ...BASE_STATUS,
  outcome: 'passed_simulated',
  success_basis: 'real_submission_and_simulated_entry_close_proof',
  simulation_state: 'flat',
  current_order_id: '',
  order_chain: [
    { ...SIM_READY.order_chain[0], track: 'real', order_id: 'ORDER_1', status: 'cancelled' },
    { ...SIM_READY.order_chain[0], order_id: 'SIM-E-1', status: 'filled' },
    { ...SIM_READY.order_chain[0], role: 'exit', offset: 'close', direction: 'short', order_id: 'SIM-C-1', status: 'filled' },
  ],
}

const ABORTED = {
  ...BASE_STATUS,
  outcome: 'aborted',
  failure_code: 'backend_restarted',
  state: 'aborted',
}

function apiBody(url) {
  const path = new URL(url).pathname
  if (path.endsWith('/trial-run/status')) return PASSED_SIMULATED
  if (path.endsWith('/trial-run/config')) return BASE_CONFIG
  if (path.endsWith('/auth/status')) return { logged_in: true, account_id: 'TEST001', gateway_connected: true, connected: true, status: 'connected' }
  if (path.endsWith('/risk/status')) return {
    connected: true,
    gateway_status: 'trading',
    last_reject_reason: '重复撤单请求已拒绝',
    risk: {
      max_orders_per_minute: 5,
      allowed_symbols: ['rb2610'],
      rate_limit_remaining: 3,
      compliance: {
        trading_day: '2026-08-11',
        counters: {
          orders_submitted: 12,
          cancel_requests: 4,
          cancels_accepted: 3,
          duplicate_open: 1,
          duplicate_close: 0,
          duplicate_cancel: 2,
        },
        thresholds: {
          orders_submitted: 500,
          cancel_requests: 300,
          duplicate_open: 1,
          duplicate_close: 1,
          duplicate_cancel: 1,
        },
        alerts: [{
          counter: 'duplicate_cancel',
          count: 2,
          threshold: 1,
          timestamp: '2026-08-11T09:30:00+08:00',
          message: '重复撤单达到告警阈值',
        }],
      },
    },
  }
  if (path.endsWith('/trading/reconcile')) return { connected: true, account: { balance: 100000, available: 100000 }, orders: { active_count: 0, total_count: 0, active: [] }, positions: { count: 0, items: [] } }
  if (path.endsWith('/system/logs')) return { logs: [] }
  return { code: 0, data: [], total: 0 }
}

function fixtureFor(route) {
  const body = apiBody(route.request().url())
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

test('public pages render without a hard crash', async ({ page }) => {
  const apiErrors = []
  page.on('console', message => {
    const text = message.text()
    if (message.type() === 'error' || text.includes('[Quant API Error]') || text.includes('[Quant API Network Error]')) {
      apiErrors.push(text)
    }
  })
    await page.route('**/*', route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    return fixtureFor(route)
  })
  await page.addInitScript(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  await page.goto('/login')
  await expect(page.locator('.login-page')).toBeVisible()
  await expect(page.getByRole('heading', { name: '量化交易系统' })).toBeVisible()

  await page.goto('/backtest')
  await expect(page.locator('.bt-page')).toBeVisible()
  await expect(page.getByText('回测与分析').first()).toBeVisible()

  await page.goto('/trial-run')
  await expect(page.locator('.trial-run-page')).toBeVisible()
  await expect(page.getByRole('heading', { name: '试运行操作台' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导出测试报告' })).toBeEnabled()
  await expect(apiErrors).toEqual([])
})

test('trial-run exposes the dual-track simulation controls and terminal conclusion', async ({ page }) => {
  const apiErrors = []
  page.on('console', message => {
    const text = message.text()
    if (message.type() === 'error' || text.includes('[Quant API Error]') || text.includes('[Quant API Network Error]')) {
      apiErrors.push(text)
    }
  })
  await page.route('**/*', async route => {
    const url = route.request().url()
    const path = new URL(url).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.endsWith('/trial-run/status')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SIM_READY) })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(apiBody(url)) })
  })

  await page.goto('/trial-run')
  await expect(page.locator('.simulation-panel')).toBeVisible()
  await expect(page.getByRole('button', { name: '模拟开仓成交' })).toBeEnabled()
  await expect(page.getByText('等待模拟平仓委托')).toBeVisible()
  await expect(page.getByRole('button', { name: '导出测试报告' })).toBeDisabled()
  await expect(apiErrors).toEqual([])
})

test('login page suppresses optional server preflight errors', async ({ page }) => {
  const apiErrors = []
  page.on('console', message => {
    const text = message.text()
    if (text.includes('[Quant API Error]') || text.includes('[Quant API Network Error]')) {
      apiErrors.push(text)
    }
  })
  await page.route('**/*', async route => {
    const url = route.request().url()
    const path = new URL(url).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.endsWith('/auth/servers') || path.endsWith('/auth/status')) {
      await route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: 'HTTP 502' }) })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(apiBody(url)) })
  })

  await page.goto('/login')
  await expect(page.locator('.login-page')).toBeVisible()
  await expect(page.getByRole('heading', { name: '量化交易系统' })).toBeVisible()
  await expect(apiErrors).toEqual([])
})

test('system page renders live compliance counters and threshold alerts', async ({ page }) => {
  await page.route('**/*', route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    return fixtureFor(route)
  })

  await page.goto('/system')
  await expect(page.locator('.compliance-monitor')).toBeVisible()
  await expect(page.getByText('实盘合规监控')).toBeVisible()
  await expect(page.locator('.counter-card').filter({ hasText: '已提交委托' })).toContainText('12')
  await expect(page.locator('.counter-card').filter({ hasText: '重复撤单' })).toContainText('2')
  await expect(page.getByText('2 / 1')).toBeVisible()
  await expect(page.getByText('最近一次风控拒绝：重复撤单请求已拒绝')).toBeVisible()
})

test('trial-run terminal aborted state does not redirect polling', async ({ page }) => {
  const apiErrors = []
  page.on('console', message => {
    const text = message.text()
    if (message.type() === 'error' || text.includes('[Quant API Error]') || text.includes('[Quant API Network Error]')) {
      apiErrors.push(text)
    }
  })
  await page.route('**/*', async route => {
    const url = route.request().url()
    const path = new URL(url).pathname
    if (!path.startsWith('/api/')) return route.continue()
    const body = path.endsWith('/trial-run/status') ? ABORTED : apiBody(url)
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })

  await page.goto('/trial-run')
  await expect(page.locator('.trial-run-page')).toBeVisible()
  await expect(page.getByText('试运行中止').first()).toBeVisible()
  await expect(page.getByRole('button', { name: '导出测试报告' })).toBeEnabled()
  await expect(apiErrors).toEqual([])
})
