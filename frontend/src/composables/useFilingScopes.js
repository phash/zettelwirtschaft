// H-FE-5: Zentraler Cache fuer Ablagebereiche.
// Vorher: 7 Views haben getFilingScopes() bei jedem Mount frisch geladen —
// jeder Tab-Wechsel triggerte einen Request. Jetzt: ein Modul-Level-State
// + inflight-Promise, alle Subscriber bekommen das gleiche Ergebnis.
//
// Re-Review #1: invalidate() löst auch das laufende inflight-Promise — sonst
// schreibt ein zuvor gestarteter, langsamer Request den frisch gelöschten
// Cache mit Stale-Daten wieder voll. Pro inflight-Run ein Token, der bei
// resolve geprüft wird; nur passende Tokens dürfen schreiben.
import { ref } from 'vue'
import { getFilingScopes } from '../services/api'

const scopes = ref([])
const loaded = ref(false)
const loading = ref(false)
let inflight = null
let inflightToken = 0

export function useFilingScopes() {
  async function ensureLoaded(forceReload = false) {
    if (loaded.value && !forceReload) return scopes.value
    if (inflight) return inflight
    const token = ++inflightToken
    loading.value = true
    inflight = getFilingScopes()
      .then((data) => {
        // Wenn der Token zwischenzeitlich überholt wurde (invalidate während
        // dieses Promise lief), ignorieren — der frischere Aufruf gewinnt.
        if (token !== inflightToken) return scopes.value
        scopes.value = Array.isArray(data) ? data : []
        loaded.value = true
        return scopes.value
      })
      .catch(() => {
        // Bei Fehler nicht als "loaded" markieren, naechster Aufruf retried.
        return []
      })
      .finally(() => {
        // loading nur zuruecksetzen wenn unsere Promise die juengste war.
        if (token === inflightToken) {
          loading.value = false
          inflight = null
        }
      })
    return inflight
  }

  // Nach Create/Update/Delete eines Scopes aufrufen — naechster ensureLoaded
  // holt frisch. Bricht parallel laufende Stale-Reads ab (Token-Bump).
  function invalidate() {
    loaded.value = false
    scopes.value = []
    inflightToken++
    inflight = null
    loading.value = false
  }

  return { scopes, loaded, loading, ensureLoaded, invalidate }
}
