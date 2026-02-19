<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import { getDocument, updateDocument, deleteDocument, addTag, removeTag, answerReviewQuestion, getFilingScopes } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

const route = useRoute()
const router = useRouter()
const notify = useNotificationStore()

const doc = ref(null)
const loading = ref(true)
const saving = ref(false)
const showDeleteDialog = ref(false)
const newTag = ref('')
const filingScopes = ref([])

// Editable fields
const editForm = ref({})

const documentTypes = [
  'RECHNUNG', 'QUITTUNG', 'KAUFVERTRAG', 'GARANTIESCHEIN',
  'VERSICHERUNGSPOLICE', 'KONTOAUSZUG', 'LOHNABRECHNUNG',
  'STEUERBESCHEID', 'MIETVERTRAG', 'HANDWERKER_RECHNUNG',
  'ARZTRECHNUNG', 'REZEPT', 'AMTLICHES_SCHREIBEN',
  'BEDIENUNGSANLEITUNG', 'SONSTIGES',
]

const taxCategories = [
  { value: 'Werbungskosten', label: 'Werbungskosten' },
  { value: 'Sonderausgaben', label: 'Sonderausgaben' },
  { value: 'Aussergewoehnliche_Belastungen', label: 'Aussergewoehnliche Belastungen' },
  { value: 'Handwerkerleistungen', label: 'Handwerkerleistungen' },
  { value: 'Haushaltsnahe_Dienstleistungen', label: 'Haushaltsnahe Dienstleistungen' },
  { value: 'Vorsorgeaufwendungen', label: 'Vorsorgeaufwendungen' },
  { value: 'Keine', label: 'Keine' },
]

const confidenceColor = computed(() => {
  const c = doc.value?.ai_confidence || 0
  if (c >= 0.8) return 'bg-green-500'
  if (c >= 0.5) return 'bg-yellow-500'
  return 'bg-red-500'
})

const confidencePercent = computed(() => Math.round((doc.value?.ai_confidence || 0) * 100))

const fileUrl = computed(() => doc.value ? `/api/documents/${doc.value.id}/file` : '')
const thumbnailUrl = computed(() => doc.value ? `/api/documents/${doc.value.id}/thumbnail` : '')
const isPdf = computed(() => doc.value?.file_type === 'pdf')

