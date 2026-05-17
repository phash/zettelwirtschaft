import { defineStore } from 'pinia'
import { ref } from 'vue'
// Raw axios statt api.js um Zirkulaere Abhaengigkeit zu vermeiden:
// api.js 401-Interceptor importiert auth store → auth store darf nicht api.js importieren
import axios from 'axios'

export const useAuthStore = defineStore('auth', () => {
  const pinEnabled = ref(false)
  const isAuthenticated = ref(false)
  const loading = ref(false)
  // B1: Backend setzt pin_warning=true wenn das System ohne PIN-Schutz laeuft.
  // Frontend zeigt einen prominenten Banner damit Heim-User die Lage erkennen.
  const pinWarning = ref(false)

  async function checkStatus() {
    try {
      const { data } = await axios.get('/api/auth/status')
      pinEnabled.value = data.pin_enabled
      isAuthenticated.value = data.authenticated
      pinWarning.value = !!data.pin_warning
    } catch {
      // Bei Netzwerkfehler: PIN-Status beibehalten (nicht deaktivieren)
      // isAuthenticated bleibt false -> Login-Redirect wird ausgeloest
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
    // NEW-005: kompletter State-Reset nach Logout. Vorher blieb `loading`
    // potenziell auf true haengen (wenn jemand mitten im Login klickt) und
    // `pinEnabled` wurde stale getragen — kein Security-Bug heute, aber sauber.
    reset()
  }

  function reset() {
    isAuthenticated.value = false
    pinEnabled.value = false
    loading.value = false
    pinWarning.value = false
  }

  return {
    pinEnabled,
    isAuthenticated,
    loading,
    pinWarning,
    checkStatus,
    login,
    logout,
    reset,
  }
})
