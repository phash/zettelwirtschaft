<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import Pagination from '../components/common/Pagination.vue'
import { searchDocuments, createSavedSearch, getSavedSearches, deleteSavedSearch } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { useFilingScopes } from '../composables/useFilingScopes'
import { typeLabels } from '../constants/documentTypes'
import { formatDate, formatAmount } from '../utils/formatters'
import { highlightSegments } from '../utils/highlight'

const route = useRoute()
const notify = useNotificationStore()

const results = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(25)
const facets = ref({ document_types: {}, years: {}, top_issuers: {}, filing_scopes: [] })
// H-FE-5: shared Composable.
const { scopes: filingScopes, ensureLoaded: ensureScopesLoaded } = useFilingScopes()
const loading = ref(false)
const savedSearches = ref([])

// Search params
const query = ref('')
const filterType = ref([])
const filterDateFrom = ref('')
const filterDateTo = ref('')
const filterAmountMin = ref(null)
const filterAmountMax = ref(null)
const filterTaxRelevant = ref(null)
const filterScope = ref('')
const filterIssuer = ref('')
const sortBy = ref('relevance')
const sortOrder = ref('desc')
const showAdvanced = ref(false)

// Save search
const saveSearchName = ref('')
const showSaveDialog = ref(false)

const SORT_OPTIONS = [
  { value: 'relevance', label: 'Relevanz' },
  { value: 'date', label: 'Datum' },
  { value: 'amount', label: 'Betrag' },
  { value: 'title', label: 'Titel' },
  { value: 'created_at', label: 'Erstellt' },
]
// Sinnvolle Startrichtung je Sortierschluessel: Titel A→Z, sonst neueste/
// groesste zuerst. Relevanz hat keine Richtung (FTS-Rang).
const DEFAULT_SORT_ORDER = { title: 'asc' }

// v-model.number liefert bei einem geleerten Feld '' statt null — nur echte
// Zahlen duerfen in die Query, sonst antwortet das Backend mit 422.
function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v)
}

// Einzige Quelle fuer die Filter-Parameter: genutzt von Suche UND
// "Suche speichern", damit gespeicherte Suchen alle Kriterien enthalten.
function buildFilterParams() {
  const params = { sort_by: sortBy.value, sort_order: sortOrder.value }
  if (query.value) params.q = query.value
  if (filterType.value.length) params.document_type = filterType.value.join(',')
  if (filterDateFrom.value) params.date_from = filterDateFrom.value
  if (filterDateTo.value) params.date_to = filterDateTo.value
  if (isNum(filterAmountMin.value)) params.amount_min = filterAmountMin.value
  if (isNum(filterAmountMax.value)) params.amount_max = filterAmountMax.value
  if (filterTaxRelevant.value === true) params.tax_relevant = true
  if (filterScope.value) params.filing_scope_id = filterScope.value
  if (filterIssuer.value) params.issuer = filterIssuer.value
  return params
}

// Sequenz-Token: nur die Antwort der juengsten Anfrage darf die Ergebnisse
// setzen (langsame, ueberholte Antworten werden verworfen).
let searchSeq = 0
// Parameter der zuletzt gestarteten Suche — der entprellte Filter-Watcher
// sucht nur, wenn sich wirklich etwas geaendert hat.
let lastSearchKey = ''

async function doSearch() {
  const seq = ++searchSeq
  const filterParams = buildFilterParams()
  lastSearchKey = JSON.stringify(filterParams)
  loading.value = true
  try {
    const data = await searchDocuments({ page: page.value, page_size: pageSize.value, ...filterParams })
    if (seq !== searchSeq) return
    results.value = data.results
    total.value = data.total
    facets.value = data.facets
  } catch (e) {
    if (seq !== searchSeq) return
    const detail = e.response?.status === 400 ? e.response.data?.detail : null
    notify.error(typeof detail === 'string' ? detail : 'Suche fehlgeschlagen.')
  } finally {
    if (seq === searchSeq) loading.value = false
  }
}

// Neue Suche ab Seite 1 — genau ein Request (kein page-Watcher mehr, der
// zusaetzlich feuert).
function resetAndSearch() {
  page.value = 1
  doSearch()
}

