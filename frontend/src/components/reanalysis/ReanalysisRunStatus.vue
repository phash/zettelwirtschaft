<script setup>
// Status des Massenlaufs "Neu analysieren" über der Dokumentliste.
// Pollt GET /reanalysis/runs/current alle 5 s, solange der Lauf aktiv ist
// (RUNNING/PAUSED/WAITING_AI); beendete Läufe bleiben 24 h sichtbar.
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import ConfirmDialog from '../common/ConfirmDialog.vue'
import { getCurrentReanalysisRun, setReanalysisRunStatus } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { apiErrorMessage } from '../../utils/apiErrors'

const POLL_INTERVAL_MS = 5000
const VISIBLE_AFTER_FINISH_MS = 24 * 60 * 60 * 1000
const ACTIVE = ['RUNNING', 'PAUSED', 'WAITING_AI']

const notify = useNotificationStore()
const run = ref(null)
const busy = ref(false)
const confirmCancel = ref(false)
let timer = null
let refreshToken = 0

const isActive = computed(() => !!run.value && ACTIVE.includes(run.value.status))
const visible = computed(() => {
  const r = run.value
  if (!r) return false
  if (ACTIVE.includes(r.status)) return true
  const finished = r.finished_at ? Date.parse(r.finished_at) : NaN
  return Number.isNaN(finished) || Date.now() - finished <= VISIBLE_AFTER_FINISH_MS
})
const percent = computed(() => {
  const r = run.value
  return r && r.total > 0 ? Math.min(100, Math.round((r.done / r.total) * 100)) : 0
})

async function refresh() {
  const token = ++refreshToken
  try {
    const data = await getCurrentReanalysisRun()
    if (token === refreshToken) run.value = data
  } catch (e) {
    // 404 = noch nie ein Lauf; jeder andere Fehler blendet die Anzeige ebenfalls aus
    // (kein Toast beim Polling), der nächste Tick versucht es erneut.
    if (token === refreshToken && e?.response?.status === 404) run.value = null
  }
}

/** Übernimmt einen Lauf, den der Aufrufer gerade gestartet hat (oder der bereits läuft). */
function setRun(next) {
  refreshToken++  // eine ältere, noch laufende Abfrage darf ihn nicht überschreiben
  run.value = next
}

function stopTimer() {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

watch(isActive, (active) => {
  if (active && timer === null) timer = setInterval(refresh, POLL_INTERVAL_MS)
  else if (!active) stopTimer()
}, { immediate: true })

onMounted(refresh)
onUnmounted(stopTimer)

async function act(action) {
  if (!run.value || busy.value) return
  busy.value = true
  try {
    setRun(await setReanalysisRunStatus(run.value.id, action))
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Der Lauf konnte nicht geändert werden.'))
    await refresh()
  } finally {
    busy.value = false
  }
}

async function onCancelConfirmed() {
  confirmCancel.value = false
  await act('cancel')
}

defineExpose({ setRun, refresh })
</script>

<template>
  <section
    v-if="visible"
    class="card !p-4 space-y-3"
    data-testid="reanalysis-run-status"
    aria-live="polite"
  >
    <template v-if="isActive">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="min-w-0">
          <p class="text-sm font-medium text-gray-900">
            Neu-Analyse: {{ run.done }} von {{ run.total }}
            <span v-if="run.status === 'PAUSED'" class="ml-2 badge bg-yellow-100 text-yellow-800">pausiert</span>
            <span v-else-if="run.status === 'WAITING_AI'" class="ml-2 badge bg-yellow-100 text-yellow-800">wartet auf KI</span>
          </p>
          <p v-if="run.status === 'WAITING_AI' && run.last_error" class="mt-1 text-xs text-gray-500">{{ run.last_error }}</p>
        </div>
        <div class="flex gap-2">
          <button
            v-if="run.status === 'PAUSED'"
            type="button"
            class="btn-secondary !py-1.5 text-xs"
            :disabled="busy"
            @click="act('resume')"
          >Fortsetzen</button>
          <button
            v-else
            type="button"
            class="btn-secondary !py-1.5 text-xs"
            :disabled="busy"
            @click="act('pause')"
          >Pausieren</button>
          <button
            type="button"
            class="btn-secondary !py-1.5 text-xs"
            :disabled="busy"
            @click="confirmCancel = true"
          >Abbrechen</button>
        </div>
      </div>
      <div
        class="h-2 w-full overflow-hidden rounded-full bg-gray-100"
        role="progressbar"
        aria-label="Fortschritt der Neu-Analyse"
        aria-valuemin="0"
        :aria-valuemax="run.total"
        :aria-valuenow="run.done"
      >
        <div class="h-full bg-primary-600 transition-all" :style="{ width: `${percent}%` }"></div>
      </div>
    </template>

    <template v-else>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-sm text-gray-900">
          <span class="font-medium">{{ run.status === 'CANCELLED' ? 'Neu-Analyse abgebrochen' : 'Neu-Analyse abgeschlossen' }}:</span>
          {{ run.done }} von {{ run.total }} bearbeitet, {{ run.proposed }} mit Vorschlag, {{ run.unchanged }} unverändert<template v-if="run.failed > 0">, {{ run.failed }} fehlgeschlagen</template>
        </p>
        <router-link v-if="run.proposed > 0" to="/pruefen?tab=vorschlaege" class="btn-primary !py-1.5 text-xs">Vorschläge prüfen</router-link>
      </div>
      <details v-if="run.failures && run.failures.length" class="text-sm text-gray-700">
        <summary class="cursor-pointer">Fehler anzeigen</summary>
        <ul class="mt-2 space-y-1 text-xs">
          <li v-for="f in run.failures" :key="f.document_id">
            <span class="font-medium">{{ f.title || f.document_id }}</span><span v-if="f.error"> — {{ f.error }}</span>
          </li>
        </ul>
      </details>
    </template>

    <ConfirmDialog
      :show="confirmCancel"
      title="Neu-Analyse abbrechen?"
      message="Bereits erzeugte Vorschläge bleiben erhalten. Die übrigen Dokumente werden nicht mehr analysiert."
      confirm-text="Lauf abbrechen"
      cancel-text="Weiterlaufen lassen"
      danger
      @confirm="onCancelConfirmed"
      @cancel="confirmCancel = false"
    />
  </section>
</template>
