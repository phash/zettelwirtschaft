<script setup>
// Aufteilungs-Editor (Spezifikation 12): Seiten eines Stapel-PDFs als Raster,
// zwischen zwei Seiten ein Uebergangs-Marker (Schnitt / unsicher / kein Schnitt).
// Klick oder Leertaste schaltet, Pfeiltasten wandern zwischen den Markern.
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import {
  getSplitBatch,
  confirmSplit,
  keepSplitWhole,
  discardSplit,
  retryFailedSplitParts,
  splitPageThumbnailUrl,
  splitPagePreviewUrl,
} from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { apiErrorMessage } from '../utils/apiErrors'
import { computeParts, confirmBody, partLabel } from '../utils/splitParts'
import { pluralize } from '../utils/pluralize'
import { trapTabKey } from '../utils/focusTrap'
import { UNDO_TITLE, undoMessage, undoSplitBatch } from '../utils/splitUndo'

const props = defineProps({ batchId: { type: String, required: true } })

const router = useRouter()
const notify = useNotificationStore()

const batch = ref(null)
const loading = ref(true)
const loadError = ref(false)
const busy = ref(false)
const conflict = ref(false)
const cuts = ref(new Set())
const excluded = ref(new Set())
const focusedMarker = ref(1)
const previewPage = ref(null)
const pendingDuplexCut = ref(null)
const showDiscardDialog = ref(false)
const showUndoDialog = ref(false)
const markerRefs = {}
const previewPanel = ref(null)

const POLL_MS = 2000
let pollTimer = null
let unmounted = false

const SOURCE_LABELS = {
  UPLOAD: 'Upload',
  WATCH_FOLDER: 'Watch-Ordner',
  EMAIL: 'E-Mail',
  SPLIT_REQUEST: 'Dokument',
}

const PART_COLORS = [
  'bg-blue-50 ring-blue-200',
  'bg-emerald-50 ring-emerald-200',
  'bg-amber-50 ring-amber-200',
  'bg-violet-50 ring-violet-200',
  'bg-rose-50 ring-rose-200',
  'bg-cyan-50 ring-cyan-200',
]
const PART_TEXT_COLORS = [
  'text-blue-700', 'text-emerald-700', 'text-amber-700', 'text-violet-700', 'text-rose-700', 'text-cyan-700',
]

const JOB_STATUS_LABELS = {
  PENDING: 'Wartet',
  PROCESSING: 'Wird analysiert',
  COMPLETED: 'Archiviert',
  NEEDS_REVIEW: 'Prüfung nötig',
  FAILED: 'Fehlgeschlagen',
}

const pages = computed(() => batch.value?.pages || [])
const pageCount = computed(() => batch.value?.page_count || 0)
const editable = computed(() => batch.value?.status === 'PROPOSED' && !conflict.value)
const boundaryByPage = computed(() => {
  const map = {}
  for (const b of batch.value?.boundaries || []) map[b.after_page] = b
  return map
})
const blankPages = computed(() => new Set(pages.value.filter((p) => p.blank).map((p) => p.n)))
const split = computed(() => computeParts(pageCount.value, cuts.value, excluded.value, blankPages.value))
const partCountLabel = computed(() => pluralize(split.value.parts.length, 'Dokument', 'Dokumente'))
const sheets = computed(() => Math.ceil(pageCount.value / 2))
const sourceMissing = computed(() => batch.value?.error_code === 'SPLIT_SOURCE_MISSING')
const canDiscard = computed(() => batch.value?.origin === 'AUTO' || sourceMissing.value)
// MANUAL ("Dokument aufteilen"): Verwerfen entfernt nur den Stapel, das
// archivierte Original bleibt unveraendert (split_service.discard).
const discardMessage = computed(() =>
  batch.value?.origin === 'MANUAL'
    ? 'Der Stapel wird verworfen. Das Originaldokument bleibt unverändert.'
    : 'Die Datei wird gelöscht und nicht archiviert. Wirklich verwerfen?',
)
// Unsichere Stellen, an denen (noch) nicht geschnitten wird. Die Begruendungen
// stehen zusaetzlich als Liste unter dem Hinweis: der Tooltip am Marker ist auf
// Touch-Geraeten nicht erreichbar.
const uncertainBoundaries = computed(() =>
  (batch.value?.boundaries || []).filter((b) => b.decision === 'uncertain' && !cuts.value.has(b.after_page)),
)
const uncertainCount = computed(() => uncertainBoundaries.value.length)
const detectingPage = computed(() => Math.min((batch.value?.pages_done || 0) + 1, pageCount.value))
const detectingPercent = computed(() =>
  pageCount.value ? Math.round(((batch.value?.pages_done || 0) / pageCount.value) * 100) : 0,
)

