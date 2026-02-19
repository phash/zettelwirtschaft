<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import Pagination from '../components/common/Pagination.vue'
import { searchDocuments, createSavedSearch, getSavedSearches, deleteSavedSearch, getFilingScopes } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

const route = useRoute()
const router = useRouter()
const notify = useNotificationStore()

const results = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(25)
const facets = ref({ document_types: {}, years: {}, top_issuers: {}, filing_scopes: {} })
const filingScopes = ref([])
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
const sortBy = ref('relevance')
const sortOrder = ref('desc')
const showAdvanced = ref(false)

// Save search
const saveSearchName = ref('')
const showSaveDialog = ref(false)

async function doSearch() {
  loading.value = true
  try {
    const params = {
      page: page.value,
      page_size: pageSize.value,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    }
    if (query.value) params.q = query.value
    if (filterType.value.length) params.document_type = filterType.value.join(',')
    if (filterDateFrom.value) params.date_from = filterDateFrom.value
    if (filterDateTo.value) params.date_to = filterDateTo.value
    if (filterAmountMin.value != null) params.amount_min = filterAmountMin.value
    if (filterAmountMax.value != null) params.amount_max = filterAmountMax.value
    if (filterTaxRelevant.value !== null) params.tax_relevant = filterTaxRelevant.value
    if (filterScope.value) params.filing_scope_id = filterScope.value

    const data = await searchDocuments(params)
    results.value = data.results
    total.value = data.total
    facets.value = data.facets
  } catch {
    notify.error('Suche fehlgeschlagen.')
  } finally {
    loading.value = false
  }
}

function toggleTypeFilter(type) {
  const idx = filterType.value.indexOf(type)
  if (idx >= 0) {
    filterType.value.splice(idx, 1)
  } else {
    filterType.value.push(type)
  }
  page.value = 1
  doSearch()
}

function clearFilters() {
  filterType.value = []
  filterDateFrom.value = ''
  filterDateTo.value = ''
  filterAmountMin.value = null
  filterAmountMax.value = null
  filterTaxRelevant.value = null
  filterScope.value = ''
  page.value = 1
  doSearch()
}

async function saveSearch() {
  if (!saveSearchName.value.trim()) return
  try {
    const params = {}
    if (query.value) params.q = query.value
    if (filterType.value.length) params.document_type = filterType.value.join(',')
    if (filterDateFrom.value) params.date_from = filterDateFrom.value
    if (filterDateTo.value) params.date_to = filterDateTo.value

    await createSavedSearch(saveSearchName.value.trim(), params)
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
    notify.error('Konnte nicht geloescht werden.')
  }
}

function applySavedSearch(search) {
  const params = JSON.parse(search.query_params)
  query.value = params.q || ''
  filterType.value = params.document_type ? params.document_type.split(',') : []
  filterDateFrom.value = params.date_from || ''
  filterDateTo.value = params.date_to || ''
  page.value = 1
  doSearch()
}

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('de-DE')
}

function formatAmount(amount, currency) {
  if (amount == null) return '-'
  return new Intl.NumberFormat('de-DE', { style: 'currency', currency: currency || 'EUR' }).format(amount)
}

watch(page, doSearch)

