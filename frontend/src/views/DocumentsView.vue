<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import Pagination from '../components/common/Pagination.vue'
import ReanalyzeDialog from '../components/reanalysis/ReanalyzeDialog.vue'
import ReanalysisRunStatus from '../components/reanalysis/ReanalysisRunStatus.vue'
import { getDocuments, updateDocument, previewReanalysisRun, startReanalysisRun } from '../services/api'
import { apiErrorMessage } from '../utils/apiErrors'
import { useNotificationStore } from '../stores/notifications'
import { useFilingScopes } from '../composables/useFilingScopes'
import { documentTypes, typeLabels } from '../constants/documentTypes'
import { formatDate, formatAmount } from '../utils/formatters'

const route = useRoute()
const router = useRouter()
const notify = useNotificationStore()

const documents = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(25)
const loading = ref(false)

// Filters
const filterType = ref('')
const filterDateFrom = ref('')
const filterDateTo = ref('')
const filterTaxRelevant = ref(null)
const filterScope = ref('')
const sortBy = ref('created_at')
const sortOrder = ref('desc')
// H-FE-5: scopes aus Composable
const { scopes: filingScopes, ensureLoaded: ensureScopesLoaded } = useFilingScopes()

// Mobile (<768px): gestapelte Karten statt der 8-spaltigen Tabelle, die in
// der Karte abgeschnitten wurde. Per matchMedia statt CSS-only, damit nicht
// beide Varianten (eine davon unsichtbar) gleichzeitig im DOM stehen.
const desktopQuery = window.matchMedia('(min-width: 768px)')
const isDesktop = ref(desktopQuery.matches)
function onMediaChange(e) { isDesktop.value = e.matches }
desktopQuery.addEventListener('change', onMediaChange)
onUnmounted(() => desktopQuery.removeEventListener('change', onMediaChange))

// --- Filter/Sortierung/Seite <-> URL-Query ---
// Der Listenzustand lebt in route.query, damit "Zurück" aus der Detailansicht
// (und Reload/Lesezeichen) Filter, Sortierung und Seite wiederherstellt.
const DEFAULT_SORT_BY = 'created_at'
const DEFAULT_SORT_ORDER = 'desc'

function stateToQuery() {
  const q = {}
  if (filterType.value) q.typ = filterType.value
  if (filterDateFrom.value) q.von = filterDateFrom.value
  if (filterDateTo.value) q.bis = filterDateTo.value
  if (filterTaxRelevant.value === true) q.steuer = '1'
  if (filterScope.value) q.bereich = filterScope.value
  if (sortBy.value !== DEFAULT_SORT_BY) q.sort = sortBy.value
  if (sortOrder.value !== DEFAULT_SORT_ORDER) q.richtung = sortOrder.value
  if (page.value > 1) q.seite = String(page.value)
  return q
}

function queryString(q) {
  return JSON.stringify(Object.keys(q).sort().map(k => [k, String(q[k])]))
}

const filterKey = computed(() => JSON.stringify([
  filterType.value, filterDateFrom.value, filterDateTo.value, filterTaxRelevant.value,
  filterScope.value, sortBy.value, sortOrder.value,
]))

// true, wenn applyQuery Filter geaendert hat: der Filter-Watcher setzt dann
// die Seite NICHT auf 1 zurueck (die Seite kommt ja aus der URL).
let filtersFromQuery = false

function applyQuery(q) {
  const before = filterKey.value
  filterType.value = typeof q.typ === 'string' ? q.typ : ''
  filterDateFrom.value = typeof q.von === 'string' ? q.von : ''
  filterDateTo.value = typeof q.bis === 'string' ? q.bis : ''
  filterTaxRelevant.value = q.steuer === '1' ? true : null
  filterScope.value = typeof q.bereich === 'string' ? q.bereich : ''
  sortBy.value = typeof q.sort === 'string' ? q.sort : DEFAULT_SORT_BY
  sortOrder.value = q.richtung === 'asc' ? 'asc' : DEFAULT_SORT_ORDER
  const p = parseInt(q.seite, 10)
  page.value = Number.isFinite(p) && p > 0 ? p : 1
  filtersFromQuery = filterKey.value !== before
}

// Initialzustand aus der URL — VOR dem Registrieren der Watcher, damit das
// keinen zusaetzlichen Ladevorgang ausloest (onMounted laedt einmal).
applyQuery(route.query)
filtersFromQuery = false

