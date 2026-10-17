<script setup>
import { ref, onMounted, computed } from 'vue'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import { getReviewDocuments, getReviewDetail, answerReviewQuestion, approveReview, acceptReview, reanalyzeDocument } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { useBackgroundTask } from '../composables/useBackgroundTask'
import { typeLabels } from '../constants/documentTypes'
import { formatDate, formatAmount } from '../utils/formatters'
import { pluralize } from '../utils/pluralize'
import { apiErrorMessage } from '../utils/apiErrors'

const notify = useNotificationStore()
const documents = ref([])
const loading = ref(true)
const currentDoc = ref(null)
// ID des angezeigten (bzw. zuletzt versuchten) Dokuments — auch gesetzt, wenn
// das Laden fehlschlug, damit "Später" im Fehlerzustand funktioniert.
const currentId = ref(null)
const reviewData = ref(null)
const loadError = ref(false)
const answers = ref({})
const currentQuestionIdx = ref(0)
const showAcceptConfirm = ref(false)

// --- "Später": rein clientseitiges Zurückstellen -------------------------
// Ändert den Review-Status NICHT (vorher setzte /skip den Status auf OK und
// das Dokument verschwand mitsamt offener Rückfragen dauerhaft). Die Liste der
// zurückgestellten IDs lebt nur in dieser Browser-Sitzung.
const LATER_KEY = 'zw_review_later'

function readLater() {
  try {
    const raw = sessionStorage.getItem(LATER_KEY)
    const ids = raw ? JSON.parse(raw) : []
    return Array.isArray(ids) ? ids.map(String) : []
  } catch {
    return []
  }
}

const laterIds = ref(readLater())

function persistLater() {
  try {
    sessionStorage.setItem(LATER_KEY, JSON.stringify(laterIds.value))
  } catch {
    // Speicher blockiert (privates Fenster) — Zurückstellen gilt dann nur bis zum Reload.
  }
}

const isLater = (doc) => laterIds.value.includes(String(doc.id))
// Warteschlange = alle offenen Dokumente ohne die zurückgestellten.
const queue = computed(() => documents.value.filter(d => !isLater(d)))
const laterCount = computed(() => documents.value.filter(isLater).length)

// Zoom & Pan state
const zoomLevel = ref(1)
const panX = ref(0)
const panY = ref(0)
const isDragging = ref(false)
const dragStart = ref({ x: 0, y: 0, panX: 0, panY: 0 })

function zoomIn() { zoomLevel.value = Math.min(zoomLevel.value + 0.25, 4) }
function zoomOut() { zoomLevel.value = Math.max(zoomLevel.value - 0.25, 0.5) }
function resetZoom() { zoomLevel.value = 1; panX.value = 0; panY.value = 0 }

function onWheel(e) {
  const delta = e.deltaY > 0 ? -0.15 : 0.15
  zoomLevel.value = Math.max(0.5, Math.min(4, zoomLevel.value + delta))
}

// Pointer Events decken Maus, Touch und Stift gemeinsam ab. Verschieben nur
// bei Zoom > 1 — sonst bleibt die Geste beim Browser (touch-action pan-y), so
// dass man auf dem Handy an der Vorschau vorbei zu den Fragen scrollen kann.
function onPointerDown(e) {
  if (zoomLevel.value <= 1) return
  isDragging.value = true
  e.currentTarget.setPointerCapture?.(e.pointerId)
  dragStart.value = { x: e.clientX, y: e.clientY, panX: panX.value, panY: panY.value }
}

function onPointerMove(e) {
  if (!isDragging.value) return
  panX.value = dragStart.value.panX + (e.clientX - dragStart.value.x)
  panY.value = dragStart.value.panY + (e.clientY - dragStart.value.y)
}

function onPointerUp() { isDragging.value = false }

const questions = computed(() => reviewData.value?.questions || [])
const openQuestions = computed(() => questions.value.filter(q => !q.is_answered))
const answeredCount = computed(() => questions.value.length - openQuestions.value.length)
const progress = computed(() => {
  if (!questions.value.length) return 0
  return Math.round((answeredCount.value / questions.value.length) * 100)
})

