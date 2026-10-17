import { defineStore } from 'pinia'
import { ref } from 'vue'
// Raw axios statt api.js um Zirkulaere Abhaengigkeit zu vermeiden:
// api.js 401-Interceptor importiert auth store → auth store darf nicht api.js importieren
import axios from 'axios'
import { clearApiCache } from '../utils/apiCache'

export const useAuthStore = defineStore('auth', () => {
  const pinEnabled = ref(false)
  const isAuthenticated = ref(false)
  const loading = ref(false)
  // B1: Backend setzt pin_warning=true wenn das System ohne PIN-Schutz laeuft.
  // Frontend zeigt einen prominenten Banner damit Heim-User die Lage erkennen.
  const pinWarning = ref(false)

  // Liefert true, wenn der Status vom Backend kam. Bei einem Netzwerkfehler
  // bleibt der bisherige Zustand stehen und es wird false geliefert — der
  // Router-Guard fragt dann bei der naechsten Navigation erneut (statt den
  // PIN-Status fuer die ganze Sitzung als "aus" anzunehmen).
  async function checkStatus() {
    try {
      const { data } = await axios.get('/api/auth/status')
      pinEnabled.value = data.pin_enabled
      isAuthenticated.value = data.authenticated
      pinWarning.value = !!data.pin_warning
      return true
    } catch {
      return false
    }
  }

  // Backend: 200 {success: true} bei Erfolg, 401 bei falschem PIN, 429 bei Sperre.
  async function login(pin) {
    loading.value = true
    try {
      await axios.post('/api/auth/login', { pin })
      // Erfolgreicher Login beweist aktiven PIN-Schutz — auch wenn die
      // Statusabfrage vorher scheiterte (sonst fehlte z.B. "Abmelden").
      pinEnabled.value = true
      isAuthenticated.value = true
      return { success: true }
    } catch (error) {
      return { success: false, detail: error.response?.data?.detail || 'Fehler bei der Anmeldung' }
    } finally {
      loading.value = false
    }
  }

  // Eine 401 vom Backend beweist: der PIN-Schutz ist aktiv und die Sitzung
  // ist abgelaufen/ungueltig. Darum pinEnabled=true setzen und NICHT
  // zuruecksetzen — sonst liesse der Router-Guard danach jede Route ohne
  // PIN durch (und die Willkommens-Tour startete vor dem Login).
  function markSessionExpired() {
    isAuthenticated.value = false
    pinEnabled.value = true
    loading.value = false
  }

  // Abmelden (nur sinnvoll mit aktivem PIN-Schutz): Server-Session beenden,
  // vom Service-Worker zwischengespeicherte API-Antworten loeschen und lokal
  // als abgemeldet markieren. Die Navigation zu /pin macht der Aufrufer.
  async function logout() {
    try {
      await axios.post('/api/auth/logout')
    } catch {
      // Session ist serverseitig ggf. schon weg — lokal trotzdem abmelden.
    }
    await clearApiCache()
    markSessionExpired()
  }

  return {
    pinEnabled,
    isAuthenticated,
    loading,
    pinWarning,
    checkStatus,
    login,
    logout,
    markSessionExpired,
  }
})
