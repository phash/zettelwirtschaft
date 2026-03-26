<script setup>
import { ref, onMounted, computed } from 'vue'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import { getReviewDocuments, getReviewDetail, answerReviewQuestion, approveReview, skipReview, reanalyzeDocument } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

const notify = useNotificationStore()
const documents = ref([])
const loading = ref(true)
const currentIndex = ref(0)
const currentDoc = ref(null)
const reviewData = ref(null)
const loadingDoc = ref(false)
const answers = ref({})
const currentQuestionIdx = ref(0)

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

function onDragStart(e) {
  if (zoomLevel.value <= 1) return
  isDragging.value = true
  dragStart.value = { x: e.clientX, y: e.clientY, panX: panX.value, panY: panY.value }
}

function onDragMove(e) {
  if (!isDragging.value) return
  panX.value = dragStart.value.panX + (e.clientX - dragStart.value.x)
  panY.value = dragStart.value.panY + (e.clientY - dragStart.value.y)
}

function onDragEnd() { isDragging.value = false }

function onTouchStart(e) {
  if (e.touches.length === 1) {
    isDragging.value = true
    dragStart.value = { x: e.touches[0].clientX - panX.value, y: e.touches[0].clientY - panY.value, panX: panX.value, panY: panY.value }
  }
}

function onTouchMove(e) {
  if (isDragging.value && e.touches.length === 1) {
    panX.value = e.touches[0].clientX - dragStart.value.x
    panY.value = e.touches[0].clientY - dragStart.value.y
  }
}

function onTouchEnd() {
  isDragging.value = false
}

const totalCount = computed(() => documents.value.length)

const questions = computed(() => reviewData.value?.questions || [])
const currentQuestion = computed(() => questions.value[currentQuestionIdx.value])
const openQuestions = computed(() => questions.value.filter(q => !q.is_answered))
const progress = computed(() => {
  if (!questions.value.length) return 0
  const answered = questions.value.filter(q => q.is_answered).length
  return Math.round((answered / questions.value.length) * 100)
})

async function loadReviewDocs() {
  loading.value = true
  try {
    documents.value = await getReviewDocuments()
    if (documents.value.length > 0) {
      await loadCurrentDoc()
    }
  } catch {
    notify.error('Rückfragen konnten nicht geladen werden.')
  } finally {
    loading.value = false
  }
}

async function loadCurrentDoc() {
  if (currentIndex.value >= documents.value.length) return
  loadingDoc.value = true
  resetZoom()
  try {
    const docId = documents.value[currentIndex.value].id
    reviewData.value = await getReviewDetail(docId)
    currentDoc.value = reviewData.value
    answers.value = {}
    currentQuestionIdx.value = 0
    // Springe zur ersten unbeantworteten Frage
    const firstOpen = questions.value.findIndex(q => !q.is_answered)
    if (firstOpen >= 0) currentQuestionIdx.value = firstOpen
  } catch {
    notify.error('Dokument konnte nicht geladen werden.')
  } finally {
    loadingDoc.value = false
  }
}

async function submitAnswer(question) {
  const answer = answers.value[question.id]
  if (!answer?.trim()) return
  try {
    const result = await answerReviewQuestion(currentDoc.value.id, question.id, answer)
    question.is_answered = true
    question.answer = answer
    notify.success('Antwort gespeichert.')

    if (result.all_answered) {
      // Alle beantwortet - Bestätigung anbieten
      notify.success('Alle Fragen beantwortet!')
    } else {
      // Nächste unbeantwortete Frage
      const nextOpen = questions.value.findIndex((q, i) => i > currentQuestionIdx.value && !q.is_answered)
      if (nextOpen >= 0) {
        currentQuestionIdx.value = nextOpen
      }
    }
  } catch {
    notify.error('Fehler beim Speichern der Antwort.')
  }
}

async function doApprove() {
  try {
    await approveReview(currentDoc.value.id)
    notify.success('Dokument bestätigt.')
    await nextDocument()
  } catch {
    notify.error('Fehler beim Bestätigen.')
  }
}

async function nextDocument() {
  if (currentIndex.value < documents.value.length - 1) {
    currentIndex.value++
    await loadCurrentDoc()
  } else {
    currentDoc.value = null
    reviewData.value = null
    await loadReviewDocs()
  }
}

async function skipDocument() {
  try {
    await skipReview(currentDoc.value.id)
  } catch {
    // ignore
  }
  await nextDocument()
}

const reanalyzing = ref(false)

