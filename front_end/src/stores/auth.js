import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const TOKEN_KEY   = 'quant_token'
const ACCOUNT_KEY = 'quant_account_id'

export const useAuthStore = defineStore('auth', () => {
  const token     = ref(localStorage.getItem(TOKEN_KEY)   ?? '')
  const accountId = ref(localStorage.getItem(ACCOUNT_KEY) ?? '')
  const balance   = ref(0)

  const isLoggedIn = computed(() => !!token.value)

  function setAuth({ token: t, accountId: aid, balance: bal = 0 }) {
    token.value     = t
    accountId.value = aid
    balance.value   = bal
    localStorage.setItem(TOKEN_KEY,   t)
    localStorage.setItem(ACCOUNT_KEY, aid)
  }

  function clearAuth() {
    token.value     = ''
    accountId.value = ''
    balance.value   = 0
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(ACCOUNT_KEY)
  }

  return { token, accountId, balance, isLoggedIn, setAuth, clearAuth }
})
