import { defineStore } from 'pinia'
import { ref } from 'vue'

// Pro-Browser gemerkt (kein Backend noetig). Versioniert, damit eine
// ueberarbeitete Tour erneut gezeigt werden kann (Key hochzaehlen).
const STORAGE_KEY = 'zw_onboarding_v1_done'

export const useOnboardingStore = defineStore('onboarding', () => {
  const open = ref(false)
  const completed = ref(false)

  try {
    completed.value = localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    // localStorage nicht verfuegbar (z.B. privater Modus) -> Tour zeigen, nicht persistieren
  }

  function _persist() {
    try {
      localStorage.setItem(STORAGE_KEY, '1')
    } catch {
      // ignore
    }
  }

  // Beim ersten App-Start automatisch starten (von AppLayout aufgerufen).
  function maybeAutoStart() {
    if (!completed.value && !open.value) open.value = true
  }

  function start() {
    open.value = true
  }

  // Beenden ODER ueberspringen -> als gesehen markieren.
  function finish() {
    open.value = false
    completed.value = true
    _persist()
  }

  // Aus der Hilfe heraus erneut ansehen.
  function restart() {
    completed.value = false
    open.value = true
  }

  return { open, completed, maybeAutoStart, start, finish, restart }
})
