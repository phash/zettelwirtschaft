// Gemeinsames Polling fuer Hintergrund-Vorgaenge (Re-Analyse, E-Mail-Abruf,
// Voll-Backup). Backend-Vertrag: der Start-Endpoint antwortet mit HTTP 202 +
// Task-Objekt ({ id, kind, key, status, progress_percent, progress_text,
// result, error, done, ... }); laeuft fuer dasselbe Objekt schon ein Vorgang,
// kommt HTTP 409 mit { detail, task } — dann haengen wir uns an diesen an.
// Status per GET /api/tasks/{id}, bis done === true.
//
// Beispiel:
//   const reanalysis = useBackgroundTask({
//     onSuccess: (result) => { toast.success('Neu analysiert'); reload() },
//     onError: (message) => toast.error(message),
//   })
//   await reanalysis.start(() => reanalyzeDocument(id))
//   // Template: reanalysis.running, reanalysis.progressText, reanalysis.progress
//   // Beim Mount einen noch laufenden Vorgang wieder aufnehmen:
//   onMounted(() => reanalysis.resume({ kind: 'reanalyze', key: id }))
//
// Das Polling stoppt automatisch, wenn die Komponente (bzw. der Effect-Scope)
// zerstoert wird.
import { computed, getCurrentScope, onScopeDispose, ref } from 'vue'
import { getTask, listTasks } from '../services/api'

const MAX_POLL_FAILURES = 8

function errorDetail(err, fallback) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  return fallback
}

export function useBackgroundTask(options = {}) {
  const {
    initialInterval = 1000,
    maxInterval = 5000,
    backoffFactor = 1.5,
    onSuccess = null,
    onError = null,
  } = options

  const task = ref(null)
  // Fehler beim Starten/Abfragen (Netzwerk, 404) — Fehler des Vorgangs selbst
  // stehen in task.error; `error` liefert beides zusammengefasst.
  const requestError = ref(null)
  const starting = ref(false)
  // true solange dieser Composable einen Vorgang verfolgt (Start-Request oder
  // Polling). Nach stop() laeuft der Vorgang im Backend ggf. weiter.
  const polling = ref(false)

  let timer = null
  let runToken = 0
  let resolveRun = null

  const status = computed(() => task.value?.status ?? null)
  const running = computed(() => starting.value || polling.value)
  const done = computed(() => task.value?.done === true)
  const succeeded = computed(() => status.value === 'SUCCEEDED')
  const failed = computed(() => status.value === 'FAILED' || (!running.value && !!requestError.value))
  const progress = computed(() => task.value?.progress_percent ?? null)
  const progressText = computed(() => task.value?.progress_text ?? null)
  const result = computed(() => (succeeded.value ? task.value.result : null))
  const error = computed(() => requestError.value || task.value?.error || null)

  function clearTimer() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function settle(value) {
    const resolve = resolveRun
    resolveRun = null
    if (resolve) resolve(value)
  }

  function finish(token) {
    if (token !== runToken) return
    clearTimer()
    polling.value = false
    const final = task.value
    if (final?.status === 'SUCCEEDED') {
      onSuccess?.(final.result, final)
    } else {
      onError?.(error.value || 'Vorgang fehlgeschlagen', final)
    }
    settle(final)
  }

  function schedule(token, delay, failures) {
    clearTimer()
    timer = setTimeout(() => poll(token, delay, failures), delay)
  }

  async function poll(token, delay, failures) {
    if (token !== runToken || !task.value) return
    try {
      const fresh = await getTask(task.value.id)
      if (token !== runToken) return
      task.value = fresh
      requestError.value = null
      if (fresh.done) {
        finish(token)
        return
      }
      schedule(token, Math.min(Math.round(delay * backoffFactor), maxInterval), 0)
    } catch (err) {
      if (token !== runToken) return
      if (err?.response?.status === 404) {
        requestError.value = 'Vorgang nicht gefunden'
        finish(token)
        return
      }
      // Netzwerk-/Serverfehler: Backend evtl. kurz weg (Neustart) — mit
      // Backoff weiter versuchen, erst nach mehreren Fehlschlaegen aufgeben.
      if (failures + 1 >= MAX_POLL_FAILURES) {
        requestError.value = 'Status des Vorgangs konnte nicht abgerufen werden.'
        finish(token)
        return
      }
      schedule(token, maxInterval, failures + 1)
    }
  }

  // Laufenden Vorgang (Task-Objekt oder ID) verfolgen. Promise wird mit dem
  // finalen Task-Objekt aufgeloest (oder null bei stop()).
  function attach(taskOrId) {
    stop()
    const token = ++runToken
    requestError.value = null
    task.value = typeof taskOrId === 'string'
      ? { id: taskOrId, status: 'PENDING', done: false }
      : taskOrId
    const promise = new Promise((resolve) => { resolveRun = resolve })
    polling.value = true
    if (task.value.done) {
      finish(token)
    } else {
      schedule(token, initialInterval, 0)
    }
    return promise
  }

  // starter: () => Promise<Task> — der API-Call, der den Vorgang startet
  // (antwortet mit 202 + Task). Bei 409 wird der laufende Vorgang uebernommen.
  async function start(starter) {
    stop()
    const token = ++runToken
    starting.value = true
    requestError.value = null
    task.value = null
    try {
      const started = await starter()
      if (token !== runToken) return null
      starting.value = false
      return attach(started)
    } catch (err) {
      if (token !== runToken) return null
      starting.value = false
      const active = err?.response?.status === 409 ? err.response.data?.task : null
      if (active) return attach(active)
      requestError.value = errorDetail(err, 'Vorgang konnte nicht gestartet werden.')
      onError?.(requestError.value, null)
      return null
    }
  }

  // Beim Mount: laeuft fuer (kind, key) noch ein Vorgang? Dann weiter pollen.
  // Gibt das Promise von attach() zurueck oder null, wenn nichts laeuft.
  async function resume({ kind, key = undefined }) {
    const token = runToken
    try {
      const active = await listTasks({ kind, key, active: true, limit: 1 })
      if (token !== runToken || !active?.length) return null
      return attach(active[0])
    } catch {
      return null
    }
  }

  // Polling beenden (Vorgang laeuft im Backend weiter).
  function stop() {
    runToken++
    clearTimer()
    starting.value = false
    polling.value = false
    settle(null)
  }

  function reset() {
    stop()
    task.value = null
    requestError.value = null
  }

  if (getCurrentScope()) onScopeDispose(stop)

  return {
    task,
    status,
    running,
    done,
    succeeded,
    failed,
    progress,
    progressText,
    result,
    error,
    start,
    attach,
    resume,
    stop,
    reset,
  }
}
