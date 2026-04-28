import { describe, expect, it } from 'vitest'
import { buildApiUrl, buildWsUrl, getWsBase } from '@/config/network.js'

describe('network config helpers', () => {
  it('builds api urls without duplicate slashes', () => {
    expect(buildApiUrl('/system/status', '/api/')).toBe('/api/system/status')
    expect(buildApiUrl('watch/search', 'http://127.0.0.1:8000/')).toBe('http://127.0.0.1:8000/watch/search')
  })

  it('derives websocket protocol from current page protocol', () => {
    expect(getWsBase({ protocol: 'https:', host: 'app.example.com' })).toBe('wss://app.example.com')
    expect(getWsBase({ protocol: 'http:', host: '127.0.0.1:5173' })).toBe('ws://127.0.0.1:5173')
  })

  it('honors explicit websocket base url', () => {
    expect(buildWsUrl('/ws/system', { wsBase: 'wss://quote.example.com/' })).toBe('wss://quote.example.com/ws/system')
  })
})