// Lädt die Liste frisch und wählt das erste nicht zurückgestellte Dokument.
// Nach jeder abschließenden Aktion (Bestätigen, So übernehmen, Re-Analyse)
// wird neu geladen — so gibt es keinen veralteten Index mehr, der hinter das
// Listenende zeigt, und neu eingetroffene Dokumente erscheinen sofort.
async function loadReviewDocs() {
  loading.value = true
  try {
    documents.value = await getReviewDocuments()
    // Zurückgestellte IDs, die nicht mehr offen sind, aufräumen.
    const openIds = new Set(documents.value.map(d => String(d.id)))
    const pruned = laterIds.value.filter(id => openIds.has(id))
    if (pruned.length !== laterIds.value.length) {
      laterIds.value = pruned
      persistLater()
    }
    await showFirstInQueue()
  } catch {
    notify.error('Rückfragen konnten nicht geladen werden.')
  } finally {
    loading.value = false
  }
}

async function showFirstInQueue() {
  const next = queue.value[0]
  if (next) {
    await loadDoc(next.id)
  } else {
    clearCurrent()
  }
}

function clearCurrent() {
  reanalysis.reset()
  currentId.value = null
  currentDoc.value = null
  reviewData.value = null
  loadError.value = false
}

async function loadDoc(docId) {
  reanalysis.reset()
  resetZoom()
  currentId.value = docId
  loadError.value = false
  try {
    const data = await getReviewDetail(docId)
    reviewData.value = data
    currentDoc.value = data
    answers.value = {}
    currentQuestionIdx.value = 0
    // Springe zur ersten unbeantworteten Frage
    const firstOpen = questions.value.findIndex(q => !q.is_answered)
    if (firstOpen >= 0) currentQuestionIdx.value = firstOpen
    // Läuft für dieses Dokument noch eine Re-Analyse (z.B. Seite neu geladen)?
    reanalysis.resume({ kind: 'reanalyze', key: String(docId) })
  } catch {
    currentDoc.value = null
    reviewData.value = null
    loadError.value = true
    notify.error('Dokument konnte nicht geladen werden.')
  }
}

function retryLoad() {
  loadReviewDocs()
}

async function submitAnswer(question) {
  const answer = answers.value[question.id]
  if (!answer?.trim()) return
  try {
    const result = await answerReviewQuestion(question.id, answer)
    question.is_answered = true
    question.answer = answer
    notify.success('Antwort gespeichert.')

    if (result.all_answered) {
      notify.success('Alle Fragen beantwortet!')
    } else {
      // Nächste unbeantwortete Frage
      const nextOpen = questions.value.findIndex((q, i) => i > currentQuestionIdx.value && !q.is_answered)
      if (nextOpen >= 0) {
        currentQuestionIdx.value = nextOpen
      }
    }
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Fehler beim Speichern der Antwort.'))
  }
}

// Bestätigen (alle Fragen beantwortet, /approve) und "So übernehmen" (KI-
// Ergebnis mit offenen Fragen unverändert akzeptieren, /accept) setzen beide
// REVIEWED; das Backend protokolliert die Aktionen getrennt im AuditLog.
async function closeReview(action, successText, errorText) {
  try {
    await action(currentDoc.value.id)
    notify.success(successText)
    await loadReviewDocs()
  } catch (e) {
    notify.error(apiErrorMessage(e, errorText))
  }
}

function doApprove() {
  return closeReview(approveReview, 'Dokument bestätigt.', 'Fehler beim Bestätigen.')
}

async function confirmAccept() {
  showAcceptConfirm.value = false
  await closeReview(acceptReview, 'Erkannte Daten übernommen.', 'Fehler beim Übernehmen.')
}

async function deferDocument() {
  if (currentId.value == null) return
  const id = String(currentId.value)
  if (!laterIds.value.includes(id)) {
    laterIds.value = [...laterIds.value, id]
    persistLater()
  }
  await showFirstInQueue()
}