// Request-Token: nur die Antwort des zuletzt gestarteten Requests darf die
// Liste setzen (schnelles Filtern/Blaettern ueberholt langsame Antworten).
let loadRequestId = 0

async function loadDocuments() {
  const requestId = ++loadRequestId
  loading.value = true
  try {
    const params = {
      page: page.value,
      page_size: pageSize.value,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    }
    if (filterType.value) params.document_type = filterType.value
    if (filterDateFrom.value) params.date_from = filterDateFrom.value
    if (filterDateTo.value) params.date_to = filterDateTo.value
    if (filterTaxRelevant.value !== null) params.tax_relevant = filterTaxRelevant.value
    if (filterScope.value) params.filing_scope_id = filterScope.value

    const data = await getDocuments(params)
    if (requestId !== loadRequestId) return  // veraltet
    documents.value = data.items
    total.value = data.total
  } catch {
    if (requestId !== loadRequestId) return
    notify.error('Dokumente konnten nicht geladen werden.')
  } finally {
    if (requestId === loadRequestId) loading.value = false
  }
}

function toggleSort(column) {
  if (sortBy.value === column) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = column
    sortOrder.value = 'desc'
  }
}

function clearFilters() {
  filterType.value = ''
  filterDateFrom.value = ''
  filterDateTo.value = ''
  filterTaxRelevant.value = null
  filterScope.value = ''
  page.value = 1
}

async function toggleTax(doc) {
  const prev = doc.tax_relevant
  doc.tax_relevant = !prev
  try {
    await updateDocument(doc.id, { tax_relevant: doc.tax_relevant })
  } catch {
    doc.tax_relevant = prev
    notify.error('Steuerrelevanz konnte nicht geändert werden.')
  }
}

// --- Auswahl + Massenlauf "Neu analysieren" (Issue #39) ---
// Die Auswahl bleibt beim Blättern erhalten und wird bei Filterwechsel geleert.
const selectedIds = ref(new Set())
const selectedCount = computed(() => selectedIds.value.size)
const allOnPageSelected = computed(
  () => documents.value.length > 0 && documents.value.every(d => selectedIds.value.has(d.id)),
)

function toggleSelected(id) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function toggleAllOnPage() {
  const next = new Set(selectedIds.value)
  if (allOnPageSelected.value) documents.value.forEach(d => next.delete(d.id))
  else documents.value.forEach(d => next.add(d.id))
  selectedIds.value = next
}

function clearSelection() {
  selectedIds.value = new Set()
}

const runStatus = ref(null)
const dialogMode = ref(null)  // 'selection' | 'outdated' | null
const dialogPreview = ref(null)
const dialogLoading = ref(false)
const dialogStarting = ref(false)
const dialogWithOcr = ref(false)
let previewRequestId = 0

const dialogTitle = computed(() => {
  if (dialogMode.value !== 'outdated') return 'Ausgewählte Dokumente neu analysieren'
  // Das Modell nennt erst die Vorschau (Backend: OLLAMA_MODEL).
  const model = dialogPreview.value?.model
  return model ? `Alle, die nicht mit ${model} analysiert wurden` : 'Mit aktuellem Modell neu analysieren'
})

function runBody() {
  return {
    document_ids: dialogMode.value === 'selection' ? [...selectedIds.value] : null,
    outdated: dialogMode.value === 'outdated',
    with_ocr: dialogWithOcr.value,
  }
}

async function loadPreview() {
  const requestId = ++previewRequestId
  dialogLoading.value = true
  try {
    const data = await previewReanalysisRun(runBody())
    if (requestId === previewRequestId) dialogPreview.value = data
  } catch (e) {
    if (requestId !== previewRequestId) return
    notify.error(apiErrorMessage(e, 'Die Vorschau konnte nicht geladen werden.'))
    closeDialog()
  } finally {
    if (requestId === previewRequestId) dialogLoading.value = false
  }
}

function openDialog(mode) {
  dialogMode.value = mode
  dialogPreview.value = null
  dialogWithOcr.value = false
  loadPreview()
}

function closeDialog() {
  previewRequestId++  // laufende Vorschau verwerfen
  dialogMode.value = null
  dialogPreview.value = null
  dialogLoading.value = false
}

function onDialogOcrChange(value) {
  dialogWithOcr.value = value
  loadPreview()
}

