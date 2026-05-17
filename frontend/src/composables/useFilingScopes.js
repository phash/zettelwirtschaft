// H-FE-5: Zentraler Cache fuer Ablagebereiche.
// Vorher: 7 Views haben getFilingScopes() bei jedem Mount frisch geladen —
// jeder Tab-Wechsel triggerte einen Request. Jetzt: ein Modul-Level-State
// + inflight-Promise, alle Subscriber bekommen das gleiche Ergebnis.
import { shallowRef, ref } from 'vue'
import { getFilingScopes } from '../services/api'

const scopes = shallowRef([])
const loaded = ref(false)
const loading = ref(false)
let inflight = null

export function useFilingScopes() {
  async function ensureLoaded(forceReload = false) {
    if (loaded.value && !forceReload) return scopes.value
    if (inflight) return inflight
    loading.value = true
    inflight = getFilingScopes()
      .then((data) => {
        scopes.value = Array.isArray(data) ? data : []
        loaded.value = true
        return scopes.value
      })
      .catch(() => {
        // Bei Fehler nicht als "loaded" markieren, naechster Aufruf retried.
        return []
      })
      .finally(() => {
        loading.value = false
        inflight = null
      })
    return inflight
  }

  // Nach Create/Update/Delete eines Scopes aufrufen — naechster ensureLoaded
  // holt frisch.
  function invalidate() {
    loaded.value = false
    scopes.value = []
  }

  return { scopes, loaded, loading, ensureLoaded, invalidate }
}
