<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import ProposalDiff from '../components/reanalysis/ProposalDiff.vue'
import ReanalyzeDialog from '../components/reanalysis/ReanalyzeDialog.vue'
import {
  getDocument, updateDocument, deleteDocument, addTag, removeTag, answerReviewQuestion, createFilingScope,
  requestDocumentSplit, getSplitBatch, reanalyzeDocument, getProposal, applyProposal, discardProposal,
} from '../services/api'
import { useFilingScopes } from '../composables/useFilingScopes'
import { useBackgroundTask } from '../composables/useBackgroundTask'
import { useNotificationStore } from '../stores/notifications'
import { documentTypes, typeLabels } from '../constants/documentTypes'
import { formatDate, formatBytes } from '../utils/formatters'
import { apiErrorMessage } from '../utils/apiErrors'
import { sourcePagesLabel } from '../utils/splitParts'
import { UNDO_TITLE, undoMessage, undoSplitBatch } from '../utils/splitUndo'

const route = useRoute()
const router = useRouter()
const notify = useNotificationStore()

const doc = ref(null)
const loading = ref(true)
const saving = ref(false)
const showDeleteDialog = ref(false)
const showSplitDialog = ref(false)
const requestingSplit = ref(false)
const showUndoDialog = ref(false)
const undoingSplit = ref(false)
// Stapel des Teils, beim Oeffnen des Dialogs geladen: der Dialog nennt die
// Folge je nach Herkunft (Original wieder aktiv vs. zurueck in den Editor).
const undoBatch = ref(null)
const newTag = ref('')
// H-FE-5: scopes aus dem shared Composable mit Cache + Invalidation.
const { scopes: filingScopes, ensureLoaded: ensureScopesLoaded, reload: reloadScopes } = useFilingScopes()

// Editable fields
const editForm = ref({})

const taxCategories = [
  { value: 'Werbungskosten', label: 'Werbungskosten' },
  { value: 'Sonderausgaben', label: 'Sonderausgaben' },
  { value: 'Aussergewoehnliche_Belastungen', label: 'Außergewöhnliche Belastungen' },
  { value: 'Handwerkerleistungen', label: 'Handwerkerleistungen' },
  { value: 'Haushaltsnahe_Dienstleistungen', label: 'Haushaltsnahe Dienstleistungen' },
  { value: 'Vorsorgeaufwendungen', label: 'Vorsorgeaufwendungen' },
  { value: 'Keine', label: 'Sonstige steuerrelevante Belege' },
]

// Felder, die leer sein duerfen: ein geleertes Eingabefeld liefert '' (Text,
// Datum) bzw. '' statt Zahl (v-model.number) — das Backend erwartet null.
const NULLABLE_FIELDS = ['document_date', 'amount', 'issuer', 'recipient', 'reference_number', 'tax_category', 'tax_year', 'filing_scope_id']

function normalizeForm(form) {
  const out = { ...form }
  for (const key of NULLABLE_FIELDS) {
    if (typeof out[key] === 'string' && out[key].trim() === '') out[key] = null
  }
  return out
}

// Rueckfrage-Feld -> Formularfeld (review.py setzt bei "filing_scope" die ID).
const QUESTION_FIELD_MAP = { filing_scope: 'filing_scope_id' }

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
// Browser (Chrome/Edge/Firefox) koennen TIFF nicht in <img> darstellen.
const isTiff = computed(() => ['tiff', 'tif'].includes(doc.value?.file_type))
const imageFailed = ref(false)
const thumbnailFailed = ref(false)
const previewUnavailable = computed(() => isTiff.value || imageFailed.value)

const documentYear = computed(() => {
  const d = editForm.value.document_date || doc.value?.document_date
  return d ? String(d).slice(0, 4) : ''
})

const isDirty = computed(() => {
  if (!doc.value) return false
  return Object.entries(normalizeForm(editForm.value)).some(
    ([key, value]) => value !== (doc.value[key] ?? null),
  )
})

