import { expect, test } from '@playwright/test'

test('public pages render without a hard crash', async ({ page }) => {
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
  await expect(page.getByRole('button', { name: '准备策略' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '授权交易' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '一键撤单' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '快捷平仓' })).toBeDisabled()
})
