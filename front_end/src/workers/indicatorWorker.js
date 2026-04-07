/**
 * indicatorWorker.js — 在 Web Worker 线程中执行技术指标计算
 *
 * 支持的计算类型（type 字段）：
 *   calc_ma    — 多周期移动平均线
 *   calc_macd  — MACD（EMA 差离 / 信号线 / 柱状图）
 *   calc_rsi   — RSI（简单平均法）
 *   calc_kdj   — KDJ（随机指标）
 *   calc_all   — 一次性计算上述全部指标
 *
 * 消息格式（双向均为 JSON-serializable plain object）：
 *   in  → { id: number, type: string, payload: object }
 *   out → { id: number, result: object }  |  { id: number, error: string }
 */

// ── 计算函数 ──────────────────────────────────────────────────────────────

/** 简单移动平均 */
function calcMA(closes, period) {
  const result = new Array(closes.length).fill(null)
  for (let i = period - 1; i < closes.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += closes[j]
    result[i] = sum / period
  }
  return result
}

/** 指数移动平均（初始值取第一个收盘价） */
function ema(data, period) {
  const k      = 2 / (period + 1)
  const result = new Array(data.length).fill(null)
  let   prev   = null
  for (let i = 0; i < data.length; i++) {
    const v = data[i]
    if (v == null) { result[i] = prev; continue }
    if (prev === null) { result[i] = v; prev = v; continue }
    const cur = v * k + prev * (1 - k)
    result[i] = cur
    prev       = cur
  }
  return result
}

/** MACD — DIF / DEA / HIST */
function calcMACD(closes, fast = 12, slow = 26, signal = 9) {
  const emaFast = ema(closes, fast)
  const emaSlow = ema(closes, slow)
  const diff    = emaFast.map((v, i) =>
    v != null && emaSlow[i] != null ? v - emaSlow[i] : null
  )
  const dea  = ema(diff.map(v => v ?? 0), signal)
  const hist = diff.map((v, i) =>
    v != null && dea[i] != null ? (v - dea[i]) * 2 : null
  )
  return { diff, dea, hist }
}

/** RSI — 简单平均法（Wilder 平滑） */
function calcRSI(closes, period = 14) {
  const result = new Array(closes.length).fill(null)
  for (let i = period; i < closes.length; i++) {
    let gains = 0, losses = 0
    for (let j = i - period + 1; j <= i; j++) {
      const chg = closes[j] - closes[j - 1]
      if (chg > 0) gains  += chg
      else         losses -= chg
    }
    const rs = gains / (losses || 1e-9)
    result[i] = 100 - 100 / (1 + rs)
  }
  return result
}

/** KDJ — 随机指标 */
function calcKDJ(highs, lows, closes, n = 9, m1 = 3, m2 = 3) {
  const k = new Array(closes.length).fill(null)
  const d = new Array(closes.length).fill(null)
  const j = new Array(closes.length).fill(null)
  let prevK = 50, prevD = 50

  for (let i = n - 1; i < closes.length; i++) {
    let hh = -Infinity, ll = Infinity
    for (let x = i - n + 1; x <= i; x++) {
      hh = Math.max(hh, highs[x])
      ll = Math.min(ll, lows[x])
    }
    const rsv = hh === ll ? 50 : (closes[i] - ll) / (hh - ll) * 100
    const kv  = (m1 - 1) / m1 * prevK + 1 / m1 * rsv
    const dv  = (m2 - 1) / m2 * prevD + 1 / m2 * kv
    k[i]  = kv
    d[i]  = dv
    j[i]  = 3 * kv - 2 * dv
    prevK = kv
    prevD = dv
  }
  return { k, d, j }
}

// ── 消息处理 ──────────────────────────────────────────────────────────────

self.onmessage = ({ data }) => {
  const { id, type, payload } = data
  let result

  try {
    switch (type) {

      case 'calc_ma': {
        const { closes, periods } = payload
        result = {}
        for (const p of periods) result[`ma${p}`] = calcMA(closes, p)
        break
      }

      case 'calc_macd': {
        const { closes, fast = 12, slow = 26, signal = 9 } = payload
        result = calcMACD(closes, fast, slow, signal)
        break
      }

      case 'calc_rsi': {
        const { closes, period = 14 } = payload
        result = calcRSI(closes, period)
        break
      }

      case 'calc_kdj': {
        const { highs, lows, closes, n = 9, m1 = 3, m2 = 3 } = payload
        result = calcKDJ(highs, lows, closes, n, m1, m2)
        break
      }

      // 一次性计算全部指标（减少 Worker 通信次数）
      case 'calc_all': {
        const { bars, maParams = {}, macdParams = {}, kdjParams = {}, rsiParams = {} } = payload
        const closes = bars.map(b => b.close)
        const highs  = bars.map(b => b.high)
        const lows   = bars.map(b => b.low)
        const vols   = bars.map(b => b.volume)

        const periods   = maParams.periods   ?? [5, 10, 20, 30]
        const volPeriod = maParams.volPeriod ?? 5

        const maResult = {}
        for (const p of periods) maResult[`ma${p}`] = calcMA(closes, p)

        result = {
          ma:    maResult,
          volMa: calcMA(vols, volPeriod),
          macd:  calcMACD(closes, macdParams.fast ?? 12, macdParams.slow ?? 26, macdParams.signal ?? 9),
          rsi:   calcRSI(closes, rsiParams.period ?? 14),
          kdj:   calcKDJ(highs, lows, closes, kdjParams.n ?? 9, kdjParams.m1 ?? 3, kdjParams.m2 ?? 3),
        }
        break
      }

      default:
        throw new Error(`未知指令: ${type}`)
    }

    self.postMessage({ id, result })
  } catch (err) {
    self.postMessage({ id, error: err.message })
  }
}