// silent: ohne Ladeanzeige (Seite bleibt stehen), z.B. nach Übernahme eines Vorschlags.
async function loadDocument({ silent = false } = {}) {
  if (!silent) {
    loading.value = true
    imageFailed.value = false
    thumbnailFailed.value = false
    proposal.value = null
    reanalysis.reset()
  }
  try {
    doc.value = await getDocument(route.params.id)
    resetForm()
    if (doc.value.has_proposal) {
      await loadProposal()
    } else {
      proposal.value = null
    }
    if (!silent) reanalysis.resume({ kind: 'reanalyze', key: String(doc.value.id) })
  } catch (e) {
    doc.value = null
    notify.error(e.response?.status === 404
      ? 'Dokument nicht gefunden – es wurde möglicherweise gelöscht.'
      : 'Dokument konnte nicht geladen werden.')
    router.replace('/dokumente')
  } finally {
    loading.value = false
  }
}

// --- Neu analysieren (Issue #39) ---
// Die Neu-Analyse ändert das Dokument nicht, sondern legt einen Vorschlag an,
// den der Benutzer feldweise übernimmt oder verwirft.
const proposal = ref(null)
const showReanalyzeDialog = ref(false)
const proposalBusy = ref(false)

const reanalysis = useBackgroundTask({
  onSuccess: (result) => handleReanalysisDone(result),
  onError: (message) => notify.error(message),
})

async function loadProposal() {
  try {
    proposal.value = await getProposal(doc.value.id)
  } catch {
    proposal.value = null  // Vorschlag inzwischen erledigt (404) — Dokument zeigt sich ohne Diff
  }
}

// Modell und Vorschlags-Kennzeichen aus dem frischen Stand übernehmen, ohne
// ungespeicherte Formular-Eingaben zu überschreiben.
async function refreshAnalysisInfo() {
  try {
    const fresh = await getDocument(doc.value.id)
    doc.value.analysis_model = fresh.analysis_model
    doc.value.has_proposal = fresh.has_proposal
  } catch {
    // Anzeige bleibt; die Meldung nennt das Ergebnis
  }
}

async function handleReanalysisDone(result) {
  await refreshAnalysisInfo()
  if (result?.status === 'proposed') {
    await loadProposal()
    return
  }
  proposal.value = null
  const model = doc.value?.analysis_model
  notify.info(model ? `Keine Änderungen — mit ${model} geprüft` : 'Keine Änderungen.')
}

function startReanalysis({ withOcr }) {
  showReanalyzeDialog.value = false
  const id = doc.value.id
  reanalysis.start(() => reanalyzeDocument(id, { withOcr }))
}

// Ungespeicherte Eingaben sperren Übernehmen/Verwerfen: die Übernahme lädt das
// Dokument neu und überschriebe sonst das Formular.
const UNSAVED_PROPOSAL_HINT = 'Bitte erst speichern oder Änderungen verwerfen.'
const proposalBlockedReason = computed(() => (isDirty.value ? UNSAVED_PROPOSAL_HINT : null))

// Nur Modell, Vorschlags-Kennzeichen und Vorschlag neu holen — das Formular bleibt.
async function refreshProposalState() {
  await refreshAnalysisInfo()
  if (doc.value?.has_proposal) {
    await loadProposal()
  } else {
    proposal.value = null
  }
}

async function finishProposal(action, successText, errorText, { reloadDocument = false } = {}) {
  if (proposalBusy.value || isDirty.value) return
  proposalBusy.value = true
  let succeeded = false
  try {
    await action()
    succeeded = true
    notify.success(successText)
  } catch (e) {
    notify.error(apiErrorMessage(e, errorText))
  } finally {
    proposalBusy.value = false
  }
  if (succeeded && reloadDocument) {
    // Übernommene Felder ins Formular holen (es war beim Start nicht geändert).
    await loadDocument({ silent: true })
    return
  }
  // Verworfen oder fehlgeschlagen (z.B. 409): Vorschlagsstand neu holen.
  await refreshProposalState()
}

function handleApplyProposal(fields) {
  return finishProposal(
    () => applyProposal(doc.value.id, fields),
    'Änderungen übernommen.',
    'Änderungen konnten nicht übernommen werden.',
    { reloadDocument: true },
  )
}

function handleDiscardProposal() {
  return finishProposal(
    () => discardProposal(doc.value.id),
    'Vorschlag verworfen.',
    'Vorschlag konnte nicht verworfen werden.',
  )
}

function resetForm() {
  if (!doc.value) return
  editForm.value = {
    title: doc.value.title,
    document_type: doc.value.document_type,
    document_date: doc.value.document_date ?? null,
    amount: doc.value.amount ?? null,
    currency: doc.value.currency,
    issuer: doc.value.issuer ?? null,
    recipient: doc.value.recipient ?? null,
    reference_number: doc.value.reference_number ?? null,
    tax_relevant: doc.value.tax_relevant,
    tax_category: doc.value.tax_category ?? null,
    tax_year: doc.value.tax_year ?? null,
    filing_scope_id: doc.value.filing_scope_id ?? null,
  }
}

