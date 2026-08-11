import { afterEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import { useSystemWs } from '@/composables/useSystemWs.js'

class FakeWebSocket {
  static OPEN = 1
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = FakeWebSocket.OPEN
    FakeWebSocket.instances.push(this)
  }

  close() {}
  send() {}
}

afterEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  vi.unstubAllGlobals()
})

describe('live order-entry readiness UI contracts', () => {
  it('maps all broker order-entry readiness fields from the system websocket', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const wrapper = mount({
      setup() {
        return useSystemWs('ws://unit.test/ws/system')
      },
      template: '<output>{{ data.contractsReady }}|{{ data.reconciliationReady }}|{{ data.tradingDay }}|{{ data.orderEntryReady }}</output>',
    })

    FakeWebSocket.instances[0].onmessage({
      data: JSON.stringify({
        type: 'system_status',
        contracts_ready: true,
        reconciliation_ready: true,
        trading_day: '2026-08-12',
        order_entry_ready: true,
      }),
    })
    await nextTick()

    expect(wrapper.text()).toBe('true|true|2026-08-12|true')
  })

  it('binds the submit button to broker readiness and emergency-stop reasons', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/TradingPanel.vue'), 'utf8')

    expect(source).toContain('const orderEntryReady = computed(')
    expect(source).toContain('const orderEntryBlockReason = computed(')
    expect(source).toContain(':disabled="!canSubmit || !orderEntryReady || emergencyActive"')
    expect(source).toContain('{{ orderEntryBlockReason }}')
    expect(source).toContain('券商对账')
    expect(source).toContain('急停')
  })

  it('renders contracts, broker reconciliation, and order-entry permission in SystemMonitor', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/SystemMonitor.vue'), 'utf8')

    expect(source).toContain('合约就绪')
    expect(source).toContain('券商对账')
    expect(source).toContain('允许报单')
    expect(source).toContain('data.contractsReady')
    expect(source).toContain('data.reconciliationReady')
    expect(source).toContain('data.orderEntryReady')
  })

  it('never substitutes mock strategies when live dashboard data is unavailable', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/views/DashboardView.vue'), 'utf8')

    expect(source).not.toContain('MOCK_STRATEGIES')
    expect(source).not.toContain('当前显示模拟数据')
    expect(source).toContain('实盘数据不可用')
    expect(source).toContain('strategies.value = []')
  })
})