async function onDialogConfirm({ withOcr }) {
  if (dialogStarting.value) return
  dialogWithOcr.value = withOcr
  dialogStarting.value = true
  try {
    const run = await startReanalysisRun(runBody())
    const fromSelection = dialogMode.value === 'selection'
    closeDialog()
    if (fromSelection) clearSelection()
    runStatus.value?.setRun(run)
    notify.success('Neu-Analyse gestartet.')
  } catch (e) {
    const active = e?.response?.status === 409 ? e.response.data?.run : null
    if (active) {
      runStatus.value?.setRun(active)
      closeDialog()
    }
    notify.error(apiErrorMessage(e, 'Die Neu-Analyse konnte nicht gestartet werden.'))
  } finally {
    dialogStarting.value = false
  }
}

watch(filterKey, clearSelection)

const mobileSortOptions = [
  { value: 'created_at:desc', label: 'Neueste zuerst' },
  { value: 'created_at:asc', label: 'Älteste zuerst' },
  { value: 'document_date:desc', label: 'Datum (neueste)' },
  { value: 'document_date:asc', label: 'Datum (älteste)' },
  { value: 'title:asc', label: 'Titel A–Z' },
  { value: 'title:desc', label: 'Titel Z–A' },
  { value: 'document_type:asc', label: 'Typ A–Z' },
  { value: 'document_type:desc', label: 'Typ Z–A' },
  { value: 'amount:desc', label: 'Betrag (höchster)' },
  { value: 'amount:asc', label: 'Betrag (niedrigster)' },
]

function applySortOption(value) {
  const [column, order] = value.split(':')
  sortBy.value = column
  sortOrder.value = order
}

function sortIcon(column) {
  if (sortBy.value !== column) return ''
  return sortOrder.value === 'asc' ? ' ↑' : ' ↓'
}

function ariaSort(column) {
  if (sortBy.value !== column) return 'none'
  return sortOrder.value === 'asc' ? 'ascending' : 'descending'
}

// H-FE-6: Ein Filter-Change setzt die Seite auf 1; der zweite Watcher laedt
// genau einmal pro Tick (Vue dedupliziert den Job, auch wenn Filter UND Seite
// sich im selben Tick aendern) und schreibt den Zustand in die URL.
watch(filterKey, () => {
  if (filtersFromQuery) {
    filtersFromQuery = false
    return
  }
  page.value = 1
})

watch([filterKey, page], () => {
  syncUrl()
  loadDocuments()
})

// Eigene router.replace-Aufrufe (noch nicht abgeschlossen) werden hier
// vermerkt: bei schnellem Umschalten loest eine aeltere Navigation sonst nach
// der neueren aus und wuerde den Filter auf den alten Wert zuruecksetzen.
const ownQueryNavigations = new Set()
let lastQueryKey = queryString(route.query)

function syncUrl() {
  const q = stateToQuery()
  const key = queryString(q)
  if (key === lastQueryKey) return
  lastQueryKey = key
  ownQueryNavigations.add(key)
  router.replace({ query: q }).finally(() => ownQueryNavigations.delete(key))
}

// Externe Query-Aenderung (z.B. Klick auf "Dokumente" in der Navigation)
// -> Zustand uebernehmen. Laeuft synchron in der Navigations-Pipeline, also
// ohne Reihenfolge-Probleme mit den eigenen replace-Aufrufen.
onBeforeRouteUpdate((to) => {
  const key = queryString(to.query)
  if (ownQueryNavigations.has(key)) return
  lastQueryKey = key
  if (key !== queryString(stateToQuery())) applyQuery(to.query)
})

