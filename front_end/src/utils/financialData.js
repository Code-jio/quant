// Financial observations must preserve unknown values, including explicit nulls.
export const finiteAmount = value => typeof value === 'number' && Number.isFinite(value) ? value : null
export const formatAmount = (value, digits = 2) => finiteAmount(value) == null ? '—'
  : value.toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits})
export const formatSigned = (value, digits = 2) => finiteAmount(value) == null ? '—'
  : `${value >= 0 ? '+' : ''}${formatAmount(value, digits)}`
export const formatPercent = (value, digits = 2) => finiteAmount(value) == null ? '—' : `${value.toFixed(digits)}%`