async function doReanalyze() {
  reanalyzing.value = true
  try {
    const result = await reanalyzeDocument(currentDoc.value.id)
    if (result.needs_review) {
      notify.success('Analyse wiederholt — bitte neue Rückfragen prüfen.')
      await loadCurrentDoc()
    } else {
      notify.success('Analyse erfolgreich — Dokument automatisch bestätigt.')
      await nextDocument()
    }
  } catch (e) {
    const msg = e.response?.status === 503
      ? 'LLM nicht erreichbar. Bitte später erneut versuchen.'
      : 'Fehler bei der Re-Analyse.'
    notify.error(msg)
  } finally {
    reanalyzing.value = false
  }
}

const needsReanalysis = computed(() => {
  if (!questions.value.length) return false
  return questions.value.some(q =>
    !q.is_answered && q.question?.includes('LLM nicht erreichbar')
  )
})

const fileUrl = computed(() =>
  currentDoc.value ? `/api/documents/${currentDoc.value.id}/file` : ''
)

const questionTypeLabels = {
  classification: 'Klassifikation',
  extraction: 'Extraktion',
  context: 'Kontext',
  confirmation: 'Bestätigung',
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

function fieldValue(fieldName) {
  if (!reviewData.value?.confident_fields || !fieldName) return null
  const cf = reviewData.value.confident_fields
  if (fieldName === 'amount' && cf.amount != null) {
    return `${cf.amount} ${cf.currency || '€'}`
  }
  return cf[fieldName] || null
}

function isFieldHighlighted(fieldKey) {
  const q = currentQuestion.value
  if (!q || q.is_answered) return false
  return q.field_affected === fieldKey
}

function questionTypeBadge(type) {
  const classes = {
    classification: 'bg-blue-100 text-blue-700',
    extraction: 'bg-purple-100 text-purple-700',
    context: 'bg-amber-100 text-amber-700',
    confirmation: 'bg-green-100 text-green-700',
  }
  return classes[type] || 'bg-gray-100 text-gray-700'
}

onMounted(loadReviewDocs)
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-bold text-gray-900">Zu prüfen</h1>
      <span v-if="totalCount > 0" class="text-sm text-gray-500">
        {{ currentIndex + 1 }} von {{ totalCount }} Dokumenten
      </span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-20">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
    </div>

    <!-- Empty state -->
    <div v-else-if="totalCount === 0" class="card text-center py-16">
      <svg class="mx-auto h-16 w-16 text-green-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <p class="mt-4 text-lg font-medium text-gray-600">Alles geprüft!</p>
      <p class="mt-1 text-sm text-gray-400">Keine Dokumente mit offenen Rückfragen.</p>
    </div>

    <!-- Review content -->
    <div v-else-if="currentDoc" class="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <!-- Document preview -->
      <div class="card !p-0 overflow-hidden lg:col-span-1">
        <div class="p-3 border-b border-gray-200 flex items-center gap-2">
          <DocTypeBadge :type="currentDoc.document_type" />
          <span class="text-sm font-medium text-gray-900 truncate flex-1">{{ currentDoc.title }}</span>
          <!-- Download & open in new tab -->
          <a :href="fileUrl" :download="currentDoc.original_filename" title="Herunterladen"
            class="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700">
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
          </a>
          <a :href="fileUrl" target="_blank" rel="noopener" title="In neuem Tab öffnen"
            class="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700">
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
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
          @wheel.prevent="onWheel"
          @mousedown.prevent="onDragStart"
          @mousemove="onDragMove"
          @mouseup="onDragEnd"
          @mouseleave="onDragEnd"
          @touchstart.prevent="onTouchStart"
          @touchmove.prevent="onTouchMove"
          @touchend="onTouchEnd"
          :style="{ cursor: isDragging ? 'grabbing' : (zoomLevel > 1 ? 'grab' : 'default') }"
        >
          <!-- Zoom controls overlay -->
          <div class="absolute bottom-3 right-3 z-10 flex items-center gap-1 rounded-lg bg-white/90 shadow-md px-2 py-1">
            <button @click="zoomOut" title="Verkleinern" class="rounded p-0.5 text-gray-600 hover:bg-gray-100 text-base leading-none font-medium w-6 h-6 flex items-center justify-center">−</button>
            <span class="text-xs text-gray-600 w-10 text-center">{{ Math.round(zoomLevel * 100) }}%</span>
            <button @click="zoomIn" title="Vergrößern" class="rounded p-0.5 text-gray-600 hover:bg-gray-100 text-base leading-none font-medium w-6 h-6 flex items-center justify-center">+</button>
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
        <div v-if="reviewData?.confident_fields" class="card !p-4 bg-gray-50">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Erkannte Daten</h3>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <div v-if="reviewData.confident_fields.title"
              :class="['rounded-md px-2 py-1 transition-colors', isFieldHighlighted('title') ? 'bg-amber-100 ring-1 ring-amber-300' : '']">
              <span class="text-gray-500">Titel:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.title }}</span>
            </div>
            <div v-if="reviewData.confident_fields.document_type"
              :class="['rounded-md px-2 py-1 transition-colors', isFieldHighlighted('document_type') ? 'bg-amber-100 ring-1 ring-amber-300' : '']">
              <span class="text-gray-500">Typ:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.document_type }}</span>
            </div>
            <div v-if="reviewData.confident_fields.issuer"
              :class="['rounded-md px-2 py-1 transition-colors', isFieldHighlighted('issuer') ? 'bg-amber-100 ring-1 ring-amber-300' : '']">
              <span class="text-gray-500">Aussteller:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.issuer }}</span>
            </div>
            <div v-if="reviewData.confident_fields.document_date"
              :class="['rounded-md px-2 py-1 transition-colors', isFieldHighlighted('document_date') ? 'bg-amber-100 ring-1 ring-amber-300' : '']">
              <span class="text-gray-500">Datum:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.document_date }}</span>
            </div>
            <div v-if="reviewData.confident_fields.amount != null"
              :class="['rounded-md px-2 py-1 transition-colors', isFieldHighlighted('amount') ? 'bg-amber-100 ring-1 ring-amber-300' : '']">
              <span class="text-gray-500">Betrag:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.amount }} {{ reviewData.confident_fields.currency }}</span>
            </div>
            <div v-if="reviewData.confident_fields.filing_scope"
              :class="['rounded-md px-2 py-1 transition-colors', isFieldHighlighted('filing_scope') ? 'bg-amber-100 ring-1 ring-amber-300' : '']">
              <span class="text-gray-500">Bereich:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.filing_scope }}</span>
            </div>
          </div>
        </div>

        <!-- Fortschritt -->
        <div class="flex items-center gap-3">
          <div class="flex-1 h-2 rounded-full bg-gray-200 overflow-hidden">
            <div class="h-full bg-primary-500 transition-all" :style="{ width: progress + '%' }"></div>
          </div>
          <span class="text-xs text-gray-500 whitespace-nowrap">
            {{ questions.filter(q => q.is_answered).length }}/{{ questions.length }}
          </span>
        </div>

        <!-- Re-Analyse Hinweis -->
        <div v-if="needsReanalysis" class="rounded-lg bg-blue-50 border border-blue-200 p-4">
          <div class="flex items-start gap-3">
            <svg class="h-5 w-5 text-blue-500 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            <div class="flex-1">
              <p class="text-sm font-medium text-blue-800">Die KI-Analyse war beim ersten Versuch nicht verfügbar.</p>
              <p class="text-xs text-blue-600 mt-1">Klicke auf „Erneut analysieren", um die automatische Erkennung mit dem vorhandenen OCR-Text zu wiederholen.</p>
            </div>
            <button @click="doReanalyze" :disabled="reanalyzing" class="btn-primary text-sm whitespace-nowrap">
              {{ reanalyzing ? 'Analysiere...' : 'Erneut analysieren' }}
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
              <p class="text-sm font-medium text-gray-700 mb-1">{{ q.question }}</p>
              <p v-if="q.explanation" class="text-xs text-gray-400 mb-2">{{ q.explanation }}</p>

              <!-- Kontext-Card: betroffenes Feld + erkannter Wert -->
              <div v-if="q.field_affected && fieldValue(q.field_affected) && !q.is_answered"
                class="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 mb-2">
                <div class="flex items-center gap-2 text-xs">
                  <svg class="h-3.5 w-3.5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span class="text-amber-700">
                    <span class="font-medium">{{ fieldLabels[q.field_affected] || q.field_affected }}:</span>
                    <span class="ml-1">{{ fieldValue(q.field_affected) }}</span>
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
                    v-for="suggestion in (typeof q.suggested_answers === 'string' ? q.suggested_answers.split('|') : q.suggested_answers)"
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
                      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" /></svg>
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
                  @keydown.ctrl.enter="submitAnswer(q)"
                ></textarea>
                <button @click="submitAnswer(q)" class="btn-primary text-sm">Beantworten</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Aktionen -->
        <div class="flex justify-between">
          <button @click="skipDocument" class="btn-secondary">Überspringen</button>
          <div class="flex gap-2">
            <router-link :to="`/dokumente/${currentDoc.id}`" class="btn-secondary">Details</router-link>
            <button
              v-if="openQuestions.length === 0"
              @click="doApprove"
              class="btn-primary"
            >
              Bestätigen
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
