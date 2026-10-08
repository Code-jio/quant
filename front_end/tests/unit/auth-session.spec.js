import { beforeEach, expect, test, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth.js'
import { fetchAuthStatus } from '@/api/index.js'

vi.mock('@/api/index.js', () => ({ fetchAuthStatus: vi.fn() }))
beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  vi.clearAllMocks()
})

test('a new tab restores its authenticated cookie without creating a broker login', async () => {
  fetchAuthStatus.mockResolvedValue({ logged_in: true, account_id: 'FIXTURE' })
  const auth = useAuthStore()
  await Promise.all([auth.restoreSession(), auth.restoreSession()])
  expect(fetchAuthStatus).toHaveBeenCalledTimes(1)
  expect(auth.isLoggedIn).toBe(true)
  expect(auth.accountId).toBe('FIXTURE')
  expect(sessionStorage.getItem('quant_session_active')).toBe('1')
})

test('an expired cookie cannot be replaced by an old tab-local flag', async () => {
  sessionStorage.setItem('quant_session_active', '1')
  fetchAuthStatus.mockResolvedValue({ logged_in: false })
  const auth = useAuthStore()
  await auth.restoreSession()
  expect(auth.isLoggedIn).toBe(false)
  expect(sessionStorage.getItem('quant_session_active')).toBeNull()
})

test('a status request failure grants no access and stores no credential', async () => {
  fetchAuthStatus.mockRejectedValue(new Error('offline'))
  const auth = useAuthStore()
  await auth.restoreSession()
  expect(auth.isLoggedIn).toBe(false)
  expect(sessionStorage.length).toBe(0)
})
