/**
 * useHotkeys — 全局快捷键注册 Composable
 *
 * 使用示例：
 *   useHotkeys([
 *     { key: 'ArrowLeft',  handler: () => prevPeriod() },
 *     { key: ['f', 'F'],   handler: () => toggleFullscreen() },
 *     { key: 'r',          ctrl: false, handler: () => reload() },
 *     { key: 'k',          ctrl: true,  handler: () => openSearch() },
 *   ])
 *
 * 规则：
 *  - 当焦点在 input / textarea / contenteditable 时，忽略非修饰键组合
 *  - 支持 ctrl / alt / shift 修饰键
 *  - key 可传单个字符串或数组（匹配任一即触发）
 */

import { onMounted, onUnmounted } from 'vue'

/**
 * @typedef {Object} HotkeyDef
 * @property {string|string[]} key      — KeyboardEvent.key 值（大小写敏感，可用数组指定多个）
 * @property {boolean}         [ctrl]   — 是否需要 Ctrl/Cmd，默认 false
 * @property {boolean}         [alt]    — 是否需要 Alt，默认 false
 * @property {boolean}         [shift]  — 是否需要 Shift，默认 false
 * @property {boolean}         [allowInInput] — 输入框内是否也触发，默认 false
 * @property {Function}        handler  — 回调，接收 KeyboardEvent
 */

/**
 * 注册快捷键列表。
 * 组件卸载时自动移除监听。
 *
 * @param {HotkeyDef[]} hotkeys
 */
export function useHotkeys(hotkeys) {
  function onKeydown(e) {
    for (const hk of hotkeys) {
      const keys    = Array.isArray(hk.key) ? hk.key : [hk.key]
      const ctrl    = hk.ctrl  ?? false
      const alt     = hk.alt   ?? false
      const shift   = hk.shift ?? false
      const allowIn = hk.allowInInput ?? false

      // 焦点在文本输入框时跳过（除非显式允许）
      if (!allowIn) {
        const tag = document.activeElement?.tagName
        if (
          tag === 'INPUT' ||
          tag === 'TEXTAREA' ||
          tag === 'SELECT' ||
          document.activeElement?.isContentEditable
        ) continue
      }

      if (!keys.some(k => k === e.key || k.toLowerCase() === e.key.toLowerCase())) continue
      if (ctrl  !== (e.ctrlKey  || e.metaKey)) continue
      if (alt   !== e.altKey)  continue
      if (shift !== e.shiftKey) continue

      e.preventDefault()
      hk.handler(e)
      break
    }
  }

  onMounted(()   => window.addEventListener('keydown', onKeydown))
  onUnmounted(() => window.removeEventListener('keydown', onKeydown))
}

// ── 预设的 K 线图表快捷键描述（用于显示帮助面板） ──────────────────────────

export const KLINE_SHORTCUTS = [
  { keys: '← →',       desc: '切换到上 / 下一个周期' },
  { keys: '1 ~ 8',     desc: '快速选择周期（1分/5分/15分/30分/1H/4H/日线/周线）' },
  { keys: 'R',         desc: '刷新当前 K 线数据' },
  { keys: 'F',         desc: '切换全屏模式' },
  { keys: '+ / -',     desc: '放大 / 缩小图表' },
  { keys: 'Home',      desc: '回到最新 K 线（重置视图）' },
  { keys: 'Ctrl + K',  desc: '打开合约搜索' },
  { keys: 'Esc',       desc: '退出全屏 / 关闭弹窗' },
]
