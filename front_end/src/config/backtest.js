export const DEFAULT_BACKTEST_FORM = {
  strategy_name: 'ma_cross',
  strategy_params: { symbol: 'IF9999', fast_period: 10, slow_period: 20, position_ratio: 0.8 },
  start_date: '2023-01-01',
  end_date: '2024-12-31',
  initial_capital: 1_000_000,
  commission_rate: 0.0003,
  slip_rate: 0.0001,
  margin_rate: 0.12,
  contract_multiplier: 1,
  max_errors: 100,
  sample_days: 700,
  allow_synthetic_data: false,
}

export const COMMON_PARAM_KEYS = new Set(['symbol'])

export const STRATEGY_PARAM_LABELS = {
  fast_period: '快线周期',
  slow_period: '慢线周期',
  position_ratio: '仓位比例',
  rsi_period: 'RSI 周期',
  oversold: '超卖阈值',
  overbought: '超买阈值',
  lookback_period: '回看周期',
}

export function paramLabel(key) {
  return STRATEGY_PARAM_LABELS[key] ?? key
}