function goToPage(p) {
  if (p === page.value) return
  page.value = p
  doSearch()
}

function toggleTypeFilter(type) {
  const idx = filterType.value.indexOf(type)
  if (idx >= 0) {
    filterType.value.splice(idx, 1)
  } else {
    filterType.value.push(type)
  }
  resetAndSearch()
}

function onSortByChange() {
  sortOrder.value = DEFAULT_SORT_ORDER[sortBy.value] || 'desc'
  resetAndSearch()
}

function toggleSortOrder() {
  sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  resetAndSearch()
}

// Setzt alle Filter (nicht den Suchbegriff) zurueck, ohne zu suchen.
function resetFilters() {
  filterType.value = []
  filterDateFrom.value = ''
  filterDateTo.value = ''
  filterAmountMin.value = null
  filterAmountMax.value = null
  filterTaxRelevant.value = null
  filterScope.value = ''
  filterIssuer.value = ''
}

function clearFilters() {
  resetFilters()
  resetAndSearch()
}

function yearRange(year) {
  return { from: `${year}-01-01`, to: `${year}-12-31` }
}

function isYearActive(year) {
  const r = yearRange(year)
  return filterDateFrom.value === r.from && filterDateTo.value === r.to
}

function toggleYear(year) {
  if (isYearActive(year)) {
    filterDateFrom.value = ''
    filterDateTo.value = ''
  } else {
    const r = yearRange(year)
    filterDateFrom.value = r.from
    filterDateTo.value = r.to
  }
  resetAndSearch()
}

function toggleIssuer(issuer) {
  filterIssuer.value = filterIssuer.value === issuer ? '' : issuer
  resetAndSearch()
}

function selectScope(scopeId) {
  filterScope.value = scopeId
  resetAndSearch()
}

// Ausgewaehlte Typen bleiben sichtbar, auch wenn sie in der aktuellen
// Treffermenge nicht (mehr) vorkommen — sonst liesse sich ein Haken nicht
// wieder entfernen.
const typeFacetEntries = computed(() => {
  const counts = { ...(facets.value.document_types || {}) }
  for (const t of filterType.value) {
    if (!(t in counts)) counts[t] = 0
  }
  return Object.entries(counts)
})

const yearFacetEntries = computed(() =>
  Object.entries(facets.value.years || {}).sort((a, b) => b[0].localeCompare(a[0]))
)

const issuerFacetEntries = computed(() => {
  const counts = { ...(facets.value.top_issuers || {}) }
  if (filterIssuer.value && !(filterIssuer.value in counts)) counts[filterIssuer.value] = 0
  return Object.entries(counts)
})

const scopeFacetCounts = computed(() => {
  const map = {}
  for (const s of facets.value.filing_scopes || []) map[s.id] = s.count
  return map
})

function scopeName(id) {
  return filingScopes.value.find(s => s.id === id)?.name || 'Unbekannter Bereich'
}

// Aktive Filter als entfernbare Chips — auch im 0-Treffer-Zustand, damit ein
// Filter nie unsichtbar weiterwirkt (z.B. nach einer Header-Suche).
const activeFilters = computed(() => {
  const chips = []
  for (const t of filterType.value) {
    chips.push({ key: `type-${t}`, label: `Typ: ${typeLabels[t] || t}`, remove: () => toggleTypeFilter(t) })
  }
  if (filterDateFrom.value || filterDateTo.value) {
    const from = filterDateFrom.value ? formatDate(filterDateFrom.value) : '…'
    const to = filterDateTo.value ? formatDate(filterDateTo.value) : '…'
    chips.push({
      key: 'date',
      label: `Zeitraum: ${from} – ${to}`,
      remove: () => { filterDateFrom.value = ''; filterDateTo.value = ''; resetAndSearch() },
    })
  }
  if (isNum(filterAmountMin.value)) {
    chips.push({ key: 'amin', label: `ab ${formatAmount(filterAmountMin.value)}`, remove: () => { filterAmountMin.value = null; resetAndSearch() } })
  }
  if (isNum(filterAmountMax.value)) {
    chips.push({ key: 'amax', label: `bis ${formatAmount(filterAmountMax.value)}`, remove: () => { filterAmountMax.value = null; resetAndSearch() } })
  }
  if (filterTaxRelevant.value === true) {
    chips.push({ key: 'tax', label: 'Nur steuerrelevante', remove: () => { filterTaxRelevant.value = null; resetAndSearch() } })
  }
  if (filterScope.value) {
    chips.push({ key: 'scope', label: `Bereich: ${scopeName(filterScope.value)}`, remove: () => selectScope('') })
  }
  if (filterIssuer.value) {
    chips.push({ key: 'issuer', label: `Aussteller: ${filterIssuer.value}`, remove: () => toggleIssuer(filterIssuer.value) })
  }
  return chips
})

