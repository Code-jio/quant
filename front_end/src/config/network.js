const env = import.meta.env ?? {}

export const API_BASE = env.VITE_API_BASE_URL ?? '/api'
export const WS_BASE = env.VITE_WS_BASE_URL ?? ''
export const WS_HOST = env.VITE_WS_HOST ?? ''

function trimTrailingSlash(value) {
  return String(value || '').replace(/\/+$/, '')
}

function ensureLeadingSlash(path) {
  return String(path || '').startsWith('/') ? String(path) : `/${path}`
}

export function buildApiUrl(path, base = API_BASE) {
  return `${trimTrailingSlash(base)}${ensureLeadingSlash(path)}`
}

export function getWsBase({
  protocol = globalThis.location?.protocol ?? 'http:',
  host = globalThis.location?.host ?? 'localhost',
  wsBase = WS_BASE,
  wsHost = WS_HOST,
} = {}) {
  if (wsBase) return trimTrailingSlash(wsBase)
  const wsProtocol = protocol === 'https:' ? 'wss' : 'ws'
  return `${wsProtocol}://${wsHost || host}`
}

export function buildWsUrl(path, options = {}) {
  return `${getWsBase(options)}${ensureLeadingSlash(path)}`
}
