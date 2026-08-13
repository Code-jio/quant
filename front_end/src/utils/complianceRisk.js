export const COMPLIANCE_THRESHOLD_FIELDS = [
  { key: 'orders_submitted', riskKey: 'order_count_alert_threshold' },
  { key: 'cancel_requests', riskKey: 'cancel_count_alert_threshold' },
  { key: 'duplicate_open', riskKey: 'duplicate_open_alert_threshold' },
  { key: 'duplicate_close', riskKey: 'duplicate_close_alert_threshold' },
  { key: 'duplicate_cancel', riskKey: 'duplicate_cancel_alert_threshold' },
]

function normalizeThreshold(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0
}

export function createComplianceThresholdDraft(thresholds = {}) {
  return Object.fromEntries(
    COMPLIANCE_THRESHOLD_FIELDS.map(({ key }) => [key, normalizeThreshold(thresholds[key])]),
  )
}

export function normalizeComplianceThresholdPatch(draft = {}) {
  return Object.fromEntries(
    COMPLIANCE_THRESHOLD_FIELDS.map(({ key, riskKey }) => [riskKey, normalizeThreshold(draft[key])]),
  )
}
