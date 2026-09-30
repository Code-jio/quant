import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const source = readFileSync(resolve(process.cwd(), 'src/components/TradingPanel.vue'), 'utf8')

function functionBody(name, nextName) {
  const start = source.indexOf(`async function ${name}()`)
  const end = source.indexOf(`async function ${nextName}()`, start)
  return source.slice(start, end)
}

describe('实盘操作面板', () => {
  it('一分钟状态轮询不触发 12 次券商权威对账', () => {
    const pollBody = functionBody('loadRiskStatus', 'handleManualReconcile')
    const callsPerMinute = 60_000 / 5_000

    expect(callsPerMinute).toBe(12)
    expect(source).toContain('riskTimer = setInterval(loadRiskStatus, 5000)')
    expect(pollBody).toContain('fetchRiskStatus()')
    expect(pollBody).toContain('fetchSystemStatus()')
    expect(pollBody).not.toContain('fetchTradingReconcile')
    expect(functionBody('handleManualReconcile', 'handleEmergencyStop')).toContain('fetchTradingReconcile()')
    expect(source).toContain('刷新核对')
  })

  it('急停存在待确认或失败撤单时展示告警，而不是成功提示', () => {
    const emergencyBody = functionBody('handleEmergencyStop', 'handleResumeTrading')

    expect(emergencyBody).toContain('normalizeEmergencyFeedback(res)')
    expect(emergencyBody).toMatch(
      /if \(feedback\.pending > 0 \|\| feedback\.failed > 0\) \{\s*ElMessage\.warning\(`急停已激活；撤单未全部确认/, 
    )
    expect(emergencyBody).toMatch(
      /else if \(feedback\.requested > 0\) \{\s*ElMessage\.success\(`急停已激活；撤单全部确认/,
    )
    expect(source).toContain('已请求 {{ emergencyFeedback.requested }}')
    expect(source).toContain('已确认撤单 {{ emergencyFeedback.confirmed }}')
    expect(source).toContain('待确认 {{ emergencyFeedback.pending }}')
    expect(source).toContain('失败 {{ emergencyFeedback.failed }}')
    expect(source).toContain('v-for="outcome in emergencyFeedback.outcomes"')
  })

  it('解除急停成功后清除急停撤单反馈，避免残留激活提示', () => {
    const resumeBody = functionBody('handleResumeTrading', 'handleClosePosition')

    expect(resumeBody).toMatch(
      /await resumeTrading\(\)\s*emergencyFeedback\.value = null\s*ElMessage\.success\('交易急停已解除'\)/,
    )
  })
})
