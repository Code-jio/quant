import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const TOKEN_KEY   = 'quant_token'
const ACCOUNT_KEY = 'quant_account_id'
const SESSION_KEY = 'quant_session_active'

export const useAuthStore = defineStore('auth', () => {
  const token     = ref(sessionStorage.getItem(TOKEN_KEY)   ?? '')
  const accountId = ref(sessionStorage.getItem(ACCOUNT_KEY) ?? '')
  const balance   = ref(0)
  const sessionActive = ref(sessionStorage.getItem(SESSION_KEY) === '1')

  const isLoggedIn = computed(() => sessionActive.value || !!token.value)

  function setAuth({ token: t, accountId: aid, balance: bal = 0 }) {
    token.value     = t
    accountId.value = aid
    balance.value   = bal
    sessionActive.value = true
    if (t) sessionStorage.setItem(TOKEN_KEY, t)
    sessionStorage.setItem(ACCOUNT_KEY, aid)
    sessionStorage.setItem(SESSION_KEY, '1')
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(ACCOUNT_KEY)
  }

  function clearAuth() {
    token.value     = ''
    accountId.value = ''
    balance.value   = 0
    sessionActive.value = false
    sessionStorage.removeItem(TOKEN_KEY)
    sessionStorage.removeItem(ACCOUNT_KEY)
    sessionStorage.removeItem(SESSION_KEY)
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(ACCOUNT_KEY)
  }

  return { token, accountId, balance, isLoggedIn, setAuth, clearAuth }
})
