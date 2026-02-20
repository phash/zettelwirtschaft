import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'

export const useAuthStore = defineStore('auth', () => {
  const pinEnabled = ref(false)
  const isAuthenticated = ref(false)
  const loading = ref(false)

  async function checkStatus() {
    try {
      const { data } = await axios.get('/api/auth/status')
      pinEnabled.value = data.pin_enabled
      isAuthenticated.value = data.authenticated
    } catch {
      pinEnabled.value = false
      isAuthenticated.value = false
    }
  }

  async function login(pin) {
    loading.value = true
    try {
      const { data } = await axios.post('/api/auth/login', { pin })
      if (data.success) {
        isAuthenticated.value = true
        return { success: true }
      }
      return { success: false, detail: data.detail || 'Falscher PIN' }
    } catch (error) {
      return { success: false, detail: error.response?.data?.detail || 'Fehler bei der Anmeldung' }
    } finally {
      loading.value = false
    }
  }

  async function logout() {
    try {
      await axios.post('/api/auth/logout')
    } catch {
      // ignore
    }
    isAuthenticated.value = false
  }

  function reset() {
    isAuthenticated.value = false
  }

  return {
    pinEnabled,
    isAuthenticated,
    loading,
    checkStatus,
    login,
    logout,
    reset,
  }
})
