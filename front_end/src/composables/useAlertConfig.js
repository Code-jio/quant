/**
 * useAlertConfig — 盯盘告警配置 composable
 *
 * 职责：
 *  - 管理价格/成交量异动告警的阈值配置
 *  - 控制哪些类型的告警开关
 *  - 所有配置持久化到 localStorage
 *
 * 告警类型：
 *   price       — 日涨跌幅超阈值（与昨收比较）
 *   price_jump  — 单笔跳价超阈值（与上一 tick 比较）
 *   volume      — 成交量突破均量 N 倍
 *
 * 使用示例：
 *   const alertCfg = useAlertConfig()
 *   alertCfg.config.priceChangeThreshold = 3   // 改为 3% 触发
 *   alertCfg.save()
 */

import { reactive } from 'vue'

const LS_KEY = 'quant_alert_config'

const DEFAULTS = {
  /** 是否开启全部告警 */
  enabled: true,

  /** 各类型告警开关 */
  types: {
    price:      true,   // 日涨跌幅异动
    price_jump: true,   // 单笔跳价
    volume:     true,   // 成交量异动
  },

  /**
   * 日涨跌幅阈值（百分比值）
   * 例：2 表示涨跌幅 >= 2% 时触发
   */
  priceChangeThreshold: 2,

  /**
   * 单笔跳价阈值（百分比值）
   * 例：0.5 表示单笔价格变化 >= 0.5% 触发
   */
  tickJumpThreshold: 0.5,

  /**
   * 成交量异动倍数
   * 例：3 表示当前量 >= 均量 3 倍时触发
   */
  volSpikeMultiplier: 3,

  /**
   * 告警声音提示
   * 'none' | 'beep' | 'ding'
   */
  sound: 'none',

  /**
   * 相同品种相同类型的告警冷却时间（秒）
   * 防止同一品种频繁刷屏
   */
  cooldownSecs: 30,
}

// ── 工具函数 ──────────────────────────────────────────────────────────────
function readLS() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) ?? {} }
  catch { return {} }
}

function writeLS(val) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(val)) }
  catch { /* quota exceeded */ }
}

// ── 冷却记录（内存，不持久化） ─────────────────────────────────────────────
const _cooldowns = {}  // { [symbol_type]: lastTriggeredTimestamp }

// ── Composable ─────────────────────────────────────────────────────────────
export function useAlertConfig() {
  const config = reactive({ ...DEFAULTS, ...readLS(), types: { ...DEFAULTS.types, ...(readLS().types ?? {}) } })

  /** 保存当前配置到 localStorage */
  function save() {
    writeLS({ ...config, types: { ...config.types } })
  }

  /** 重置为默认值 */
  function reset() {
    Object.assign(config, DEFAULTS)
    config.types = { ...DEFAULTS.types }
    save()
  }

  /**
   * 判断某品种某类型的告警是否在冷却中
   * 内部由 useWatchWs 调用（也可外部调用）
   */
  function isCoolingDown(symbol, type) {
    const key  = `${symbol}_${type}`
    const last = _cooldowns[key] ?? 0
    return (Date.now() - last) < config.cooldownSecs * 1000
  }

  /**
   * 标记一次告警触发（更新冷却时间戳）
   */
  function markTriggered(symbol, type) {
    _cooldowns[`${symbol}_${type}`] = Date.now()
  }

  return { config, save, reset, isCoolingDown, markTriggered }
}