function markerState(n) {
  if (cuts.value.has(n)) return 'cut'
  if (boundaryByPage.value[n]?.decision === 'uncertain') return 'uncertain'
  return 'none'
}

function markerTitle(n) {
  const reasons = boundaryByPage.value[n]?.reasons || []
  const state = markerState(n)
  const head = state === 'cut' ? `Schnitt nach Seite ${n}` : state === 'uncertain' ? `Unsicher: Schnitt nach Seite ${n}?` : `Kein Schnitt nach Seite ${n}`
  return reasons.length ? `${head} – ${reasons.join('; ')}` : head
}

function partIndexOf(n) {
  return split.value.segmentOf[n] ?? null
}

function partColor(n) {
  const index = partIndexOf(n)
  return index ? PART_COLORS[(index - 1) % PART_COLORS.length] : 'bg-gray-50 ring-gray-200'
}

function partTextColor(index) {
  return PART_TEXT_COLORS[(index - 1) % PART_TEXT_COLORS.length]
}

// Erste Seite eines Teils traegt die Beschriftung "Teil 1 · Seiten 1–2".
function partStartingAt(n) {
  return split.value.parts.find((part) => part.range[0] === n) || null
}

function pageLabel(page) {
  if (!page.blank) return null
  if (page.blank_kind === 'back') return 'Leere Rückseite'
  if (page.blank_kind === 'separator') return 'Trennblatt?'
  return 'Leerseite'
}

function isUnrecognized(n) {
  return batch.value?.partial_from && n >= batch.value.partial_from
}

function initFromBatch(data) {
  cuts.value = new Set((data.boundaries || []).filter((b) => b.decision === 'cut').map((b) => b.after_page))
  excluded.value = new Set(data.excluded || [])
  focusedMarker.value = 1
}

function clearPoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll() {
  clearPoll()
  if (unmounted) return
  if (['DETECTING', 'SPLITTING'].includes(batch.value?.status)) {
    pollTimer = setTimeout(() => load({ keepEdits: true }), POLL_MS)
  }
}

async function load({ keepEdits = false } = {}) {
  clearPoll()
  try {
    const data = await getSplitBatch(props.batchId)
    const wasEditable = batch.value?.status === 'PROPOSED'
    batch.value = data
    loadError.value = false
    // Beim Polling eigene, noch nicht gesendete Schnitte nicht ueberschreiben.
    if (!keepEdits || !wasEditable) initFromBatch(data)
  } catch (e) {
    if (e.response?.status === 404) {
      notify.error('Stapel nicht gefunden – er wurde möglicherweise verworfen.')
      router.replace('/')
      return
    }
    loadError.value = true
  } finally {
    loading.value = false
  }
  schedulePoll()
}

async function reload() {
  conflict.value = false
  loading.value = true
  await load()
}

function setCut(n, on) {
  const next = new Set(cuts.value)
  if (on) next.add(n)
  else next.delete(n)
  cuts.value = next
}

function toggleMarker(n) {
  if (!editable.value) return
  focusedMarker.value = n
  if (cuts.value.has(n)) {
    setCut(n, false)
    return
  }
  // Duplex: ein Schnitt nach einer ungeraden Seite trennt Vorder- und Rueckseite.
  if (batch.value?.duplex && n % 2 === 1) {
    pendingDuplexCut.value = n
    return
  }
  setCut(n, true)
}

function confirmDuplexCut() {
  const n = pendingDuplexCut.value
  pendingDuplexCut.value = null
  if (n) setCut(n, true)
}

function focusMarker(n) {
  focusedMarker.value = n
  nextTick(() => markerRefs[n]?.focus())
}

