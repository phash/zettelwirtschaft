<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import Pagination from '../components/common/Pagination.vue'
import { getDocuments, getFilingScopes } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

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
const filingScopes = ref([])

const documentTypes = [
  'RECHNUNG', 'QUITTUNG', 'KAUFVERTRAG', 'GARANTIESCHEIN',
  'VERSICHERUNGSPOLICE', 'KONTOAUSZUG', 'LOHNABRECHNUNG',
  'STEUERBESCHEID', 'MIETVERTRAG', 'HANDWERKER_RECHNUNG',
  'ARZTRECHNUNG', 'REZEPT', 'AMTLICHES_SCHREIBEN',
  'BEDIENUNGSANLEITUNG', 'SONSTIGES',
]

async function loadDocuments() {
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
    documents.value = data.items
    total.value = data.total
  } catch {
    notify.error('Dokumente konnten nicht geladen werden.')
  } finally {
    loading.value = false
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

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('de-DE')
}

function formatAmount(amount, currency) {
  if (amount == null) return '-'
  return new Intl.NumberFormat('de-DE', { style: 'currency', currency: currency || 'EUR' }).format(amount)
}

function sortIcon(column) {
  if (sortBy.value !== column) return ''
  return sortOrder.value === 'asc' ? ' \u2191' : ' \u2193'
}

watch([filterType, filterDateFrom, filterDateTo, filterTaxRelevant, filterScope, sortBy, sortOrder], () => {
  page.value = 1
  loadDocuments()
})

watch(page, loadDocuments)

onMounted(async () => {
  try { filingScopes.value = await getFilingScopes() } catch {}
  loadDocuments()
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-bold text-gray-900">Dokumente</h1>
      <router-link to="/upload" class="btn-primary">Hochladen</router-link>
    </div>

    <!-- Filters -->
    <div class="card !p-4">
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Typ</label>
          <select v-model="filterType" class="input !py-1.5">
            <option value="">Alle Typen</option>
            <option v-for="t in documentTypes" :key="t" :value="t">{{ t }}</option>
          </select>
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Von</label>
          <input v-model="filterDateFrom" type="date" class="input !py-1.5" />
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Bis</label>
          <input v-model="filterDateTo" type="date" class="input !py-1.5" />
        </div>
        <div v-if="filingScopes.length > 1">
          <label class="block text-xs font-medium text-gray-500 mb-1">Bereich</label>
          <select v-model="filterScope" class="input !py-1.5">
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
        <button @click="clearFilters" class="btn-secondary !py-1.5 text-xs">Zuruecksetzen</button>
      </div>
    </div>

    <!-- Table -->
    <div class="card !p-0 overflow-hidden">
      <div v-if="loading" class="flex items-center justify-center py-12">
        <div class="h-8 w-8 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
      </div>
      <table v-else class="w-full">
        <thead class="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
          <tr>
            <th class="px-4 py-3 w-10"></th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" @click="toggleSort('title')">
              Titel{{ sortIcon('title') }}
            </th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" @click="toggleSort('document_type')">
              Typ{{ sortIcon('document_type') }}
            </th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" @click="toggleSort('document_date')">
              Datum{{ sortIcon('document_date') }}
            </th>
            <th class="px-4 py-3 cursor-pointer hover:text-gray-700" @click="toggleSort('amount')">
              Betrag{{ sortIcon('amount') }}
            </th>
            <th class="px-4 py-3">Aussteller</th>
            <th class="px-4 py-3">Tags</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="documents.length === 0">
            <td colspan="7" class="px-4 py-12 text-center text-sm text-gray-400">
              Keine Dokumente gefunden.
            </td>
          </tr>
          <tr
            v-for="doc in documents"
            :key="doc.id"
            class="cursor-pointer hover:bg-gray-50 transition-colors"
            @click="router.push(`/dokumente/${doc.id}`)"
          >
            <td class="px-4 py-3">
              <div class="flex h-8 w-8 items-center justify-center rounded bg-gray-100 text-[10px] font-medium text-gray-500">
                {{ doc.file_type?.toUpperCase() }}
              </div>
            </td>
            <td class="px-4 py-3">
              <p class="text-sm font-medium text-gray-900 truncate max-w-xs">{{ doc.title }}</p>
              <p class="text-xs text-gray-400">{{ doc.original_filename }}</p>
            </td>
            <td class="px-4 py-3"><DocTypeBadge :type="doc.document_type" /></td>
            <td class="px-4 py-3 text-sm text-gray-600">{{ formatDate(doc.document_date) }}</td>
            <td class="px-4 py-3 text-sm text-gray-600">{{ formatAmount(doc.amount, doc.currency) }}</td>
            <td class="px-4 py-3 text-sm text-gray-600 truncate max-w-[120px]">{{ doc.issuer || '-' }}</td>
            <td class="px-4 py-3">
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

    <Pagination :total="total" :page="page" :page-size="pageSize" @update:page="page = $event" />
  </div>
</template>