async function saveChanges() {
  saving.value = true
  try {
    const updates = {}
    for (const [key, value] of Object.entries(normalizeForm(editForm.value))) {
      if (value !== (doc.value[key] ?? null)) {
        updates[key] = value
      }
    }
    if (Object.keys(updates).length === 0) {
      notify.info('Keine Änderungen.')
      return
    }
    doc.value = await updateDocument(doc.value.id, updates)
    resetForm()
    notify.success('Änderungen gespeichert.')
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Speichern fehlgeschlagen.'))
  } finally {
    saving.value = false
  }
}

// Nach dem Loeschen bzw. bewusstem Verwerfen nicht erneut nachfragen.
let skipLeaveGuard = false

async function handleDelete() {
  showDeleteDialog.value = false
  try {
    await deleteDocument(doc.value.id)
    notify.success('Dokument gelöscht.')
  } catch (e) {
    if (e.response?.status !== 404) {
      notify.error(apiErrorMessage(e, 'Löschen fehlgeschlagen.'))
      return
    }
    // 404: bereits geloescht (z.B. in einem anderen Tab) — Ziel erreicht.
    notify.info('Das Dokument war bereits gelöscht.')
  }
  skipLeaveGuard = true
  router.push('/dokumente')
}

// "Dokument aufteilen" (Spezifikation 12): nur PDFs; ob es mindestens zwei
// Seiten hat, prueft das Backend (422 mit Meldung).
const canSplit = computed(() => isPdf.value && doc.value?.status !== 'DELETED')
const splitDialogMessage = computed(() => {
  const keeps = []
  if (doc.value?.filing_scope_name) keeps.push(`der Ablagebereich „${doc.value.filing_scope_name}“`)
  keeps.push('die manuell vergebenen Tags')
  return `Die Seiten werden zuerst erkannt, danach legst du die Schnitte fest. Jeder Teil übernimmt ${keeps.join(' und ')}; `
    + 'Steuerkategorie, Garantie und Rückfragen werden pro Teil neu analysiert. '
    + 'Das Original wird erst nach der Archivierung aller Teile als gelöscht markiert.'
})
const splitOrigin = computed(() => {
  const d = doc.value
  if (!d?.split_batch_id) return null
  const pages = d.source_pages ? ` (${sourcePagesLabel(d.source_pages)})` : ''
  const count = d.split_part_count ? ` von ${d.split_part_count}` : ''
  const source = d.split_source_filename ? ` aus „${d.split_source_filename}“` : ''
  return `Teil ${d.split_part_index}${count}${source}${pages}`
})

// "Aufteilung rückgängig" am Teildokument: gesperrt (mit Begruendung), sobald
// ein Teil bearbeitet wurde — das Backend liefert can_undo_split + Grund.
// Nur die Sperr-Angaben neu laden — ungespeicherte Eingaben im Formular bleiben.
async function refreshUndoFlags() {
  try {
    const fresh = await getDocument(doc.value.id)
    doc.value.can_undo_split = fresh.can_undo_split
    doc.value.split_undo_blocked_reason = fresh.split_undo_blocked_reason
  } catch {
    // Anzeige bleibt; die Meldung oben nennt den Grund
  }
}

async function openUndoDialog() {
  if (undoingSplit.value || !doc.value?.split_batch_id) return
  undoingSplit.value = true
  try {
    undoBatch.value = await getSplitBatch(doc.value.split_batch_id)
  } catch {
    undoBatch.value = null  // Dialog zeigt dann den allgemeinen Text
  } finally {
    undoingSplit.value = false
  }
  showUndoDialog.value = true
}

async function handleUndoSplit() {
  showUndoDialog.value = false
  if (undoingSplit.value || !doc.value?.split_batch_id) return
  undoingSplit.value = true
  try {
    const target = await undoSplitBatch(doc.value.split_batch_id, undoBatch.value)
    notify.success(target.message)
    skipLeaveGuard = true
    router.push(target.path)
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Die Aufteilung kann nicht rückgängig gemacht werden.'))
    await refreshUndoFlags()
  } finally {
    undoingSplit.value = false
  }
}