function onMarkerKeydown(e, n) {
  const last = pageCount.value - 1
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
    e.preventDefault()
    if (n < last) focusMarker(n + 1)
  } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
    e.preventDefault()
    if (n > 1) focusMarker(n - 1)
  } else if (e.key === 'Home') {
    e.preventDefault()
    focusMarker(1)
  } else if (e.key === 'End') {
    e.preventDefault()
    focusMarker(last)
  }
}

function toggleExcluded(n) {
  if (!editable.value) return
  const next = new Set(excluded.value)
  if (next.has(n)) next.delete(n)
  else next.add(n)
  excluded.value = next
}

function handleConflict(e) {
  if (e.response?.status === 409 && e.response?.data?.code !== 'SPLIT_SOURCE_MISSING') {
    conflict.value = true
    return true
  }
  return false
}

async function handleConfirm() {
  if (!editable.value || split.value.error || busy.value) return
  busy.value = true
  try {
    const count = split.value.parts.length
    await confirmSplit(batch.value.id, confirmBody(batch.value.version, split.value.parts, excluded.value))
    notify.success(`Wird in ${pluralize(count, 'Dokument', 'Dokumente')} aufgeteilt.`)
    await load()
  } catch (e) {
    if (!handleConflict(e)) {
      notify.error(apiErrorMessage(e, 'Aufteilen fehlgeschlagen.'))
      if (e.response?.data?.code === 'SPLIT_SOURCE_MISSING') await load({ keepEdits: true })
    }
  } finally {
    busy.value = false
  }
}

async function handleKeepWhole() {
  if (!editable.value || busy.value) return
  busy.value = true
  try {
    await keepSplitWhole(batch.value.id, batch.value.version)
    if (batch.value.origin === 'MANUAL' && batch.value.source_document_id) {
      notify.success('Das Dokument bleibt unverändert.')
      router.push(`/dokumente/${batch.value.source_document_id}`)
    } else {
      notify.success('Wird als ein Dokument verarbeitet.')
      router.push('/')
    }
  } catch (e) {
    if (!handleConflict(e)) notify.error(apiErrorMessage(e, 'Aktion fehlgeschlagen.'))
  } finally {
    busy.value = false
  }
}

async function handleDiscard() {
  showDiscardDialog.value = false
  if (busy.value) return
  busy.value = true
  try {
    await discardSplit(batch.value.id, batch.value.version)
    notify.success('Stapel verworfen.')
    router.push('/')
  } catch (e) {
    if (!handleConflict(e)) notify.error(apiErrorMessage(e, 'Verwerfen fehlgeschlagen.'))
  } finally {
    busy.value = false
  }
}

async function handleRetryFailed() {
  busy.value = true
  try {
    await retryFailedSplitParts(batch.value.id)
    notify.success('Fehlgeschlagene Teile werden erneut verarbeitet.')
    await load()
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Wiederholung fehlgeschlagen.'))
  } finally {
    busy.value = false
  }
}

// "Aufteilung rückgängig" (DONE / DONE_WITH_ERRORS): AUTO -> Stapel wieder
// als Vorschlag im Editor, MANUAL -> Original wieder aktiv.
async function handleUndo() {
  showUndoDialog.value = false
  if (busy.value || !batch.value?.can_undo) return
  busy.value = true
  try {
    const target = await undoSplitBatch(batch.value.id, batch.value)
    notify.success(target.message)
    if (target.path === `/aufteilen/${batch.value.id}`) await load()
    else router.push(target.path)
  } catch (e) {
    // 409 nennt den Grund (Teil bearbeitet, Quelle nicht mehr aufbewahrt) —
    // anzeigen und den aktuellen Stand laden.
    notify.error(apiErrorMessage(e, 'Die Aufteilung kann nicht rückgängig gemacht werden.'))
    await load()
  } finally {
    busy.value = false
  }
}

// --- Grossansicht ---
let previewFocusReturn = null

function openPreview(n) {
  previewFocusReturn = document.activeElement
  previewPage.value = n
  nextTick(() => previewPanel.value?.focus())
}