async function reviewLaterAgain() {
  laterIds.value = []
  persistLater()
  await showFirstInQueue()
}

// Re-Analyse läuft als Hintergrund-Vorgang (HTTP 202 + Polling) — sie kann
// auf CPU-Hardware Minuten dauern.
const reanalysis = useBackgroundTask({
  onSuccess: async (result, task) => {
    const sameDoc = currentDoc.value && String(currentDoc.value.id) === String(task?.key)
    if (result?.needs_review) {
      notify.success('Analyse wiederholt — bitte neue Rückfragen prüfen.')
      if (sameDoc) await loadDoc(currentDoc.value.id)
    } else {
      notify.success('Analyse erfolgreich — Dokument automatisch bestätigt.')
      await loadReviewDocs()
    }
  },
  onError: (message) => notify.error(message),
})

function doReanalyze() {
  const id = currentDoc.value.id
  reanalysis.start(() => reanalyzeDocument(id))
}

// Primär über den strukturierten Fragetyp; der Textvergleich bleibt nur als
// Fallback für Rückfragen, die vor Einführung von question_type entstanden.
const needsReanalysis = computed(() =>
  openQuestions.value.some(q =>
    q.question_type === 'analysis_failed' || q.question?.includes('LLM nicht erreichbar')
  )
)

const fileUrl = computed(() =>
  currentDoc.value ? `/api/documents/${currentDoc.value.id}/file` : ''
)

const questionTypeLabels = {
  classification: 'Klassifikation',
  extraction: 'Extraktion',
  context: 'Kontext',
  confirmation: 'Bestätigung',
  analysis_failed: 'Analyse fehlgeschlagen',
}

const fieldLabels = {
  title: 'Titel',
  document_type: 'Dokumenttyp',
  document_date: 'Datum',
  amount: 'Betrag',
  currency: 'Währung',
  issuer: 'Aussteller',
  recipient: 'Empfänger',
  reference_number: 'Referenznummer',
  summary: 'Zusammenfassung',
  tax_relevant: 'Steuerrelevant',
  tax_category: 'Steuerkategorie',
  filing_scope: 'Ablagebereich',
}

// Felder der Box "Erkannte Daten" (Reihenfolge = Anzeige).
const summaryFields = [
  { key: 'title', label: 'Titel' },
  { key: 'document_type', label: 'Typ' },
  { key: 'issuer', label: 'Aussteller' },
  { key: 'document_date', label: 'Datum' },
  { key: 'amount', label: 'Betrag' },
  { key: 'filing_scope', label: 'Bereich' },
]

// Einheitliche Anzeige eines erkannten Werts: Typ-Label statt Enum, deutsches
// Datum, formatierter Betrag mit Währung. null = nicht erkannt.
function displayValue(fieldName) {
  const cf = reviewData.value?.confident_fields
  if (!cf || !fieldName) return null
  const raw = cf[fieldName]
  if (raw == null || raw === '') return null
  if (fieldName === 'document_type') return typeLabels[raw] || raw
  if (fieldName === 'document_date') return formatDate(raw)
  if (fieldName === 'amount') return formatAmount(raw, cf.currency || 'EUR')
  if (typeof raw === 'boolean') return raw ? 'Ja' : 'Nein'
  return String(raw)
}

function isFieldHighlighted(fieldKey) {
  const q = questions.value[currentQuestionIdx.value]
  if (!q || q.is_answered) return false
  return q.field_affected === fieldKey
}

function questionTypeBadge(type) {
  const classes = {
    classification: 'bg-blue-100 text-blue-700',
    extraction: 'bg-purple-100 text-purple-700',
    context: 'bg-amber-100 text-amber-700',
    confirmation: 'bg-green-100 text-green-700',
    analysis_failed: 'bg-red-100 text-red-700',
  }
  return classes[type] || 'bg-gray-100 text-gray-700'
}

function suggestionsOf(q) {
  return typeof q.suggested_answers === 'string' ? q.suggested_answers.split('|') : q.suggested_answers
}