async function saveSearch() {
  if (!saveSearchName.value.trim()) return
  try {
    await createSavedSearch(saveSearchName.value.trim(), buildFilterParams())
    notify.success('Suche gespeichert.')
    showSaveDialog.value = false
    saveSearchName.value = ''
    loadSavedSearches()
  } catch {
    notify.error('Suche konnte nicht gespeichert werden.')
  }
}

async function loadSavedSearches() {
  try {
    savedSearches.value = await getSavedSearches()
  } catch {
    // ignore
  }
}

async function removeSavedSearch(id) {
  try {
    await deleteSavedSearch(id)
    savedSearches.value = savedSearches.value.filter(s => s.id !== id)
  } catch {
    notify.error('Konnte nicht gelöscht werden.')
  }
}

function toNumberOrNull(v) {
  if (v === '' || v == null) return null
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

function applySavedSearch(search) {
  let params
  try {
    params = typeof search.query_params === 'string' ? JSON.parse(search.query_params) : search.query_params
    if (!params || typeof params !== 'object') throw new TypeError('query_params ist kein Objekt')
  } catch (e) {
    if (import.meta.env.DEV) console.error('Ungültige gespeicherte Suche:', e)
    notify.error('Diese gespeicherte Suche ist ungültig und kann nicht angewendet werden.')
    return
  }
  // Erst alles zuruecksetzen, damit keine alten Filter mitwirken.
  resetFilters()
  query.value = typeof params.q === 'string' ? params.q : ''
  filterType.value = typeof params.document_type === 'string' && params.document_type
    ? params.document_type.split(',').map(t => t.trim()).filter(Boolean)
    : []
  filterDateFrom.value = typeof params.date_from === 'string' ? params.date_from : ''
  filterDateTo.value = typeof params.date_to === 'string' ? params.date_to : ''
  filterAmountMin.value = toNumberOrNull(params.amount_min)
  filterAmountMax.value = toNumberOrNull(params.amount_max)
  filterTaxRelevant.value = params.tax_relevant === true || params.tax_relevant === 'true' ? true : null
  filterScope.value = typeof params.filing_scope_id === 'string' ? params.filing_scope_id : ''
  filterIssuer.value = typeof params.issuer === 'string' ? params.issuer : ''
  sortBy.value = SORT_OPTIONS.some(o => o.value === params.sort_by) ? params.sort_by : 'relevance'
  sortOrder.value = params.sort_order === 'asc' || params.sort_order === 'desc'
    ? params.sort_order
    : (DEFAULT_SORT_ORDER[sortBy.value] || 'desc')
  if (filterDateFrom.value || filterDateTo.value || isNum(filterAmountMin.value)
    || isNum(filterAmountMax.value) || filterTaxRelevant.value) {
    showAdvanced.value = true
  }
  resetAndSearch()
}

// Erweiterte Filter (Datum, Betrag, steuerrelevant) suchen entprellt von
// selbst — vorher passierte nichts bis zum Klick auf "Suchen". Wurde mit
// genau diesen Parametern bereits gesucht (Chip entfernt, gespeicherte Suche
// angewendet), entfaellt der zweite Request.
let filterDebounce = null
watch([filterDateFrom, filterDateTo, filterAmountMin, filterAmountMax, filterTaxRelevant], () => {
  clearTimeout(filterDebounce)
  filterDebounce = setTimeout(() => {
    if (JSON.stringify(buildFilterParams()) !== lastSearchKey) resetAndSearch()
  }, 400)
})
onBeforeUnmount(() => clearTimeout(filterDebounce))

// FE-L2: route.query.q kann string | null | string[] sein -> auf string verengen.
function queryFromRoute() {
  return typeof route.query.q === 'string' ? route.query.q : ''
}

// FE-M1: Die Header-Suche navigiert mit ?q=... Ist man bereits auf /suche, wird
// die View-Instanz wiederverwendet (kein erneutes onMounted) — ohne Watcher
// aenderte sich die URL, aber Eingabefeld und Ergebnisse blieben stehen.
// Aktive Filter bleiben bestehen, sind aber als Chips sichtbar.
watch(() => route.query.q, () => {
  query.value = queryFromRoute()
  resetAndSearch()
})

onMounted(async () => {
  query.value = queryFromRoute()
  await ensureScopesLoaded()
  doSearch()
  loadSavedSearches()
})
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-2xl font-bold text-gray-900">Suche</h1>

    <!-- Search bar -->
    <div class="card !p-4">
      <div class="flex gap-3">
        <div class="relative flex-1">
          <svg class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            v-model="query"
            type="text"
            aria-label="Volltextsuche"
            placeholder="Volltextsuche... (z.B. 'Telekom Rechnung' oder tele*)"
            class="input pl-10"
            @keydown.enter="resetAndSearch"
          />
        </div>
        <button @click="resetAndSearch" class="btn-primary">Suchen</button>
        <button @click="showAdvanced = !showAdvanced" class="btn-secondary" :aria-expanded="showAdvanced">
          {{ showAdvanced ? 'Einfach' : 'Erweitert' }}
        </button>
      </div>

      <!-- Advanced filters -->
      <div v-if="showAdvanced" class="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div>
          <label for="search-date-from" class="block text-xs font-medium text-gray-500 mb-1">Datum von</label>
          <input id="search-date-from" v-model="filterDateFrom" type="date" class="input" />
        </div>
        <div>
          <label for="search-date-to" class="block text-xs font-medium text-gray-500 mb-1">Datum bis</label>
          <input id="search-date-to" v-model="filterDateTo" type="date" class="input" />
        </div>
        <div>
          <label for="search-amount-min" class="block text-xs font-medium text-gray-500 mb-1">Min. Betrag</label>
          <input id="search-amount-min" v-model.number="filterAmountMin" type="number" step="0.01" class="input" placeholder="0.00" />
        </div>
        <div>
          <label for="search-amount-max" class="block text-xs font-medium text-gray-500 mb-1">Max. Betrag</label>
          <input id="search-amount-max" v-model.number="filterAmountMax" type="number" step="0.01" class="input" placeholder="9999.99" />
        </div>
        <div class="flex items-end gap-3 col-span-2">
          <label class="flex items-center gap-2">
            <input
              type="checkbox"
              :checked="filterTaxRelevant === true"
              @change="filterTaxRelevant = $event.target.checked ? true : null"
              class="rounded border-gray-300"
            />
            <span class="text-sm text-gray-600">Nur steuerrelevante</span>
          </label>
          <button @click="clearFilters" class="btn-secondary !py-1.5 text-xs ml-auto">Zurücksetzen</button>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 gap-6 lg:grid-cols-4">
      <!-- Sidebar: Facets + Saved searches -->
      <aside class="space-y-4">
        <!-- Document type facets -->
        <div v-if="typeFacetEntries.length" class="card !p-4" data-testid="facet-types">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Dokumenttyp</h3>
          <div class="space-y-1">
            <label
              v-for="[type, count] in typeFacetEntries"
              :key="type"
              class="flex items-center gap-2 cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                :checked="filterType.includes(type)"
                @change="toggleTypeFilter(type)"
                class="rounded border-gray-300"
              />
              <span class="flex-1 text-gray-700">{{ typeLabels[type] || type }}</span>
              <span class="text-gray-400">{{ count }}</span>
            </label>
          </div>
        </div>

        <!-- Year facets -->
        <div v-if="yearFacetEntries.length" class="card !p-4" data-testid="facet-years">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Jahr</h3>
          <div class="space-y-1">
            <button
              v-for="[year, count] in yearFacetEntries"
              :key="year"
              type="button"
              @click="toggleYear(year)"
              :aria-pressed="isYearActive(year)"
              :class="['flex w-full justify-between rounded px-1 text-sm hover:bg-gray-50',
                isYearActive(year) ? 'font-semibold text-primary-700' : 'text-gray-600']"
            >
              <span>{{ year }}</span>
              <span class="text-gray-400">{{ count }}</span>
            </button>
          </div>
        </div>

        <!-- Filing Scope filter -->
        <div v-if="filingScopes.length > 1" class="card !p-4">
          <label for="search-scope" class="block text-xs font-semibold text-gray-500 uppercase mb-2">Ablagebereich</label>
          <select id="search-scope" :value="filterScope" class="input !py-1.5" @change="selectScope($event.target.value)">
            <option value="">Alle Bereiche</option>
            <option v-for="s in filingScopes" :key="s.id" :value="s.id">
              {{ s.name }}{{ scopeFacetCounts[s.id] != null ? ` (${scopeFacetCounts[s.id]})` : '' }}
            </option>
          </select>
        </div>

        <!-- Issuer facets -->
        <div v-if="issuerFacetEntries.length" class="card !p-4" data-testid="facet-issuers">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Aussteller</h3>
          <div class="space-y-1">
            <button
              v-for="[issuer, count] in issuerFacetEntries"
              :key="issuer"
              type="button"
              @click="toggleIssuer(issuer)"
              :aria-pressed="filterIssuer === issuer"
              :class="['flex w-full justify-between gap-2 rounded px-1 text-left text-sm hover:bg-gray-50',
                filterIssuer === issuer ? 'font-semibold text-primary-700' : 'text-gray-600']"
            >
              <span class="truncate">{{ issuer }}</span>
              <span class="text-gray-400">{{ count }}</span>
            </button>
          </div>
        </div>

        <!-- Saved searches -->
        <div class="card !p-4">
          <div class="flex items-center justify-between mb-2">
            <h3 class="text-xs font-semibold text-gray-500 uppercase">Gespeicherte Suchen</h3>
            <button @click="showSaveDialog = true" class="text-xs text-primary-600 hover:text-primary-700">+ Speichern</button>
          </div>
          <div v-if="savedSearches.length === 0" class="text-xs text-gray-400">Keine gespeicherten Suchen.</div>
          <div v-else class="space-y-1">
            <div v-for="s in savedSearches" :key="s.id" class="flex items-center gap-2">
              <button @click="applySavedSearch(s)" class="flex-1 text-left text-sm text-primary-600 hover:text-primary-700 truncate">
                {{ s.name }}
              </button>
              <button
                @click="removeSavedSearch(s.id)"
                :aria-label="`Gespeicherte Suche „${s.name}“ löschen`"
                :title="`Gespeicherte Suche „${s.name}“ löschen`"
                class="text-gray-400 hover:text-red-500 text-xs"
              >&times;</button>
            </div>
          </div>
        </div>

        <!-- Save dialog -->
        <div v-if="showSaveDialog" class="card !p-4">
          <input v-model="saveSearchName" class="input mb-2" aria-label="Name der Suche" placeholder="Name der Suche..." @keydown.enter="saveSearch" />
          <div class="flex gap-2">
            <button @click="saveSearch" class="btn-primary text-xs flex-1">Speichern</button>
            <button @click="showSaveDialog = false" class="btn-secondary text-xs">Abbrechen</button>
          </div>
        </div>
      </aside>

      <!-- Results -->
      <div class="lg:col-span-3 space-y-4">
        <!-- Sort bar -->
        <div class="flex flex-wrap items-center justify-between gap-2 text-sm">
          <span class="text-gray-500">{{ total }} Ergebnis{{ total !== 1 ? 'se' : '' }}</span>
          <div class="flex items-center gap-2">
            <label for="search-sort" class="text-gray-500">Sortierung:</label>
            <select id="search-sort" v-model="sortBy" class="input !w-auto !py-1" @change="onSortByChange">
              <option v-for="o in SORT_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
            <button
              v-if="sortBy !== 'relevance'"
              type="button"
              @click="toggleSortOrder"
              class="btn-secondary !px-2 !py-1"
              data-testid="sort-order-toggle"
              :aria-label="sortOrder === 'asc' ? 'Aufsteigend sortiert – absteigend sortieren' : 'Absteigend sortiert – aufsteigend sortieren'"
              :title="sortOrder === 'asc' ? 'Aufsteigend' : 'Absteigend'"
            >
              {{ sortOrder === 'asc' ? '↑' : '↓' }}
            </button>
          </div>
        </div>

        <!-- Active filters -->
        <div v-if="activeFilters.length" class="flex flex-wrap items-center gap-2" data-testid="active-filters">
          <span
            v-for="chip in activeFilters"
            :key="chip.key"
            class="inline-flex items-center gap-1 rounded-full bg-primary-50 px-3 py-1 text-xs text-primary-700"
          >
            {{ chip.label }}
            <button
              type="button"
              @click="chip.remove()"
              class="ml-0.5 text-primary-400 hover:text-primary-700"
              :aria-label="`Filter „${chip.label}“ entfernen`"
            >&times;</button>
          </span>
          <button type="button" @click="clearFilters" class="text-xs text-gray-500 underline hover:text-gray-700">
            Filter zurücksetzen
          </button>
        </div>

        <!-- Loading -->
        <div v-if="loading" class="flex items-center justify-center py-12">
          <div class="h-8 w-8 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
        </div>

        <!-- No results -->
        <div v-else-if="results.length === 0" class="card text-center py-12">
          <svg class="mx-auto h-12 w-12 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <p class="mt-3 text-gray-500">Keine Ergebnisse gefunden.</p>
          <p class="mt-1 text-sm text-gray-400">
            {{ activeFilters.length ? 'Versuche andere Suchbegriffe oder entferne Filter.' : 'Versuche andere Suchbegriffe.' }}
          </p>
          <button v-if="activeFilters.length" type="button" @click="clearFilters" class="btn-secondary mt-4 text-sm">
            Filter zurücksetzen
          </button>
        </div>

        <!-- Results list -->
        <div v-else class="space-y-3">
          <router-link
            v-for="r in results"
            :key="r.id"
            :to="`/dokumente/${r.id}`"
            class="card !p-4 block hover:shadow-md transition-shadow"
          >
            <div class="flex items-start gap-4">
              <div class="flex h-12 w-12 items-center justify-center rounded-lg bg-gray-100 text-xs font-medium text-gray-500 flex-shrink-0">
                {{ r.document_type?.slice(0, 3) }}
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <h3 class="text-sm font-semibold text-gray-900 truncate">{{ r.title }}</h3>
                  <DocTypeBadge :type="r.document_type" />
                </div>
                <div class="mt-1 flex items-center gap-3 text-xs text-gray-500">
                  <span v-if="r.issuer">{{ r.issuer }}</span>
                  <span v-if="r.document_date">{{ formatDate(r.document_date) }}</span>
                  <span v-if="r.amount != null">{{ formatAmount(r.amount, r.currency) }}</span>
                </div>
                <!-- Highlight snippet: Treffer als <mark>-Knoten, Rest als Text (kein v-html) -->
                <p v-if="r.highlight" class="mt-2 text-sm text-gray-600 line-clamp-2" data-testid="search-highlight">
                  <template v-for="(seg, i) in highlightSegments(r.highlight)" :key="i">
                    <mark v-if="seg.hit" class="rounded bg-yellow-100 px-0.5 text-gray-900">{{ seg.text }}</mark>
                    <template v-else>{{ seg.text }}</template>
                  </template>
                </p>
                <!-- Tags -->
                <div v-if="r.tags?.length" class="mt-2 flex flex-wrap gap-1">
                  <span v-for="tag in r.tags" :key="tag" class="badge bg-gray-100 text-gray-600">{{ tag }}</span>
                </div>
              </div>
            </div>
          </router-link>
        </div>

        <Pagination :total="total" :page="page" :page-size="pageSize" @update:page="goToPage" />
      </div>
    </div>
  </div>
</template>
