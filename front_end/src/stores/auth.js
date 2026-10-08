import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { fetchAuthStatus } from '@/api/index.js'

const ACCOUNT_KEY = 'quant_account_id'
const SESSION_KEY = 'quant_session_active'

export const useAuthStore = defineStore('auth', () => {
  const accountId = ref(sessionStorage.getItem(ACCOUNT_KEY) ?? '')
  const balance   = ref(0)
  const sessionActive = ref(sessionStorage.getItem(SESSION_KEY) === '1')
  let restored = false
  let restoring = null

  const isLoggedIn = computed(() => sessionActive.value)

  function setAuth({ accountId: aid, balance: bal = 0 }) {
    restored = true
    accountId.value = aid
    balance.value   = bal
    sessionActive.value = true
    sessionStorage.setItem(ACCOUNT_KEY, aid)
    sessionStorage.setItem(SESSION_KEY, '1')
    localStorage.removeItem(ACCOUNT_KEY)
    window.dispatchEvent(new CustomEvent("quant-auth-change"))
  }

  function clearAuth() {
    restored = true
    accountId.value = ''
    balance.value   = 0
    sessionActive.value = false
    sessionStorage.removeItem(ACCOUNT_KEY)
    sessionStorage.removeItem(SESSION_KEY)
    localStorage.removeItem(ACCOUNT_KEY)
    window.dispatchEvent(new CustomEvent("quant-auth-change"))
  }

  async function restoreSession() {
    if (restored) return
    if (!restoring) {
      restoring = (async () => {
        try {
          const status = await fetchAuthStatus()
          if (status.logged_in) setAuth({ accountId: status.account_id })
          else clearAuth()
        } catch {
          // The cookie is the authority; a tab-local flag never grants access.
          clearAuth()
        } finally { restoring = null }
      })()
    }
    await restoring
  }

  return { accountId, balance, isLoggedIn, setAuth, clearAuth, restoreSession }
})