onMounted(loadReviewDocs)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h1 class="text-2xl font-bold text-gray-900">Zu prüfen</h1>
      <span v-if="documents.length > 0" class="text-sm text-gray-500" data-testid="review-counter">
        Noch {{ pluralize(queue.length, 'Dokument', 'Dokumente') }} offen<template v-if="laterCount"> · {{ laterCount }} auf später verschoben</template>
      </span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-20">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
    </div>

    <!-- Empty state -->
    <div v-else-if="documents.length === 0" class="card text-center py-16">
      <svg class="mx-auto h-16 w-16 text-green-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <p class="mt-4 text-lg font-medium text-gray-600">Alles geprüft!</p>
      <p class="mt-1 text-sm text-gray-400">Keine Dokumente mit offenen Rückfragen.</p>
    </div>

    <!-- Ende der Warteschlange, aber zurückgestellte Dokumente vorhanden -->
    <div v-else-if="queue.length === 0" class="card text-center py-16" data-testid="review-later-end">
      <p class="text-lg font-medium text-gray-600">
        {{ pluralize(laterCount, 'Dokument', 'Dokumente') }} auf später verschoben
      </p>
      <p class="mt-1 text-sm text-gray-400">Alle übrigen Dokumente sind bearbeitet.</p>
      <button @click="reviewLaterAgain" class="btn-primary mt-4">Erneut durchgehen</button>
    </div>

    <!-- Review content -->
    <div v-else-if="currentDoc" class="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <!-- Document preview -->
      <div class="card !p-0 overflow-hidden lg:col-span-1">
        <div class="p-3 border-b border-gray-200 flex items-center gap-2">
          <DocTypeBadge :type="currentDoc.document_type" />
          <span class="text-sm font-medium text-gray-900 truncate flex-1">{{ currentDoc.title }}</span>
          <!-- Download & open in new tab -->
          <a :href="fileUrl" :download="currentDoc.original_filename" title="Herunterladen" aria-label="Herunterladen"
            class="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700">
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
          </a>
          <a :href="fileUrl" target="_blank" rel="noopener" title="In neuem Tab öffnen" aria-label="In neuem Tab öffnen"
            class="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700">
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </div>
        <!-- PDF: native viewer -->
        <div v-if="currentDoc.file_type === 'pdf'" class="aspect-[3/4] bg-gray-100">
          <iframe :src="fileUrl" class="h-full w-full" title="Vorschau"></iframe>
        </div>
        <!-- Image: zoom + pan -->
        <div v-else
          class="relative aspect-[3/4] bg-gray-100 overflow-hidden select-none"
          data-testid="review-image-preview"
          @wheel.prevent="onWheel"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
          @lostpointercapture="onPointerUp"
          :style="{
            cursor: isDragging ? 'grabbing' : (zoomLevel > 1 ? 'grab' : 'default'),
            touchAction: zoomLevel > 1 ? 'none' : 'pan-y pinch-zoom',
          }"
        >
          <!-- Zoom controls overlay (eigene Pointer-Events: kein Pan-Start beim Tippen) -->
          <div class="absolute bottom-3 right-3 z-10 flex items-center gap-1 rounded-lg bg-white/90 shadow-md px-2 py-1"
            @pointerdown.stop>
            <button @click="zoomOut" title="Verkleinern" aria-label="Verkleinern" class="rounded p-0.5 text-gray-600 hover:bg-gray-100 text-base leading-none font-medium w-6 h-6 flex items-center justify-center">−</button>
            <span class="text-xs text-gray-600 w-10 text-center" data-testid="zoom-level">{{ Math.round(zoomLevel * 100) }}%</span>
            <button @click="zoomIn" title="Vergrößern" aria-label="Vergrößern" class="rounded p-0.5 text-gray-600 hover:bg-gray-100 text-base leading-none font-medium w-6 h-6 flex items-center justify-center">+</button>
            <button v-if="zoomLevel !== 1 || panX !== 0 || panY !== 0" @click="resetZoom" title="Zurücksetzen"
              class="ml-1 rounded px-1.5 py-0.5 text-xs text-gray-500 hover:bg-gray-100">1:1</button>
          </div>
          <div class="flex h-full w-full items-center justify-center">
            <img
              :src="fileUrl"
              :alt="currentDoc.title"
              draggable="false"
              :style="{
                transform: `translate(${panX}px, ${panY}px) scale(${zoomLevel})`,
                transformOrigin: 'center center',
                transition: isDragging ? 'none' : 'transform 0.1s ease',
                maxWidth: '100%',
                maxHeight: '100%',
                objectFit: 'contain',
              }"
            />
          </div>
        </div>
      </div>

      <!-- Questions (45%) -->
      <div class="space-y-4 lg:col-span-1">
        <!-- KI-Zusammenfassung -->
        <div v-if="reviewData?.confident_fields" class="card !p-4 bg-gray-50" data-testid="review-confident-fields">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Erkannte Daten</h3>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <template v-for="f in summaryFields" :key="f.key">
              <div v-if="displayValue(f.key)"
                :class="['rounded-md px-2 py-1 transition-colors', isFieldHighlighted(f.key) ? 'bg-amber-100 ring-1 ring-amber-300' : '']">
                <span class="text-gray-500">{{ f.label }}:</span>{{ ' ' }}<span class="text-gray-700">{{ displayValue(f.key) }}</span>
              </div>
            </template>
          </div>
        </div>

        <!-- Fortschritt -->
        <div class="flex items-center gap-3">
          <div class="flex-1 h-2 rounded-full bg-gray-200 overflow-hidden">
            <div class="h-full bg-primary-500 transition-all" :style="{ width: progress + '%' }"></div>
          </div>
          <span class="text-xs text-gray-500 whitespace-nowrap">
            {{ answeredCount }}/{{ questions.length }}
          </span>
        </div>

        <!-- Re-Analyse Hinweis -->
        <div v-if="needsReanalysis" class="rounded-lg bg-blue-50 border border-blue-200 p-4" data-testid="reanalysis-panel">
          <div class="flex items-start gap-3">
            <svg class="h-5 w-5 text-blue-500 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            <div class="flex-1">
              <p class="text-sm font-medium text-blue-800">Die KI-Analyse war beim ersten Versuch nicht verfügbar.</p>
              <p class="text-xs text-blue-600 mt-1">Klicke auf „Erneut analysieren“, um die automatische Erkennung mit dem vorhandenen OCR-Text zu wiederholen.</p>
              <p v-if="reanalysis.running.value" class="text-xs text-blue-700 mt-2" role="status">
                {{ reanalysis.progressText.value || 'Analyse läuft im Hintergrund …' }}
              </p>
            </div>
            <button @click="doReanalyze" :disabled="reanalysis.running.value" class="btn-primary text-sm whitespace-nowrap">
              {{ reanalysis.running.value ? 'Analysiere …' : 'Erneut analysieren' }}
            </button>
          </div>
        </div>

        <!-- Fragen-Cards -->
        <div class="card">
          <h2 class="text-lg font-semibold text-gray-900 mb-4">Rückfragen der KI</h2>
          <div class="space-y-6">
            <div v-for="(q, idx) in questions" :key="q.id" :class="idx === currentQuestionIdx ? '' : 'opacity-60'">
              <div class="flex items-start gap-2 mb-2">
                <span class="badge bg-gray-100 text-gray-600 text-xs">{{ idx + 1 }}</span>
                <span v-if="q.question_type" :class="['badge text-xs', questionTypeBadge(q.question_type)]">
                  {{ questionTypeLabels[q.question_type] || q.question_type }}
                </span>
              </div>
              <p :id="`review-q-${q.id}`" class="text-sm font-medium text-gray-700 mb-1">{{ q.question }}</p>
              <p v-if="q.explanation" class="text-xs text-gray-400 mb-2">{{ q.explanation }}</p>

              <!-- Kontext-Card: betroffenes Feld + erkannter Wert -->
              <div v-if="q.field_affected && displayValue(q.field_affected) && !q.is_answered"
                class="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 mb-2">
                <div class="flex items-center gap-2 text-xs">
                  <svg class="h-3.5 w-3.5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span class="text-amber-700">
                    <span class="font-medium">{{ fieldLabels[q.field_affected] || q.field_affected }}:</span>{{ ' ' }}<span>{{ displayValue(q.field_affected) }}</span>
                  </span>
                </div>
              </div>

              <!-- Beantwortet -->
              <div v-if="q.is_answered" class="rounded-lg bg-green-50 border border-green-200 p-3 text-sm text-green-700">
                {{ q.answer }}
              </div>

              <!-- Antwort-Eingabe -->
              <div v-else class="space-y-2">
                <!-- Vorschlaege als Buttons -->
                <div v-if="q.suggested_answers" class="flex flex-wrap gap-2">
                  <button
                    v-for="suggestion in suggestionsOf(q)"
                    :key="suggestion"
                    @click="answers[q.id] = suggestion.trim()"
                    :class="[
                      'badge cursor-pointer transition-colors',
                      answers[q.id] === suggestion.trim()
                        ? (suggestion.trim().startsWith('NEU: ') ? 'bg-green-100 text-green-700 ring-1 ring-green-400' : 'bg-primary-100 text-primary-700 ring-1 ring-primary-300')
                        : (suggestion.trim().startsWith('NEU: ') ? 'bg-green-50 text-green-600 hover:bg-green-100 border border-dashed border-green-300' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'),
                    ]"
                  >
                    <span v-if="suggestion.trim().startsWith('NEU: ')" class="inline-flex items-center gap-1">
                      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" /></svg>
                      {{ suggestion.trim().substring(5) }}
                      <span class="text-xs opacity-70">(neu erstellen)</span>
                    </span>
                    <span v-else>{{ suggestion.trim() }}</span>
                  </button>
                </div>
                <textarea
                  v-model="answers[q.id]"
                  class="input"
                  rows="2"
                  placeholder="Antwort eingeben..."
                  :aria-labelledby="`review-q-${q.id}`"
                  @keydown.ctrl.enter="submitAnswer(q)"
                ></textarea>
                <button @click="submitAnswer(q)" class="btn-primary text-sm">Beantworten</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Aktionen -->
        <div class="flex flex-wrap justify-between gap-2">
          <button @click="deferDocument" class="btn-secondary" title="Dokument bleibt offen und kommt am Ende erneut">Später</button>
          <div class="flex flex-wrap gap-2">
            <router-link :to="`/dokumente/${currentDoc.id}`" class="btn-secondary">Details</router-link>
            <button
              v-if="openQuestions.length === 0"
              @click="doApprove()"
              class="btn-primary"
            >
              Bestätigen
            </button>
            <button
              v-else
              @click="showAcceptConfirm = true"
              class="btn-primary"
            >
              So übernehmen
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Fallback: Dokument konnte nicht geladen werden (nie eine leere Seite) -->
    <div v-else class="card text-center py-12" data-testid="review-load-error">
      <p class="text-gray-600">Das Dokument konnte nicht geladen werden.</p>
      <div class="mt-4 flex justify-center gap-2">
        <button @click="retryLoad" class="btn-primary">Erneut versuchen</button>
        <button v-if="queue.length > 1" @click="deferDocument" class="btn-secondary">Später</button>
      </div>
    </div>

    <ConfirmDialog
      :show="showAcceptConfirm"
      title="Erkannte Daten übernehmen?"
      :message="`Die erkannten Daten werden so übernommen und das Dokument gilt als geprüft. ${openQuestions.length === 1 ? 'Die offene Rückfrage wird' : `Die ${openQuestions.length} offenen Rückfragen werden`} nicht mehr gestellt.`"
      confirm-text="So übernehmen"
      @confirm="confirmAccept"
      @cancel="showAcceptConfirm = false"
    />
  </div>
</template>
