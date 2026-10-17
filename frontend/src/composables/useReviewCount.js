// Anzahl offener Prüfungen fuer das Badge an "Zu prüfen" (Sidebar + Mehr-Menue).
// Modul-Level-State: alle Navigationen teilen einen Wert und einen Request.
// Kein Intervall-Polling — aktualisiert wird bei Navigation (gedrosselt) und
// beim Oeffnen des Mehr-Menues (force).
import { ref } from 'vue'
import { getReviewStats } from '../services/api'

const MIN_REFRESH_INTERVAL_MS = 15000

const count = ref(0)
let lastFetch = 0
let inflight = null

export function useReviewCount() {
  async function refresh(force = false) {
    if (inflight) return inflight
    if (!force && Date.now() - lastFetch < MIN_REFRESH_INTERVAL_MS) return count.value
    inflight = getReviewStats()
      .then((stats) => {
        count.value = Number(stats?.open_documents) || 0
        return count.value
      })
      .catch(() => count.value) // Badge ist Komfort — bei Fehler alten Wert behalten
      .finally(() => {
        lastFetch = Date.now()
        inflight = null
      })
    return inflight
  }

  return { count, refresh }
}