// Liste und Ablagebereiche parallel laden — die Liste braucht die Bereiche nicht.
onMounted(() => {
  loadDocuments()
  ensureScopesLoaded()
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-bold text-gray-900">Dokumente</h1>
      <div class="flex flex-wrap items-center justify-end gap-2">
        <button type="button" class="btn-secondary" @click="openDialog('outdated')">Mit aktuellem Modell neu analysieren …</button>
        <router-link to="/upload" class="btn-primary">Hochladen</router-link>
      </div>
    </div>

    <ReanalysisRunStatus ref="runStatus" />

    <div
      v-if="selectedCount > 0"
      class="card !p-3 flex flex-wrap items-center gap-3 text-sm"
      data-testid="selection-bar"
    >
      <span class="font-medium text-gray-900">{{ selectedCount }} ausgewählt</span>
      <span aria-hidden="true" class="text-gray-300">·</span>
      <button type="button" class="btn-primary !py-1.5 text-xs" @click="openDialog('selection')">Neu analysieren</button>
      <span aria-hidden="true" class="text-gray-300">·</span>
      <button type="button" class="btn-secondary !py-1.5 text-xs" @click="clearSelection">Auswahl aufheben</button>
    </div>

    <!-- Filters -->
    <div class="card !p-4">
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label for="filterType" class="block text-xs font-medium text-gray-500 mb-1">Typ</label>
          <select id="filterType" v-model="filterType" class="input !py-1.5">
            <option value="">Alle Typen</option>
            <option v-for="t in documentTypes" :key="t" :value="t">{{ typeLabels[t] || t }}</option>
          </select>
        </div>
        <div>
          <label for="filterDateFrom" class="block text-xs font-medium text-gray-500 mb-1">Von</label>
          <input id="filterDateFrom" v-model="filterDateFrom" type="date" class="input !py-1.5" />
        </div>
        <div>
          <label for="filterDateTo" class="block text-xs font-medium text-gray-500 mb-1">Bis</label>
          <input id="filterDateTo" v-model="filterDateTo" type="date" class="input !py-1.5" />
        </div>
        <div v-if="filingScopes.length > 1">
          <label for="filterScope" class="block text-xs font-medium text-gray-500 mb-1">Bereich</label>
          <select id="filterScope" v-model="filterScope" class="input !py-1.5">
            <option value="">Alle Bereiche</option>
            <option v-for="s in filingScopes" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <input
            type="checkbox"
            id="taxFilter"
            :checked="filterTaxRelevant === true"
            @change="filterTaxRelevant = $event.target.checked ? true : null"
            class="rounded border-gray-300"
          />
          <label for="taxFilter" class="text-sm text-gray-600">Steuerrelevant</label>
        </div>
        <!-- Mobile hat keine sortierbaren Tabellenkoepfe -> eigene Auswahl. -->
        <div v-if="!isDesktop">
          <label for="mobileSort" class="block text-xs font-medium text-gray-500 mb-1">Sortierung</label>
          <select id="mobileSort" :value="`${sortBy}:${sortOrder}`" @change="applySortOption($event.target.value)" class="input !py-1.5">
            <option v-for="o in mobileSortOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
        <button @click="clearFilters" class="btn-secondary !py-1.5 text-xs">Zurücksetzen</button>
      </div>
    </div>

    <div v-if="loading" class="card flex items-center justify-center py-12">
      <div class="h-8 w-8 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
    </div>

    <!-- Mobile: gestapelte Karten -->
    <div v-else-if="!isDesktop" class="space-y-2" data-testid="documents-cards">
      <div v-if="documents.length === 0" class="card py-12 text-center text-sm text-gray-400">
        Keine Dokumente gefunden.
      </div>
      <div
        v-for="doc in documents"
        :key="doc.id"
        class="card !p-3 flex items-start gap-3"
        data-testid="document-card"
      >
        <router-link
          :to="`/dokumente/${doc.id}`"
          class="min-w-0 flex-1 no-underline text-inherit focus:outline-none focus:ring-2 focus:ring-primary-500 rounded"
          :aria-label="`Dokument ${doc.title || doc.original_filename} öffnen`"
        >
          <p class="text-sm font-medium text-gray-900 truncate">{{ doc.title }}</p>
          <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
            <DocTypeBadge :type="doc.document_type" />
            <span>{{ formatDate(doc.document_date) }}</span>
            <span v-if="doc.amount != null">{{ formatAmount(doc.amount, doc.currency) }}</span>
          </div>
        </router-link>
        <label class="flex flex-shrink-0 flex-col items-center gap-1 text-[10px] text-gray-500">
          <input
            type="checkbox"
            :checked="selectedIds.has(doc.id)"
            @change="toggleSelected(doc.id)"
            class="rounded border-gray-300"
            :aria-label="`${doc.title || doc.original_filename} auswählen`"
          />
          Auswahl
        </label>
        <label class="flex flex-shrink-0 flex-col items-center gap-1 text-[10px] text-gray-500">
          <input
            type="checkbox"
            :checked="doc.tax_relevant"
            @change="toggleTax(doc)"
            class="rounded border-gray-300"
            :aria-label="`${doc.title || doc.original_filename} steuerrelevant`"
          />
          Steuer
        </label>
      </div>
    </div>

    <!-- Desktop/Tablet: Tabelle (Nebenspalten erst ab lg, sonst horizontal scrollbar) -->
    <div v-else class="card !p-0 overflow-x-auto">
      <table class="w-full">
        <thead class="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
          <tr>
            <th class="px-4 py-3 w-10">
              <input
                type="checkbox"
                class="rounded border-gray-300"
                :checked="allOnPageSelected"
                :disabled="documents.length === 0"
                aria-label="Alle auf dieser Seite auswählen"
                @change="toggleAllOnPage"
              />
            </th>
            <th class="px-4 py-3 w-10"><span class="sr-only">Dateityp</span></th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" :aria-sort="ariaSort('title')" @click="toggleSort('title')">
              Titel{{ sortIcon('title') }}
            </th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" :aria-sort="ariaSort('document_type')" @click="toggleSort('document_type')">
              Typ{{ sortIcon('document_type') }}
            </th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" :aria-sort="ariaSort('document_date')" @click="toggleSort('document_date')">
              Datum{{ sortIcon('document_date') }}
            </th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" :aria-sort="ariaSort('amount')" @click="toggleSort('amount')">
              Betrag{{ sortIcon('amount') }}
            </th>
            <th class="px-4 py-3">Steuer</th>
            <th class="hidden px-4 py-3 lg:table-cell">Aussteller</th>
            <th class="hidden px-4 py-3 lg:table-cell">Tags</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="documents.length === 0">
            <td colspan="9" class="px-4 py-12 text-center text-sm text-gray-400">
              Keine Dokumente gefunden.
            </td>
          </tr>
          <!-- Re-Review #3: Tabellen-Semantik bleibt erhalten (kein role="link" auf
               <tr>). Klick auf die Zeile navigiert weiter; Tab-Reihenfolge fokussiert
               den echten <router-link> in der Titel-Spalte fuer Screen-Reader und
               middle-click/Strg+Klick im neuen Tab. Die Checkbox bleibt eigener Stop. -->
          <tr
            v-for="doc in documents"
            :key="doc.id"
            class="cursor-pointer hover:bg-gray-50 transition-colors"
            @click="router.push(`/dokumente/${doc.id}`)"
          >
            <td class="px-4 py-3">
              <input
                type="checkbox"
                class="rounded border-gray-300"
                :checked="selectedIds.has(doc.id)"
                :aria-label="`${doc.title || doc.original_filename} auswählen`"
                @click.stop
                @change="toggleSelected(doc.id)"
              />
            </td>
            <td class="px-4 py-3">
              <div class="flex h-8 w-8 items-center justify-center rounded bg-gray-100 text-[10px] font-medium text-gray-500">
                {{ doc.file_type?.toUpperCase() }}
              </div>
            </td>
            <td class="px-4 py-3">
              <router-link
                :to="`/dokumente/${doc.id}`"
                class="block no-underline text-inherit focus:outline-none focus:ring-2 focus:ring-primary-500 rounded"
                :aria-label="`Dokument ${doc.title || doc.original_filename} öffnen`"
                @click.stop
              >
                <p class="text-sm font-medium text-gray-900 truncate max-w-xs">{{ doc.title }}</p>
                <p class="text-xs text-gray-400">{{ doc.original_filename }}</p>
              </router-link>
            </td>
            <td class="px-4 py-3"><DocTypeBadge :type="doc.document_type" /></td>
            <td class="px-4 py-3 text-sm text-gray-600 whitespace-nowrap">{{ formatDate(doc.document_date) }}</td>
            <td class="px-4 py-3 text-sm text-gray-600 whitespace-nowrap">{{ formatAmount(doc.amount, doc.currency) }}</td>
            <td class="px-4 py-3 text-center">
              <input
                type="checkbox"
                :checked="doc.tax_relevant"
                @click.stop
                @change="toggleTax(doc)"
                class="rounded border-gray-300"
                :aria-label="`${doc.title || doc.original_filename} steuerrelevant`"
              />
            </td>
            <td class="hidden px-4 py-3 text-sm text-gray-600 truncate max-w-[120px] lg:table-cell">{{ doc.issuer || '-' }}</td>
            <td class="hidden px-4 py-3 lg:table-cell">
              <div class="flex flex-wrap gap-1">
                <span v-for="tag in (doc.tags || []).slice(0, 3)" :key="tag.name || tag" class="badge bg-gray-100 text-gray-600">
                  {{ tag.name || tag }}
                </span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <ReanalyzeDialog
      :open="dialogMode !== null"
      :title="dialogTitle"
      :preview="dialogPreview"
      :busy="dialogLoading || dialogStarting"
      @confirm="onDialogConfirm"
      @cancel="closeDialog"
      @ocr-change="onDialogOcrChange"
    />

    <Pagination :total="total" :page="page" :page-size="pageSize" @update:page="page = $event" />
  </div>
</template>