onMounted(async () => {
  if (route.query.q) {
    query.value = route.query.q
  }
  try { filingScopes.value = await getFilingScopes() } catch {}
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
          <svg class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            v-model="query"
            type="text"
            placeholder="Volltextsuche... (z.B. 'Telekom Rechnung' oder tele*)"
            class="input pl-10"
            @keydown.enter="page = 1; doSearch()"
          />
        </div>
        <button @click="page = 1; doSearch()" class="btn-primary">Suchen</button>
        <button @click="showAdvanced = !showAdvanced" class="btn-secondary">
          {{ showAdvanced ? 'Einfach' : 'Erweitert' }}
        </button>
      </div>

      <!-- Advanced filters -->
      <div v-if="showAdvanced" class="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Datum von</label>
          <input v-model="filterDateFrom" type="date" class="input" />
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Datum bis</label>
          <input v-model="filterDateTo" type="date" class="input" />
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Min. Betrag</label>
          <input v-model.number="filterAmountMin" type="number" step="0.01" class="input" placeholder="0.00" />
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Max. Betrag</label>
          <input v-model.number="filterAmountMax" type="number" step="0.01" class="input" placeholder="9999.99" />
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
          <button @click="clearFilters" class="btn-secondary !py-1.5 text-xs ml-auto">Zuruecksetzen</button>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 gap-6 lg:grid-cols-4">
      <!-- Sidebar: Facets + Saved searches -->
      <aside class="space-y-4">
        <!-- Document type facets -->
        <div v-if="Object.keys(facets.document_types).length" class="card !p-4">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Dokumenttyp</h3>
          <div class="space-y-1">
            <label
              v-for="(count, type) in facets.document_types"
              :key="type"
              class="flex items-center gap-2 cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                :checked="filterType.includes(type)"
                @change="toggleTypeFilter(type)"
                class="rounded border-gray-300"
              />
              <span class="flex-1 text-gray-700">{{ type }}</span>
              <span class="text-gray-400">{{ count }}</span>
            </label>
          </div>
        </div>

        <!-- Year facets -->
        <div v-if="Object.keys(facets.years).length" class="card !p-4">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Jahr</h3>
          <div class="space-y-1">
            <span v-for="(count, year) in facets.years" :key="year" class="flex justify-between text-sm text-gray-600">
              <span>{{ year }}</span>
              <span class="text-gray-400">{{ count }}</span>
            </span>
          </div>
        </div>

        <!-- Filing Scope filter -->
        <div v-if="filingScopes.length > 1" class="card !p-4">
          <h3 class="text-xs font-semibold text-gray-500 uppercase mb-2">Ablagebereich</h3>
          <select v-model="filterScope" class="input !py-1.5" @change="page = 1; doSearch()">
            <option value="">Alle Bereiche</option>
            <option v-for="s in filingScopes" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
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
              <button @click="removeSavedSearch(s.id)" class="text-gray-400 hover:text-red-500 text-xs">&times;</button>
            </div>
          </div>
        </div>

        <!-- Save dialog -->
        <div v-if="showSaveDialog" class="card !p-4">
          <input v-model="saveSearchName" class="input mb-2" placeholder="Name der Suche..." @keydown.enter="saveSearch" />
          <div class="flex gap-2">
            <button @click="saveSearch" class="btn-primary text-xs flex-1">Speichern</button>
            <button @click="showSaveDialog = false" class="btn-secondary text-xs">Abbrechen</button>
          </div>
        </div>
      </aside>

      <!-- Results -->
      <div class="lg:col-span-3 space-y-4">
        <!-- Sort bar -->
        <div class="flex items-center justify-between text-sm">
          <span class="text-gray-500">{{ total }} Ergebnis{{ total !== 1 ? 'se' : '' }}</span>
          <div class="flex items-center gap-2">
            <span class="text-gray-500">Sortierung:</span>
            <select v-model="sortBy" class="input !w-auto !py-1" @change="doSearch()">
              <option value="relevance">Relevanz</option>
              <option value="date">Datum</option>
              <option value="amount">Betrag</option>
              <option value="title">Titel</option>
              <option value="created_at">Erstellt</option>
            </select>
          </div>
        </div>

        <!-- Loading -->
        <div v-if="loading" class="flex items-center justify-center py-12">
          <div class="h-8 w-8 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
        </div>

        <!-- No results -->
        <div v-else-if="results.length === 0" class="card text-center py-12">
          <svg class="mx-auto h-12 w-12 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <p class="mt-3 text-gray-500">Keine Ergebnisse gefunden.</p>
          <p class="mt-1 text-sm text-gray-400">Versuchen Sie andere Suchbegriffe oder weniger Filter.</p>
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
                <!-- Highlight snippet -->
                <p v-if="r.highlight" class="mt-2 text-sm text-gray-600 line-clamp-2" v-html="r.highlight"></p>
                <!-- Tags -->
                <div v-if="r.tags?.length" class="mt-2 flex flex-wrap gap-1">
                  <span v-for="tag in r.tags" :key="tag" class="badge bg-gray-100 text-gray-600">{{ tag }}</span>
                </div>
              </div>
            </div>
          </router-link>
        </div>

        <Pagination :total="total" :page="page" :page-size="pageSize" @update:page="page = $event" />
      </div>
    </div>
  </div>
</template>