function closePreview() {
  previewPage.value = null
  previewFocusReturn?.focus?.()
  previewFocusReturn = null
}

function onPreviewKeydown(e) {
  if (e.key === 'Escape') {
    e.preventDefault()
    closePreview()
  } else if (e.key === 'ArrowRight' && previewPage.value < pageCount.value) {
    previewPage.value += 1
  } else if (e.key === 'ArrowLeft' && previewPage.value > 1) {
    previewPage.value -= 1
  } else {
    trapTabKey(e, previewPanel.value)
  }
}

watch(() => props.batchId, () => reload())

onMounted(load)
onUnmounted(() => {
  unmounted = true
  clearPoll()
})
</script>

<template>
  <div v-if="loading" class="flex items-center justify-center py-20">
    <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
  </div>

  <div v-else-if="loadError && !batch" class="card text-center text-sm text-gray-600">
    <p>Der Stapel konnte nicht geladen werden.</p>
    <button class="btn-secondary mt-3" @click="reload">Erneut versuchen</button>
  </div>

  <div v-else-if="batch" class="space-y-4 pb-40 lg:pb-0" data-testid="split-editor">
    <!-- Kopf -->
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <h1 class="truncate text-xl font-bold text-gray-900">Dokument aufteilen</h1>
        <p class="mt-1 truncate text-sm text-gray-600" data-testid="split-header">
          „{{ batch.original_filename }}“ · {{ SOURCE_LABELS[batch.job_source] || batch.job_source }}
          · {{ pluralize(batch.page_count, 'Seite', 'Seiten') }}
        </p>
        <div class="mt-2 flex flex-wrap gap-2">
          <span v-if="batch.duplex" class="badge bg-sky-100 text-sky-700">Duplex erkannt: {{ sheets }} Blätter</span>
          <span v-if="batch.llm_unavailable" class="badge bg-gray-100 text-gray-600">KI-Prüfung nicht verfügbar</span>
          <span v-else-if="batch.llm_used" class="badge bg-violet-100 text-violet-700" data-testid="split-llm-used">KI-geprüft</span>
        </div>
      </div>
    </div>

    <!-- 409: zwischenzeitlich geaendert -->
    <div v-if="conflict" class="card flex flex-wrap items-center justify-between gap-3 border border-amber-200 bg-amber-50" role="alert">
      <p class="text-sm text-amber-800">Der Stapel wurde inzwischen geändert.</p>
      <button class="btn-secondary" @click="reload">Neu laden</button>
    </div>

    <!-- Stapeldatei fehlt -->
    <div v-if="sourceMissing && batch.status === 'PROPOSED'" class="card border border-red-200 bg-red-50" role="alert" data-testid="split-source-missing">
      <p class="text-sm text-red-700">Die Stapeldatei ist nicht mehr vorhanden – der Stapel kann nur noch verworfen werden.</p>
    </div>

    <!-- Erkennung laeuft -->
    <div v-if="batch.status === 'DETECTING'" class="card" data-testid="split-detecting">
      <div class="flex items-center gap-3">
        <div class="h-6 w-6 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600"></div>
        <p class="text-sm text-gray-700" role="status" aria-live="polite">Seite {{ detectingPage }} von {{ batch.page_count }} wird erkannt …</p>
      </div>
      <div class="mt-3 h-2 w-full rounded-full bg-gray-200" role="progressbar" aria-label="Fortschritt der Seitenerkennung" :aria-valuenow="detectingPercent" aria-valuemin="0" aria-valuemax="100">
        <div class="h-2 rounded-full bg-primary-600 transition-all" :style="{ width: detectingPercent + '%' }"></div>
      </div>
      <p class="mt-2 text-xs text-gray-500">Der Editor wird freigegeben, sobald alle Seiten erkannt sind.</p>
    </div>

    <!-- Abgeschlossen / laufende Teile -->
    <div v-else-if="['SPLITTING', 'DONE', 'DONE_WITH_ERRORS'].includes(batch.status)" class="card space-y-3" data-testid="split-result">
      <h2 class="text-sm font-semibold uppercase tracking-wider text-gray-900">
        {{ batch.status === 'SPLITTING' ? 'Teile werden verarbeitet' : 'Aufgeteilt' }}
      </h2>
      <ul class="divide-y divide-gray-100">
        <li v-for="part in batch.parts" :key="part.index" class="flex flex-wrap items-center justify-between gap-2 py-2">
          <span class="text-sm text-gray-700">{{ partLabel(part) }}</span>
          <span class="flex items-center gap-3">
            <span class="badge bg-gray-100 text-gray-700">{{ JOB_STATUS_LABELS[part.job_status] || 'Wartet' }}</span>
            <router-link v-if="part.document_id" :to="`/dokumente/${part.document_id}`" class="text-sm text-primary-600 hover:text-primary-700">
              Anzeigen
            </router-link>
          </span>
        </li>
      </ul>
      <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button v-if="batch.status === 'DONE_WITH_ERRORS'" class="btn-secondary" :disabled="busy" @click="handleRetryFailed">
          Fehlgeschlagene Teile wiederholen
        </button>
        <button
          v-if="batch.status !== 'SPLITTING'"
          type="button"
          class="btn-secondary"
          :disabled="busy || !batch.can_undo"
          :aria-describedby="batch.undo_blocked_reason ? 'split-editor-undo-reason' : undefined"
          data-testid="split-editor-undo"
          @click="showUndoDialog = true"
        >Aufteilung rückgängig</button>
        <span
          v-if="batch.status !== 'SPLITTING' && batch.undo_blocked_reason"
          id="split-editor-undo-reason"
          class="text-xs text-gray-500"
        >{{ batch.undo_blocked_reason }}</span>
      </div>
    </div>

    <div v-else-if="batch.status !== 'PROPOSED'" class="card text-sm text-gray-600" data-testid="split-closed">
      <template v-if="batch.status === 'KEPT_WHOLE'">Der Stapel wurde als ein Dokument behalten.</template>
      <template v-else-if="batch.status === 'DISCARDED'">Der Stapel wurde verworfen.</template>
      <template v-else-if="batch.status === 'FAILED'">Die Datei kann nicht aufgeteilt werden.</template>
      <template v-else>Die Aufteilung wurde rückgängig gemacht.</template>
    </div>

    <!-- Editor -->
    <template v-if="batch.status === 'PROPOSED'">
      <p class="text-sm text-gray-600">
        Klicke zwischen zwei Seiten, um dort zu schneiden. Seiten lassen sich weglassen;
        nichts wird ohne deine Bestätigung archiviert.
      </p>
      <p id="split-marker-help" class="sr-only">
        Übergang zwischen zwei Seiten. Pfeiltasten wechseln zum vorherigen oder nächsten Übergang,
        Leertaste setzt oder entfernt den Schnitt.
      </p>
      <!-- Ansage nach jeder Aenderung (Screenreader): aktuelle Aufteilung -->
      <p class="sr-only" aria-live="polite" aria-atomic="true" data-testid="split-summary">
        {{ split.error || `Aufteilung in ${partCountLabel}: ${split.parts.map((part) => partLabel(part)).join(', ')}` }}
      </p>
      <p v-if="uncertainCount" class="text-sm text-orange-700" data-testid="split-uncertain-hint">
        {{ uncertainCount === 1 ? 'Eine Stelle ist' : `${uncertainCount} Stellen sind` }} unsicher (orange gestrichelt) –
        bitte prüfen und bei Bedarf dort schneiden.
      </p>
      <ul v-if="uncertainCount" class="list-disc space-y-0.5 pl-5 text-xs text-orange-700" data-testid="split-uncertain-list">
        <li v-for="b in uncertainBoundaries" :key="b.after_page">
          Nach Seite {{ b.after_page }}<template v-if="b.reasons?.length">: {{ b.reasons.join('; ') }}</template>
        </li>
      </ul>
      <div v-if="batch.partial_from" class="card border border-amber-200 bg-amber-50 text-sm text-amber-800" data-testid="split-partial">
        Ab Seite {{ batch.partial_from }} ohne Texterkennung – bitte selbst prüfen.
      </div>

      <ol class="grid grid-cols-2 gap-x-6 gap-y-5 pr-6 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8" data-testid="split-grid" aria-label="Seiten des Stapels">
        <li
          v-for="page in pages"
          :key="page.n"
          class="relative"
          :data-testid="`split-page-${page.n}`"
        >
          <p
            v-if="partStartingAt(page.n)"
            class="mb-1 truncate text-xs font-semibold"
            :class="partTextColor(partStartingAt(page.n).index)"
            data-testid="split-part-label"
          >{{ partLabel(partStartingAt(page.n)) }}</p>
          <p v-else class="mb-1 text-xs">&nbsp;</p>
          <div class="rounded-lg p-1.5 ring-1 transition" :class="partColor(page.n)">
            <!-- Nur das Vorschaubild ausgrauen: "Wieder aufnehmen" muss lesbar
                 bleiben (Kontrast), sonst ist das Weglassen schwer umkehrbar. -->
            <div class="relative overflow-hidden rounded bg-white" @dblclick="openPreview(page.n)">
              <img
                :src="splitPageThumbnailUrl(batch.id, page.n)"
                :alt="`Seite ${page.n}`"
                loading="lazy"
                class="mx-auto aspect-[1/1.414] w-full object-contain transition"
                :class="excluded.has(page.n) ? 'opacity-40 grayscale' : ''"
              />
              <button
                type="button"
                class="absolute right-1 top-1 rounded-full bg-white/90 p-1 text-gray-600 shadow hover:text-gray-900"
                :aria-label="`Seite ${page.n} groß anzeigen`"
                title="Groß anzeigen"
                @click="openPreview(page.n)"
              >
                <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                  <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5M11 8v6M8 11h6" />
                </svg>
              </button>
            </div>
            <div class="mt-1 flex items-center justify-between gap-1">
              <span class="text-xs font-medium text-gray-700">Seite {{ page.n }}</span>
              <button
                type="button"
                class="text-xs text-gray-600 underline-offset-2 hover:text-gray-900 hover:underline"
                :disabled="!editable"
                @click="toggleExcluded(page.n)"
              >{{ excluded.has(page.n) ? 'Wieder aufnehmen' : 'Seite weglassen' }}<span class="sr-only"> (Seite {{ page.n }})</span></button>
            </div>
            <div class="mt-1 flex flex-wrap gap-1">
              <span v-if="pageLabel(page)" class="badge bg-gray-200 text-gray-700">{{ pageLabel(page) }}</span>
              <span v-if="excluded.has(page.n)" class="badge bg-red-100 text-red-700">Weggelassen</span>
              <span v-if="isUnrecognized(page.n)" class="badge bg-amber-100 text-amber-700">ohne Texterkennung</span>
            </div>
          </div>

          <!-- Uebergang nach dieser Seite -->
          <button
            v-if="page.n < batch.page_count"
            :ref="(el) => { markerRefs[page.n] = el }"
            type="button"
            class="split-marker group absolute -right-6 top-6 bottom-0 flex w-6 items-stretch justify-center focus:outline-none"
            :class="`split-marker-${markerState(page.n)}`"
            :data-state="markerState(page.n)"
            :data-testid="`split-marker-${page.n}`"
            :aria-label="markerTitle(page.n)"
            aria-describedby="split-marker-help"
            :title="markerTitle(page.n)"
            :aria-pressed="markerState(page.n) === 'cut'"
            :tabindex="focusedMarker === page.n ? 0 : -1"
            :disabled="!editable"
            @click="toggleMarker(page.n)"
            @keydown="onMarkerKeydown($event, page.n)"
          >
            <span
              v-if="markerState(page.n) === 'cut'"
              class="relative w-1 rounded bg-red-500 group-focus-visible:ring-2 group-focus-visible:ring-primary-500"
            >
              <svg class="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white p-0.5 text-red-600 shadow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="M20 4 8.12 15.88M14.47 14.48 20 20M8.12 8.12 12 12" />
              </svg>
            </span>
            <span
              v-else-if="markerState(page.n) === 'uncertain'"
              class="w-0 border-l-2 border-dashed border-orange-400 group-focus-visible:border-primary-500"
            ></span>
            <!-- Touch-Geraete haben kein Hovern: dort ist die Stelle immer
                 als feine Linie sichtbar (sonst unauffindbar). -->
            <span
              v-else
              class="w-px bg-transparent group-hover:bg-gray-400 group-focus-visible:w-1 group-focus-visible:bg-primary-400 pointer-coarse:bg-gray-300"
            ></span>
          </button>
        </li>
      </ol>

      <p v-if="split.error" class="text-sm text-red-600" role="status" data-testid="split-error">{{ split.error }}</p>

      <!-- Aktionen: Mobile als feste Leiste ueber der Navigation. pb-6: der
           hervorstehende Scan-Button der Bottom-Navigation ragt in die Leiste. -->
      <div
        class="fixed inset-x-0 bottom-16 z-30 flex flex-wrap gap-2 border-t border-gray-200 bg-white px-3 pb-6 pt-3 shadow-lg lg:static lg:z-auto lg:border-0 lg:bg-transparent lg:p-0 lg:shadow-none"
        data-testid="split-actions"
      >
        <button
          type="button"
          class="btn-primary flex-1 lg:flex-none"
          :disabled="!editable || !!split.error || busy || sourceMissing"
          @click="handleConfirm"
        >In {{ partCountLabel }} aufteilen</button>
        <button
          type="button"
          class="btn-secondary flex-1 lg:flex-none"
          :disabled="!editable || busy || sourceMissing"
          @click="handleKeepWhole"
        >Als ein Dokument behalten</button>
        <button
          v-if="canDiscard"
          type="button"
          class="btn-danger flex-1 lg:flex-none"
          :disabled="busy || conflict"
          @click="showDiscardDialog = true"
        >Verwerfen</button>
      </div>
    </template>

    <!-- Grossansicht -->
    <teleport to="body">
      <div v-if="previewPage" class="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div class="fixed inset-0 bg-black/70" aria-hidden="true" @click="closePreview"></div>
        <div
          ref="previewPanel"
          role="dialog"
          aria-modal="true"
          :aria-label="`Seite ${previewPage} von ${batch.page_count}`"
          tabindex="-1"
          class="relative z-10 flex max-h-full w-full max-w-3xl flex-col rounded-xl bg-white p-3 shadow-xl focus:outline-none"
          data-testid="split-preview"
          @keydown="onPreviewKeydown"
        >
          <div class="mb-2 flex items-center justify-between gap-2">
            <button type="button" class="btn-secondary !px-3" :disabled="previewPage <= 1" aria-label="Vorherige Seite" @click="previewPage -= 1">&larr;</button>
            <span class="text-sm font-medium text-gray-700">Seite {{ previewPage }} von {{ batch.page_count }}</span>
            <div class="flex gap-2">
              <button type="button" class="btn-secondary !px-3" :disabled="previewPage >= batch.page_count" aria-label="Nächste Seite" @click="previewPage += 1">&rarr;</button>
              <button type="button" class="btn-secondary !px-3" aria-label="Großansicht schließen" @click="closePreview">&times;</button>
            </div>
          </div>
          <div class="min-h-0 flex-1 overflow-auto">
            <img :src="splitPagePreviewUrl(batch.id, previewPage)" :alt="`Seite ${previewPage}`" class="mx-auto max-h-[80vh] object-contain" />
          </div>
        </div>
      </div>
    </teleport>

    <ConfirmDialog
      :show="pendingDuplexCut !== null"
      title="Blatt trennen?"
      message="Dieser Schnitt trennt Vorder- und Rückseite eines Blatts. Trotzdem schneiden?"
      confirm-text="Trotzdem schneiden"
      @confirm="confirmDuplexCut"
      @cancel="pendingDuplexCut = null"
    />

    <ConfirmDialog
      :show="showUndoDialog"
      :title="UNDO_TITLE"
      :message="undoMessage(batch.origin)"
      confirm-text="Rückgängig machen"
      :danger="true"
      @confirm="handleUndo"
      @cancel="showUndoDialog = false"
    />

    <ConfirmDialog
      :show="showDiscardDialog"
      title="Stapel verwerfen"
      :message="discardMessage"
      confirm-text="Verwerfen"
      :danger="true"
      @confirm="handleDiscard"
      @cancel="showDiscardDialog = false"
    />
  </div>
</template>
