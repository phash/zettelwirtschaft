// H-FE-5: Zentraler Cache fuer Ablagebereiche.
// Vorher: 7 Views haben getFilingScopes() bei jedem Mount frisch geladen —
// jeder Tab-Wechsel triggerte einen Request. Jetzt: ein Modul-Level-State
// + inflight-Promise, alle Subscriber bekommen das gleiche Ergebnis.
//
// Re-Review #1: Pro Request ein Token; nur der juengste Request darf den Cache
// schreiben — ein zuvor gestarteter, langsamer Request ueberschreibt sonst
// frischere Daten mit Stale-Daten.
//
// Die bisherige Liste bleibt waehrend eines Neuladens stehen (kein Leeren aller
// Dropdowns), Ladefehler landen in `error` statt still als leere Liste.
import { ref } from 'vue'
import { getFilingScopes } from '../services/api'

const scopes = ref([])
const loaded = ref(false)
const loading = ref(false)
const error = ref(null)
let inflight = null
let inflightToken = 0

function fetchScopes() {
  const token = ++inflightToken
  loading.value = true
  // Ueberholter Request: auf den juengeren warten (falls er laeuft), damit auch
  // Aufrufer des alten Promises die frischen Daten bekommen.
  const superseded = () => inflight || scopes.value
  const promise = getFilingScopes()
    .then((data) => {
      if (token !== inflightToken) return superseded()
      scopes.value = Array.isArray(data) ? data : []
      loaded.value = true
      error.value = null
      return scopes.value
    })
    .catch((e) => {
      if (token !== inflightToken) return superseded()
      // Nicht als "loaded" markieren -> naechster ensureLoaded() versucht es erneut.
      error.value = e
      throw e
    })
    .finally(() => {
      if (token === inflightToken) {
        loading.value = false
        inflight = null
      }
    })
  inflight = promise
  return promise
}

export function useFilingScopes() {
  // Fuer Dropdowns/Filter: wirft nie. Bei einem Fehler kommt die bisherige
  // Liste zurueck und `error` ist gesetzt.
  async function ensureLoaded(forceReload = false) {
    if (loaded.value && !forceReload) return scopes.value
    try {
      // Auch beim Anhaengen an einen laufenden Request abfangen — sonst wirft
      // der zweite gleichzeitige Aufrufer doch, wenn dieser Request scheitert.
      return await (inflight && !forceReload ? inflight : fetchScopes())
    } catch {
      return scopes.value
    }
  }

  // Nach Create/Update/Delete eines Scopes: frisch laden. Die alte Liste bleibt
  // bis zum Eintreffen der neuen sichtbar; ein Fehler wird geworfen, damit der
  // Aufrufer ihn melden kann.
  function reload() {
    return fetchScopes()
  }

  // Cache als veraltet markieren — der naechste ensureLoaded() laedt neu.
  // Laufende Requests duerfen danach nicht mehr schreiben (Token-Bump).
  // Die aktuelle Liste bleibt fuer bereits montierte Views stehen.
  function invalidate() {
    loaded.value = false
    inflightToken++
    inflight = null
    loading.value = false
  }

  return { scopes, loaded, loading, error, ensureLoaded, reload, invalidate }
}