async function handleRequestSplit() {
  showSplitDialog.value = false
  if (requestingSplit.value) return
  requestingSplit.value = true
  try {
    const { batch_id: batchId } = await requestDocumentSplit(doc.value.id)
    notify.success('Die Seiten werden erkannt …')
    skipLeaveGuard = true
    router.push(`/aufteilen/${batchId}`)
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Aufteilen ist nicht möglich.'))
  } finally {
    requestingSplit.value = false
  }
}

function goBack() {
  // Mit Verlauf zurueck (stellt Filter/Seite der Liste aus der URL wieder her),
  // bei Direktaufruf (kein Vorgaenger) zur Dokumentliste.
  if (window.history.state?.back) {
    router.back()
  } else {
    router.push('/dokumente')
  }
}

async function handleAddTag() {
  const name = newTag.value.trim()
  if (!name) return
  try {
    // Nur Tags aus der Antwort uebernehmen, doc.value-Felder NICHT komplett
    // ersetzen — sonst werden in Bearbeitung befindliche editForm-Aenderungen
    // mit dem alten Server-Stand verglichen und beim naechsten Save als
    // ungewollte Updates geschickt (M-25).
    const updated = await addTag(doc.value.id, name)
    doc.value.tags = updated.tags
    newTag.value = ''
  } catch {
    notify.error('Tag konnte nicht hinzugefügt werden.')
  }
}

async function handleRemoveTag(tag) {
  try {
    await removeTag(doc.value.id, tag.id)
    doc.value.tags = doc.value.tags.filter(t => t.id !== tag.id)
  } catch {
    notify.error('Tag konnte nicht entfernt werden.')
  }
}

// Quick-add filing scope
const showNewScope = ref(false)
const newScopeName = ref('')
const creatingScopeInline = ref(false)

async function quickAddScope() {
  if (!newScopeName.value.trim()) return
  creatingScopeInline.value = true
  try {
    const created = await createFilingScope({ name: newScopeName.value.trim(), keywords: [] })
    // Fehler beim Nachladen ignorieren: der Bereich existiert, die Liste bleibt bis zum naechsten Laden alt.
    await reloadScopes().catch(() => {})
    editForm.value.filing_scope_id = created.id
    newScopeName.value = ''
    showNewScope.value = false
    notify.success('Ablagebereich erstellt.')
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Erstellen fehlgeschlagen.'))
  } finally {
    creatingScopeInline.value = false
  }
}

// Review questions
const reviewAnswers = ref({})

function suggestionsOf(q) {
  const raw = typeof q.suggested_answers === 'string' ? q.suggested_answers.split('|') : (q.suggested_answers || [])
  return raw.map((s) => s.trim()).filter(Boolean)
}

async function submitAnswer(question) {
  const answer = reviewAnswers.value[question.id]?.trim()
  if (!answer) return
  try {
    await answerReviewQuestion(question.id, answer)
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Antwort konnte nicht gespeichert werden.'))
    return
  }
  question.is_answered = true
  question.answer = answer
  delete reviewAnswers.value[question.id]
  notify.success('Antwort gespeichert.')
  await syncAfterAnswer(question, answer)
}

// Der Server uebernimmt die Antwort ins betroffene Feld. Nur DIESES Feld (und
// die Rueckfragen) aus dem frischen Stand uebernehmen — andere, noch nicht
// gespeicherte Formular-Aenderungen bleiben erhalten.
async function syncAfterAnswer(question, answer) {
  if (question.field_affected === 'filing_scope' && answer.startsWith('NEU:')) {
    // review.py legt den Ablagebereich ggf. neu an -> Auswahlliste auffrischen.
    await reloadScopes().catch(() => {})
  }
  let fresh
  try {
    fresh = await getDocument(doc.value.id)
  } catch {
    return  // Antwort ist gespeichert; Anzeige aktualisiert sich beim naechsten Laden
  }
  doc.value.review_questions = fresh.review_questions
  doc.value.review_status = fresh.review_status
  const field = QUESTION_FIELD_MAP[question.field_affected] || question.field_affected
  if (field && field in fresh) {
    doc.value[field] = fresh[field]
    if (field in editForm.value) editForm.value[field] = fresh[field] ?? null
  }
}

// --- Ungespeicherte Aenderungen ---
const showLeaveDialog = ref(false)
let leaveResolve = null