async function loadDocument() {
  loading.value = true
  try {
    doc.value = await getDocument(route.params.id)
    resetForm()
  } catch {
    notify.error('Dokument konnte nicht geladen werden.')
    router.push('/dokumente')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  if (!doc.value) return
  editForm.value = {
    title: doc.value.title,
    document_type: doc.value.document_type,
    document_date: doc.value.document_date,
    amount: doc.value.amount,
    currency: doc.value.currency,
    issuer: doc.value.issuer,
    recipient: doc.value.recipient,
    reference_number: doc.value.reference_number,
    tax_relevant: doc.value.tax_relevant,
    tax_category: doc.value.tax_category,
    tax_year: doc.value.tax_year,
    filing_scope_id: doc.value.filing_scope_id,
  }
}

async function saveChanges() {
  saving.value = true
  try {
    const updates = {}
    for (const [key, value] of Object.entries(editForm.value)) {
      if (value !== doc.value[key]) {
        updates[key] = value
      }
    }
    if (Object.keys(updates).length === 0) {
      notify.info('Keine Aenderungen.')
      return
    }
    doc.value = await updateDocument(doc.value.id, updates)
    resetForm()
    notify.success('Aenderungen gespeichert.')
  } catch {
    notify.error('Speichern fehlgeschlagen.')
  } finally {
    saving.value = false
  }
}

async function handleDelete() {
  try {
    await deleteDocument(doc.value.id)
    notify.success('Dokument geloescht.')
    router.push('/dokumente')
  } catch {
    notify.error('Loeschen fehlgeschlagen.')
  }
  showDeleteDialog.value = false
}

async function handleAddTag() {
  const name = newTag.value.trim()
  if (!name) return
  try {
    doc.value = await addTag(doc.value.id, name)
    newTag.value = ''
  } catch {
    notify.error('Tag konnte nicht hinzugefuegt werden.')
  }
}

async function handleRemoveTag(tagName) {
  try {
    await removeTag(doc.value.id, tagName)
    doc.value.tags = doc.value.tags.filter(t => t.name !== tagName)
  } catch {
    notify.error('Tag konnte nicht entfernt werden.')
  }
}

// Review questions
const reviewAnswers = ref({})

async function submitAnswer(question) {
  const answer = reviewAnswers.value[question.id]
  if (!answer?.trim()) return
  try {
    await answerReviewQuestion(doc.value.id, question.id, answer)
    question.is_answered = true
    question.answer = answer
    notify.success('Antwort gespeichert.')
  } catch {
    notify.error('Antwort konnte nicht gespeichert werden.')
  }
}

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('de-DE')
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

onMounted(async () => {
  try { filingScopes.value = await getFilingScopes() } catch {}
  loadDocument()
})
</script>

<template>
  <div v-if="loading" class="flex items-center justify-center py-20">
    <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
  </div>

  <div v-else-if="doc" class="space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <button @click="router.push('/dokumente')" class="btn-secondary !px-3">
          &larr;
        </button>
        <h1 class="text-xl font-bold text-gray-900 truncate">{{ doc.title }}</h1>
        <DocTypeBadge :type="doc.document_type" />
      </div>
      <div class="flex gap-2">
        <a :href="fileUrl" download class="btn-secondary">Herunterladen</a>
        <button @click="showDeleteDialog = true" class="btn-danger">Loeschen</button>
      </div>
    </div>

    <!-- Two-column layout -->
    <div class="grid grid-cols-1 gap-6 lg:grid-cols-5">
      <!-- Preview (left, 60%) -->
      <div class="lg:col-span-3">
        <div class="card !p-0 overflow-hidden">
          <div v-if="isPdf" class="aspect-[3/4] bg-gray-100">
            <iframe :src="fileUrl" class="h-full w-full" title="PDF-Vorschau"></iframe>
          </div>
          <div v-else class="flex items-center justify-center bg-gray-100 p-4">
            <img :src="fileUrl" :alt="doc.title" class="max-h-[600px] object-contain" />
          </div>
        </div>
        <p class="mt-2 text-xs text-gray-400">
          {{ doc.original_filename }} &middot; {{ formatFileSize(doc.file_size_bytes) }}
        </p>
      </div>

      <!-- Metadata (right, 40%) -->
      <div class="lg:col-span-2 space-y-4">
        <!-- Edit form -->
        <div class="card space-y-3">
          <h2 class="text-sm font-semibold text-gray-900 uppercase tracking-wider">Metadaten</h2>

          <div>
            <label class="block text-xs font-medium text-gray-500 mb-1">Titel</label>
            <input v-model="editForm.title" class="input" />
          </div>

          <div>
            <label class="block text-xs font-medium text-gray-500 mb-1">Dokumenttyp</label>
            <select v-model="editForm.document_type" class="input">
              <option v-for="t in documentTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-medium text-gray-500 mb-1">Datum</label>
              <input v-model="editForm.document_date" type="date" class="input" />
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-500 mb-1">Betrag</label>
              <input v-model.number="editForm.amount" type="number" step="0.01" class="input" />
            </div>
          </div>

          <div>
            <label class="block text-xs font-medium text-gray-500 mb-1">Aussteller</label>
            <input v-model="editForm.issuer" class="input" />
          </div>

          <div>
            <label class="block text-xs font-medium text-gray-500 mb-1">Referenznummer</label>
            <input v-model="editForm.reference_number" class="input" />
          </div>

          <div v-if="filingScopes.length > 1">
            <label class="block text-xs font-medium text-gray-500 mb-1">Ablagebereich</label>
            <select v-model="editForm.filing_scope_id" class="input">
              <option :value="null">Kein Bereich</option>
              <option v-for="s in filingScopes" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </div>

          <div class="flex items-center gap-3 pt-1">
            <input type="checkbox" v-model="editForm.tax_relevant" id="taxToggle" class="rounded border-gray-300" />
            <label for="taxToggle" class="text-sm text-gray-600">Steuerrelevant</label>
          </div>

          <div v-if="editForm.tax_relevant">
            <label class="block text-xs font-medium text-gray-500 mb-1">Steuerkategorie</label>
            <select v-model="editForm.tax_category" class="input">
              <option :value="null">Keine</option>
              <option v-for="c in taxCategories" :key="c.value" :value="c.value">{{ c.label }}</option>
            </select>
          </div>

          <button @click="saveChanges" :disabled="saving" class="btn-primary w-full">
            {{ saving ? 'Wird gespeichert...' : 'Speichern' }}
          </button>
        </div>

        <!-- Tags -->
        <div class="card">
          <h2 class="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-3">Tags</h2>
          <div class="flex flex-wrap gap-2 mb-3">
            <span
              v-for="tag in doc.tags"
              :key="tag.id"
              class="inline-flex items-center gap-1 rounded-full bg-primary-50 px-3 py-1 text-sm text-primary-700"
            >
              {{ tag.name }}
              <button @click="handleRemoveTag(tag.name)" class="ml-1 text-primary-400 hover:text-primary-700">&times;</button>
            </span>
            <span v-if="!doc.tags?.length" class="text-sm text-gray-400">Keine Tags</span>
          </div>
          <div class="flex gap-2">
            <input v-model="newTag" class="input flex-1" placeholder="Neuer Tag..." @keydown.enter="handleAddTag" />
            <button @click="handleAddTag" class="btn-secondary !px-3">+</button>
          </div>
        </div>

        <!-- Confidence -->
        <div class="card">
          <h2 class="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-3">KI-Analyse</h2>
          <div class="mb-2">
            <div class="flex items-center justify-between text-sm">
              <span class="text-gray-500">Konfidenz</span>
              <span class="font-medium">{{ confidencePercent }}%</span>
            </div>
            <div class="mt-1 h-2 w-full rounded-full bg-gray-200">
              <div :class="['h-2 rounded-full transition-all', confidenceColor]" :style="{ width: confidencePercent + '%' }"></div>
            </div>
          </div>
          <div v-if="doc.summary" class="mt-3">
            <p class="text-xs font-medium text-gray-500 mb-1">Zusammenfassung</p>
            <p class="text-sm text-gray-600">{{ doc.summary }}</p>
          </div>
        </div>

        <!-- Warranty -->
        <div v-if="doc.warranty_info" class="card">
          <h2 class="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-3">Garantie</h2>
          <div class="space-y-1 text-sm">
            <p><span class="text-gray-500">Produkt:</span> {{ doc.warranty_info.product_name }}</p>
            <p><span class="text-gray-500">Ablauf:</span> {{ formatDate(doc.warranty_info.warranty_end_date) }}</p>
            <p>
              <span :class="doc.warranty_info.is_expired ? 'badge bg-red-100 text-red-700' : 'badge bg-green-100 text-green-700'">
                {{ doc.warranty_info.is_expired ? 'Abgelaufen' : 'Aktiv' }}
              </span>
            </p>
          </div>
        </div>

        <!-- Review questions -->
        <div v-if="doc.review_questions?.length" class="card">
          <h2 class="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-3">Rueckfragen</h2>
          <div class="space-y-4">
            <div v-for="q in doc.review_questions" :key="q.id">
              <p class="text-sm font-medium text-gray-700">{{ q.question }}</p>
              <div v-if="q.is_answered" class="mt-1 rounded bg-green-50 p-2 text-sm text-green-700">
                {{ q.answer }}
              </div>
              <div v-else class="mt-2 flex gap-2">
                <input
                  v-model="reviewAnswers[q.id]"
                  class="input flex-1"
                  placeholder="Antwort eingeben..."
                  @keydown.enter="submitAnswer(q)"
                />
                <button @click="submitAnswer(q)" class="btn-primary !px-3">Senden</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <ConfirmDialog
      :show="showDeleteDialog"
      title="Dokument loeschen"
      message="Soll dieses Dokument wirklich geloescht werden? Es kann spaeter wiederhergestellt werden."
      confirm-text="Loeschen"
      :danger="true"
      @confirm="handleDelete"
      @cancel="showDeleteDialog = false"
    />
  </div>
</template>
