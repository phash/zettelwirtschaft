<script setup>
import { ref, onMounted, watch } from 'vue'
import { getTaxSummary, getTaxYears, exportTaxPackage, getTaxValidation, getFilingScopes } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import StatCard from '../components/common/StatCard.vue'

const notify = useNotificationStore()
const loading = ref(true)
const exporting = ref(false)
const years = ref([])
const selectedYear = ref(new Date().getFullYear())
const selectedScope = ref('')
const summary = ref(null)
const validation = ref(null)
const showWarnings = ref(false)
const filingScopes = ref([])

async function loadYears() {
  try {
    const data = await getTaxYears()
    years.value = data.years
    if (data.years.length && !data.years.includes(selectedYear.value)) {
      selectedYear.value = data.years[0]
    }
  } catch {
    // ignore
  }
}

async function loadSummary() {
  loading.value = true
  try {
    const params = selectedScope.value ? { filing_scope_id: selectedScope.value } : {}
    summary.value = await getTaxSummary(selectedYear.value, params)
    validation.value = await getTaxValidation(selectedYear.value, params)
  } catch {
    notify.error('Steuerdaten konnten nicht geladen werden.')
  } finally {
    loading.value = false
  }
}

async function doExport() {
  exporting.value = true
  try {
    await exportTaxPackage(selectedYear.value, selectedScope.value || null)
    notify.success(`Steuerpaket ${selectedYear.value} heruntergeladen.`)
  } catch {
    notify.error('Export fehlgeschlagen.')
  } finally {
    exporting.value = false
  }
}

function formatAmount(amount) {
  return new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(amount)
}

watch([selectedYear, selectedScope], loadSummary)

onMounted(async () => {
  try { filingScopes.value = await getFilingScopes() } catch {}
  await loadYears()
  await loadSummary()
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-bold text-gray-900">Steuerbelege</h1>
      <div class="flex items-center gap-3">
        <select v-if="filingScopes.length > 1" v-model="selectedScope" class="input !w-auto">
          <option value="">Alle Bereiche</option>
          <option v-for="s in filingScopes" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
        <select v-model="selectedYear" class="input !w-auto">
          <option v-for="y in years" :key="y" :value="y">{{ y }}</option>
          <option v-if="!years.includes(selectedYear)" :value="selectedYear">{{ selectedYear }}</option>
        </select>
        <button
          @click="doExport"
          :disabled="exporting || !summary?.total_documents"
          class="btn-primary"
        >
          <span v-if="exporting">Exportiere...</span>
          <span v-else>ZIP exportieren</span>
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-16">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
    </div>

    <template v-else-if="summary">
      <!-- Stats -->
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Belege" :value="summary.total_documents" icon="document" />
        <StatCard label="Gesamtbetrag" :value="formatAmount(summary.total_amount)" icon="currency" />
        <StatCard label="Kategorien" :value="summary.categories.length" icon="folder" />
      </div>

      <!-- Warnungen -->
      <div v-if="validation?.warnings?.length" class="card !p-4 border-l-4 border-amber-400 bg-amber-50">
        <button @click="showWarnings = !showWarnings" class="flex w-full items-center justify-between text-left">
          <div class="flex items-center gap-2">
            <svg class="h-5 w-5 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span class="text-sm font-medium text-amber-800">{{ validation.warnings.length }} Hinweise vor dem Export</span>
          </div>
          <svg :class="['h-4 w-4 text-amber-500 transition-transform', showWarnings ? 'rotate-180' : '']" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        <ul v-if="showWarnings" class="mt-3 space-y-1">
          <li v-for="(w, i) in validation.warnings" :key="i" class="text-sm text-amber-700">{{ w }}</li>
        </ul>
      </div>

      <!-- Kategorien -->
      <div class="space-y-3">
        <h2 class="text-lg font-semibold text-gray-900">Kategorien</h2>
        <div v-if="summary.categories.length === 0" class="card text-center py-12">
          <p class="text-gray-500">Keine steuerrelevanten Belege fuer {{ selectedYear }}.</p>
          <p class="text-sm text-gray-400 mt-1">Markieren Sie Dokumente als steuerrelevant in der Dokumentenansicht.</p>
        </div>
        <div v-else class="grid gap-3">
          <div v-for="cat in summary.categories" :key="cat.category" class="card !p-4">
            <div class="flex items-center justify-between">
              <div>
                <h3 class="text-sm font-semibold text-gray-900">{{ cat.label }}</h3>
                <p class="text-xs text-gray-500 mt-0.5">{{ cat.document_count }} Beleg{{ cat.document_count !== 1 ? 'e' : '' }}</p>
              </div>
              <div class="text-right">
                <p class="text-lg font-semibold text-gray-900">{{ formatAmount(cat.total_amount) }}</p>
              </div>
            </div>
            <!-- Anteilsbalken -->
            <div class="mt-3 h-2 rounded-full bg-gray-100 overflow-hidden">
              <div
                class="h-full rounded-full bg-primary-500"
                :style="{ width: Math.max(2, (cat.total_amount / summary.total_amount) * 100) + '%' }"
              ></div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
