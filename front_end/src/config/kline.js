export const INTERVALS = [
  { label: '1分', value: '1m' },
  { label: '5分', value: '5m' },
  { label: '15分', value: '15m' },
  { label: '30分', value: '30m' },
  { label: '1时', value: '1h' },
  { label: '4时', value: '4h' },
  { label: '日线', value: '1d' },
  { label: '周线', value: '1w' },
]

export const MA_COLORS = [
  '#f5c842', '#f09a00', '#f06400', '#cc4444',
  '#3b82f6', '#22c55e', '#ec4899', '#8b5cf6',
  '#06b6d4', '#f97316', '#84cc16', '#e879f9',
]

export const DEFAULT_MA_CONFIG = [
  { n: 5, color: MA_COLORS[0], visible: true, dashType: 'solid' },
  { n: 10, color: MA_COLORS[1], visible: true, dashType: 'solid' },
  { n: 20, color: MA_COLORS[2], visible: true, dashType: 'solid' },
  { n: 30, color: MA_COLORS[3], visible: true, dashType: 'solid' },
]
