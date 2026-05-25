import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchTrialRunConfig,
  fetchRiskStatus,
  fetchTrialRunStatus,
  prepareTrialRun,
  resetTrialRun,
  startTrialRun,
  stopTrialRun,
} from '@/api/index.js'

function mockFetch() {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ ok: true }),
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('trial-run api client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('calls trial-run GET endpoints', async () => {
    const fetchMock = mockFetch()

    await fetchTrialRunConfig()
    await fetchTrialRunStatus()

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/trial-run/config',
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/trial-run/status',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('calls trial-run POST endpoints with JSON bodies', async () => {
    const fetchMock = mockFetch()

    await prepareTrialRun({ symbol: 'IF9999' })
    await startTrialRun({ symbol: 'IF9999', volume: 1 })
    await stopTrialRun()
    await resetTrialRun()

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/trial-run/prepare',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ symbol: 'IF9999' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/trial-run/start',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ symbol: 'IF9999', volume: 1 }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/trial-run/stop',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      '/api/trial-run/reset',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('keeps redirectOn401 as a client-only polling option', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 401,
      json: async () => ({ detail: '未登录' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchRiskStatus({ redirectOn401: false })).rejects.toThrow('未登录')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/risk/status',
      expect.not.objectContaining({ redirectOn401: false }),
    )
  })
})