function confirmLeave() {
  // Eine noch offene Rueckfrage (vom Router bereits abgebrochene Navigation)
  // abschliessen, statt ihr Promise haengen zu lassen.
  leaveResolve?.(false)
  return new Promise((resolve) => {
    leaveResolve = resolve
    showLeaveDialog.value = true
  })
}

function resolveLeave(ok) {
  showLeaveDialog.value = false
  leaveResolve?.(ok)
  leaveResolve = null
}

async function guardUnsaved() {
  if (skipLeaveGuard || !isDirty.value) return true
  return confirmLeave()
}

onBeforeRouteLeave(guardUnsaved)
onBeforeRouteUpdate(guardUnsaved)

function onBeforeUnload(e) {
  if (!isDirty.value) return
  e.preventDefault()
  e.returnValue = ''
}
window.addEventListener('beforeunload', onBeforeUnload)

onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})

watch(() => route.params.id, (id) => {
  if (id) loadDocument()
})

onMounted(async () => {
  await ensureScopesLoaded()
  loadDocument()
})
</script>

<template>
  <div v-if="loading" class="flex items-center justify-center py-20">
    <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
  </div>

  <div v-else-if="doc" class="space-y-4">
    <!-- Header -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <!-- min-w-48: bei wenig Platz bricht die Aktionsgruppe in eine eigene Zeile um,
           statt den Titel auf Breite 0 zu quetschen. -->
      <div class="flex min-w-48 flex-1 items-center gap-3">
        <button @click="goBack" class="btn-secondary !px-3 flex-shrink-0" aria-label="Zurück" title="Zurück">
          &larr;
        </button>
        <h1 class="min-w-0 truncate text-xl font-bold text-gray-900">{{ doc.title }}</h1>
        <DocTypeBadge :type="doc.document_type" class="flex-shrink-0" />
      </div>
      <!-- Darf schrumpfen und umbrechen: drei Aktionen passen auf dem Smartphone
           nicht in eine Zeile (sonst horizontales Scrollen, "Löschen" abgeschnitten). -->
      <div class="flex max-w-full flex-wrap gap-2">
        <button
          v-if="canSplit"
          type="button"
          class="btn-secondary"
          :disabled="requestingSplit"
          @click="showSplitDialog = true"
        >Dokument aufteilen</button>
        <button
          type="button"
          class="btn-secondary"
          :disabled="reanalysis.running.value"
          @click="showReanalyzeDialog = true"
        >{{ reanalysis.running.value ? 'Analysiere …' : 'Neu analysieren' }}</button>
        <a :href="fileUrl" download class="btn-secondary">Herunterladen</a>
        <button @click="showDeleteDialog = true" class="btn-danger">Löschen</button>
      </div>
    </div>

    <!-- Two-column layout -->
    <div class="grid grid-cols-1 gap-6 lg:grid-cols-5">
      <!-- Preview (left, 60%) -->
      <div class="lg:col-span-3">
        <div class="card !p-0 overflow-hidden">
          <div v-if="isPdf && fileUrl" class="aspect-[3/4] bg-gray-100">
            <iframe :src="fileUrl" class="h-full w-full" title="PDF-Vorschau"></iframe>
          </div>
          <div v-else-if="previewUnavailable" class="flex flex-col items-center justify-center gap-3 bg-gray-100 p-6 text-center" data-testid="preview-unavailable">
            <img
              v-if="!thumbnailFailed"
              :src="thumbnailUrl"
              :alt="`Miniaturansicht von ${doc.title}`"
              class="max-h-[300px] object-contain"
              @error="thumbnailFailed = true"
            />
            <p class="text-sm text-gray-500">Vorschau im Browser nicht verfügbar.</p>
            <a :href="fileUrl" download class="btn-secondary">Herunterladen</a>
          </div>
          <div v-else class="flex items-center justify-center bg-gray-100 p-4">
            <img :src="fileUrl" :alt="doc.title" class="max-h-[600px] object-contain" @error="imageFailed = true" />
          </div>
        </div>
        <p class="mt-2 text-xs text-gray-400">
          {{ doc.original_filename }} &middot; {{ formatBytes(doc.file_size_bytes) }}
        </p>
        <div v-if="splitOrigin" class="mt-1 text-sm text-gray-600">
          <p data-testid="split-origin">
            {{ splitOrigin }} &middot;
            <router-link :to="`/aufteilen/${doc.split_batch_id}`" class="text-primary-600 hover:text-primary-700">Zum Stapel</router-link>
          </p>
          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
            <button
              type="button"
              class="btn-secondary"
              :disabled="!doc.can_undo_split || undoingSplit"
              :aria-describedby="!doc.can_undo_split && doc.split_undo_blocked_reason ? 'split-undo-reason' : undefined"
              data-testid="split-undo"
              @click="openUndoDialog"
            >Aufteilung rückgängig</button>
            <span
              v-if="!doc.can_undo_split && doc.split_undo_blocked_reason"
              id="split-undo-reason"
              class="text-xs text-gray-500"
              data-testid="split-undo-reason"
            >{{ doc.split_undo_blocked_reason }}</span>
          </div>
        </div>
      </div>

      <!-- Metadata (right, 40%) -->
      <div class="lg:col-span-2 space-y-4">
        <p v-if="reanalysis.running.value" class="rounded-md bg-blue-50 border border-blue-200 px-3 py-2 text-sm text-blue-800" role="status">
          {{ reanalysis.progressText.value || 'Analyse läuft im Hintergrund …' }}
        </p>

        <ProposalDiff
          v-if="proposal"
          :proposal="proposal"
          :busy="proposalBusy"
          :blocked-reason="proposalBlockedReason"
          @apply="handleApplyProposal"
          @discard="handleDiscardProposal"
        />

        <!-- Edit form -->
        <div class="card space-y-3">
          <h2 class="text-sm font-semibold text-gray-900 uppercase tracking-wider">Metadaten</h2>

          <div>
            <label for="docTitle" class="block text-xs font-medium text-gray-500 mb-1">Titel</label>
            <input id="docTitle" v-model="editForm.title" class="input" />
          </div>

          <div>
            <label for="docType" class="block text-xs font-medium text-gray-500 mb-1">Dokumenttyp</label>
            <select id="docType" v-model="editForm.document_type" class="input">
              <option v-for="t in documentTypes" :key="t" :value="t">{{ typeLabels[t] || t }}</option>
            </select>
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <label for="docDate" class="block text-xs font-medium text-gray-500 mb-1">Datum</label>
              <input id="docDate" v-model="editForm.document_date" type="date" class="input" />
            </div>
            <div>
              <label for="docAmount" class="block text-xs font-medium text-gray-500 mb-1">Betrag</label>
              <input id="docAmount" v-model.number="editForm.amount" type="number" step="0.01" class="input" />
            </div>
          </div>

          <div>
            <label for="docIssuer" class="block text-xs font-medium text-gray-500 mb-1">Aussteller</label>
            <input id="docIssuer" v-model="editForm.issuer" class="input" />
          </div>

          <div>
            <label for="docReference" class="block text-xs font-medium text-gray-500 mb-1">Referenznummer</label>
            <input id="docReference" v-model="editForm.reference_number" class="input" />
          </div>

          <div>
            <label for="docScope" class="block text-xs font-medium text-gray-500 mb-1">Ablagebereich</label>
            <div class="flex gap-2">
              <select id="docScope" v-model="editForm.filing_scope_id" class="input flex-1">
                <option :value="null">Kein Bereich</option>
                <option v-for="s in filingScopes" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>
              <button
                @click="showNewScope = !showNewScope"
                class="btn-secondary !px-2"
                title="Neuen Bereich anlegen"
                aria-label="Neuen Bereich anlegen"
                :aria-expanded="showNewScope"
              >+</button>
            </div>
            <div v-if="showNewScope" class="mt-2 flex gap-2">
              <input
                v-model="newScopeName"
                class="input flex-1 text-sm"
                placeholder="Neuer Bereich..."
                aria-label="Name des neuen Ablagebereichs"
                @keydown.enter="quickAddScope"
              />
              <button @click="quickAddScope" :disabled="creatingScopeInline" class="btn-primary text-xs">Anlegen</button>
            </div>
          </div>

          <div class="flex items-center gap-3 pt-1">
            <input type="checkbox" v-model="editForm.tax_relevant" id="taxToggle" class="rounded border-gray-300" />
            <label for="taxToggle" class="text-sm text-gray-600">Steuerrelevant</label>
          </div>

          <div v-if="editForm.tax_relevant" class="grid grid-cols-3 gap-3">
            <div class="col-span-2">
              <label for="docTaxCategory" class="block text-xs font-medium text-gray-500 mb-1">Steuerkategorie</label>
              <select id="docTaxCategory" v-model="editForm.tax_category" class="input">
                <option :value="null">Nicht zugeordnet</option>
                <option v-for="c in taxCategories" :key="c.value" :value="c.value">{{ c.label }}</option>
              </select>
            </div>
            <div>
              <label for="docTaxYear" class="block text-xs font-medium text-gray-500 mb-1">Steuerjahr</label>
              <input
                id="docTaxYear"
                v-model.number="editForm.tax_year"
                type="number"
                min="1990"
                max="2100"
                step="1"
                class="input"
                :placeholder="documentYear"
              />
            </div>
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
              <button
                @click="handleRemoveTag(tag)"
                class="ml-1 text-primary-400 hover:text-primary-700"
                :aria-label="`Tag „${tag.name}“ entfernen`"
              >&times;</button>
            </span>
            <span v-if="!doc.tags?.length" class="text-sm text-gray-400">Keine Tags</span>
          </div>
          <div class="flex gap-2">
            <input v-model="newTag" class="input flex-1" placeholder="Neuer Tag..." aria-label="Neuer Tag" @keydown.enter="handleAddTag" />
            <button @click="handleAddTag" class="btn-secondary !px-3" aria-label="Tag hinzufügen" title="Tag hinzufügen">+</button>
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
          <p class="mt-2 text-xs text-gray-500">Modell: {{ doc.analysis_model || 'unbekannt' }}</p>
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
          <h2 class="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-3">Rückfragen</h2>
          <div class="space-y-4">
            <div v-for="q in doc.review_questions" :key="q.id">
              <p :id="`question-${q.id}`" class="text-sm font-medium text-gray-700">{{ q.question }}</p>
              <div v-if="q.is_answered" class="mt-1 rounded bg-green-50 p-2 text-sm text-green-700">
                {{ q.answer }}
              </div>
              <div
                v-if="!q.is_answered && suggestionsOf(q).length"
                class="mt-2 flex flex-wrap gap-2"
                data-testid="question-suggestions"
              >
                <button
                  v-for="suggestion in suggestionsOf(q)"
                  :key="suggestion"
                  type="button"
                  class="badge cursor-pointer transition-colors"
                  :class="suggestion.startsWith('NEU: ')
                    ? 'bg-green-50 text-green-700 hover:bg-green-100 border border-dashed border-green-300'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'"
                  @click="reviewAnswers[q.id] = suggestion; submitAnswer(q)"
                >
                  <template v-if="suggestion.startsWith('NEU: ')">{{ suggestion.substring(5) }} (neu erstellen)</template>
                  <template v-else>{{ suggestion }}</template>
                </button>
              </div>
              <div v-if="!q.is_answered" class="mt-2 flex gap-2">
                <input
                  v-model="reviewAnswers[q.id]"
                  class="input flex-1"
                  placeholder="Antwort eingeben..."
                  :aria-labelledby="`question-${q.id}`"
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
      title="Dokument löschen"
      message="Soll dieses Dokument wirklich gelöscht werden? Das Dokument wird aus dem Archiv, der Suche und dem Assistenten entfernt."
      confirm-text="Löschen"
      :danger="true"
      @confirm="handleDelete"
      @cancel="showDeleteDialog = false"
    />

    <ReanalyzeDialog
      :open="showReanalyzeDialog"
      @confirm="startReanalysis"
      @cancel="showReanalyzeDialog = false"
    />

    <ConfirmDialog
      :show="showSplitDialog"
      title="Dokument aufteilen"
      :message="splitDialogMessage"
      confirm-text="Aufteilen"
      @confirm="handleRequestSplit"
      @cancel="showSplitDialog = false"
    />

    <ConfirmDialog
      :show="showUndoDialog"
      :title="UNDO_TITLE"
      :message="undoMessage(undoBatch?.origin)"
      confirm-text="Rückgängig machen"
      :danger="true"
      @confirm="handleUndoSplit"
      @cancel="showUndoDialog = false"
    />

    <ConfirmDialog
      :show="showLeaveDialog"
      title="Ungespeicherte Änderungen"
      message="Die Änderungen an diesem Dokument wurden noch nicht gespeichert. Seite trotzdem verlassen?"
      confirm-text="Verwerfen und verlassen"
      cancel-text="Weiter bearbeiten"
      :danger="true"
      @confirm="resolveLeave(true)"
      @cancel="resolveLeave(false)"
    />
  </div>
</template>
