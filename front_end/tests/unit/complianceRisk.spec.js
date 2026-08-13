import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  COMPLIANCE_THRESHOLD_FIELDS,
  createComplianceThresholdDraft,
  normalizeComplianceThresholdPatch,
} from '@/utils/complianceRisk.js'

describe('compliance risk threshold helpers', () => {
  it('maps only the five compliance thresholds to the risk config fields', () => {
    expect(COMPLIANCE_THRESHOLD_FIELDS).toEqual([
      { key: 'orders_submitted', riskKey: 'order_count_alert_threshold' },
      { key: 'cancel_requests', riskKey: 'cancel_count_alert_threshold' },
      { key: 'duplicate_open', riskKey: 'duplicate_open_alert_threshold' },
      { key: 'duplicate_close', riskKey: 'duplicate_close_alert_threshold' },
      { key: 'duplicate_cancel', riskKey: 'duplicate_cancel_alert_threshold' },
    ])
  })

  it('creates a non-negative integer draft and treats invalid threshold values as disabled', () => {
    expect(createComplianceThresholdDraft({
      orders_submitted: 10.8,
      cancel_requests: -1,
      duplicate_open: '5',
      duplicate_close: 'invalid',
      duplicate_cancel: null,
    })).toEqual({
      orders_submitted: 10,
      cancel_requests: 0,
      duplicate_open: 5,
      duplicate_close: 0,
      duplicate_cancel: 0,
    })
  })

  it('normalizes the save patch to exactly the five supported risk config fields', () => {
    expect(normalizeComplianceThresholdPatch({
      orders_submitted: '7.9',
      cancel_requests: -10,
      duplicate_open: 2,
      duplicate_close: undefined,
      duplicate_cancel: '3',
      unrelated_risk_setting: 999,
    })).toEqual({
      order_count_alert_threshold: 7,
      cancel_count_alert_threshold: 0,
      duplicate_open_alert_threshold: 2,
      duplicate_close_alert_threshold: 0,
      duplicate_cancel_alert_threshold: 3,
    })
  })
})

describe('ComplianceMonitor source contract', () => {
  it('uses the risk config updater and exposes threshold settings actions', async () => {
    const source = await readFile(resolve(process.cwd(), 'src/components/ComplianceMonitor.vue'), 'utf8')

    expect(source).toContain('updateRiskConfig')
    expect(source).toContain('normalizeComplianceThresholdPatch')
    expect(source).toContain('openThresholdSettings')
    expect(source).toContain('saveThresholdSettings')
    expect(source).toContain('cancelThresholdSettings')
    expect(source).toContain('阈值设置')
    expect(source).toContain('保存设置')
    expect(source).toContain('取消')
  })
})
