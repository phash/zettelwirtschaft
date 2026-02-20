<script setup>
import { ref, onMounted, computed } from 'vue'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import { getReviewDocuments, getReviewDetail, answerReviewQuestion, approveReview, skipReview } from '../services/api'
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
    notify.error('Rueckfragen konnten nicht geladen werden.')
  } finally {
    loading.value = false
  }
}

async function loadCurrentDoc() {
  if (currentIndex.value >= documents.value.length) return
  loadingDoc.value = true
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
      // Alle beantwortet - Bestaetigung anbieten
      notify.success('Alle Fragen beantwortet!')
    } else {
      // Naechste unbeantwortete Frage
      const nextOpen = questions.value.findIndex((q, i) => i > currentQuestionIdx.value && !q.is_answered)
      if (nextOpen >= 0) {
        currentQuestionIdx.value = nextOpen
      }
    }
  } catch {
    notify.error('Fehler beim Speichern.')
  }
}

async function doApprove() {
  try {
    await approveReview(currentDoc.value.id)
    notify.success('Dokument bestaetigt.')
    await nextDocument()
  } catch {
    notify.error('Fehler beim Bestaetigen.')
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

const fileUrl = computed(() =>
  currentDoc.value ? `/api/documents/${currentDoc.value.id}/file` : ''
)

const questionTypeLabels = {
  classification: 'Klassifikation',
  extraction: 'Extraktion',
  context: 'Kontext',
  confirmation: 'Bestaetigung',
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
      <h1 class="text-2xl font-bold text-gray-900">Zu pruefen</h1>
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
      <p class="mt-4 text-lg font-medium text-gray-600">Alles geprueft!</p>
      <p class="mt-1 text-sm text-gray-400">Keine Dokumente mit offenen Rueckfragen.</p>
    </div>

    <!-- Review content -->
    <div v-else-if="currentDoc" class="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <!-- Document preview (55%) -->
      <div class="card !p-0 overflow-hidden lg:col-span-1">
        <div class="p-4 border-b border-gray-200 flex items-center gap-3">
          <DocTypeBadge :type="currentDoc.document_type" />
          <span class="text-sm font-medium text-gray-900 truncate">{{ currentDoc.title }}</span>
        </div>
        <div class="aspect-[3/4] bg-gray-100">
          <iframe v-if="currentDoc.file_type === 'pdf'" :src="fileUrl" class="h-full w-full" title="Vorschau"></iframe>
          <img v-else :src="fileUrl" :alt="currentDoc.title" class="h-full w-full object-contain p-4" />
        </div>
      </div>

      <!-- Questions (45%) -->
      <div class="space-y-4 lg:col-span-1">
        <!-- KI-Zusammenfassung -->
        <div v-if="reviewData?.confident_fields" class="card !p-4 bg-gray-50">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Erkannte Daten</h3>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <div v-if="reviewData.confident_fields.document_type">
              <span class="text-gray-500">Typ:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.document_type }}</span>
            </div>
            <div v-if="reviewData.confident_fields.issuer">
              <span class="text-gray-500">Aussteller:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.issuer }}</span>
            </div>
            <div v-if="reviewData.confident_fields.document_date">
              <span class="text-gray-500">Datum:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.document_date }}</span>
            </div>
            <div v-if="reviewData.confident_fields.amount != null">
              <span class="text-gray-500">Betrag:</span>
              <span class="ml-1 text-gray-700">{{ reviewData.confident_fields.amount }} {{ reviewData.confident_fields.currency }}</span>
            </div>
            <div v-if="reviewData.confident_fields.filing_scope">
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

        <!-- Fragen-Cards -->
        <div class="card">
          <h2 class="text-lg font-semibold text-gray-900 mb-4">Rueckfragen der KI</h2>
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
          <button @click="skipDocument" class="btn-secondary">Ueberspringen</button>
          <div class="flex gap-2">
            <router-link :to="`/dokumente/${currentDoc.id}`" class="btn-secondary">Details</router-link>
            <button
              v-if="openQuestions.length === 0"
              @click="doApprove"
              class="btn-primary"
            >
              Bestaetigen
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
