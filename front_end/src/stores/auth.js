import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const ACCOUNT_KEY = 'quant_account_id'
const SESSION_KEY = 'quant_session_active'

export const useAuthStore = defineStore('auth', () => {
  const accountId = ref(sessionStorage.getItem(ACCOUNT_KEY) ?? '')
  const balance   = ref(0)
  const sessionActive = ref(sessionStorage.getItem(SESSION_KEY) === '1')

  const isLoggedIn = computed(() => sessionActive.value)

  function setAuth({ accountId: aid, balance: bal = 0 }) {
    accountId.value = aid
    balance.value   = bal
    sessionActive.value = true
    sessionStorage.setItem(ACCOUNT_KEY, aid)
    sessionStorage.setItem(SESSION_KEY, '1')
    localStorage.removeItem(ACCOUNT_KEY)
  }

  function clearAuth() {
    accountId.value = ''
    balance.value   = 0
    sessionActive.value = false
    sessionStorage.removeItem(ACCOUNT_KEY)
    sessionStorage.removeItem(SESSION_KEY)
    localStorage.removeItem(ACCOUNT_KEY)
  }

  return { accountId, balance, isLoggedIn, setAuth, clearAuth }
})
