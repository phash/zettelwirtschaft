<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getWarranties, getWarrantyStats } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import StatCard from '../components/common/StatCard.vue'

const router = useRouter()
const notify = useNotificationStore()
const loading = ref(true)
const warranties = ref([])
const stats = ref(null)
const filterStatus = ref('all')

async function loadData() {
  loading.value = true
  try {
    const statusParam = filterStatus.value === 'all' ? undefined : filterStatus.value
    const [ws, st] = await Promise.all([
      getWarranties({ status: statusParam }),
      getWarrantyStats(),
    ])
    warranties.value = ws
    stats.value = st
  } catch {
    notify.error('Garantiedaten konnten nicht geladen werden.')
  } finally {
    loading.value = false
  }
}

function statusBadge(w) {
  if (w.is_expired) return { text: 'Abgelaufen', class: 'bg-red-100 text-red-700' }
  if (w.days_remaining <= 90) return { text: `${w.days_remaining} Tage`, class: 'bg-amber-100 text-amber-700' }
  return { text: 'Aktiv', class: 'bg-green-100 text-green-700' }
}

function progressPercent(w) {
  const totalDays = w.warranty_duration_months * 30
  const elapsed = totalDays - w.days_remaining
  return Math.min(100, Math.max(0, (elapsed / totalDays) * 100))
}

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('de-DE')
}

function goToDocument(docId) {
  router.push(`/dokumente/${docId}`)
}

onMounted(loadData)
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-bold text-gray-900">Garantien</h1>
      <div class="flex items-center gap-2">
        <select v-model="filterStatus" class="input !w-auto !py-1.5" @change="loadData">
          <option value="all">Alle</option>
          <option value="active">Aktiv</option>
          <option value="expiring">Bald ablaufend</option>
          <option value="expired">Abgelaufen</option>
        </select>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-16">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
    </div>

    <template v-else>
      <!-- Stats -->
      <div v-if="stats" class="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Gesamt" :value="stats.total" icon="document" />
        <StatCard label="Aktiv" :value="stats.active" icon="check" />
        <StatCard label="Bald ablaufend" :value="stats.expiring_soon" icon="warning" />
        <StatCard label="Abgelaufen" :value="stats.expired" icon="error" />
      </div>

      <!-- Leerer Zustand -->
      <div v-if="warranties.length === 0" class="card text-center py-12">
        <svg class="mx-auto h-12 w-12 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
        <p class="mt-3 text-gray-500">Keine Garantien vorhanden.</p>
        <p class="mt-1 text-sm text-gray-400">Garantien werden automatisch aus Kaufbelegen erkannt.</p>
      </div>

      <!-- Garantie-Liste -->
      <div v-else class="space-y-3">
        <div
          v-for="w in warranties"
          :key="w.id"
          class="card !p-4 cursor-pointer hover:shadow-md transition-shadow"
          @click="goToDocument(w.document_id)"
        >
          <div class="flex items-start gap-4">
            <div class="flex h-12 w-12 items-center justify-center rounded-lg bg-gray-100 text-gray-500 flex-shrink-0">
              <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <h3 class="text-sm font-semibold text-gray-900 truncate">{{ w.product_name }}</h3>
                <span :class="['badge', statusBadge(w).class]">{{ statusBadge(w).text }}</span>
              </div>
              <div class="mt-1 flex items-center gap-3 text-xs text-gray-500">
                <span v-if="w.retailer">{{ w.retailer }}</span>
                <span>Kauf: {{ formatDate(w.purchase_date) }}</span>
                <span>Ende: {{ formatDate(w.warranty_end_date) }}</span>
              </div>
              <!-- Fortschrittsbalken -->
              <div class="mt-2 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                <div
                  :class="[
                    'h-full rounded-full transition-all',
                    w.is_expired ? 'bg-red-400' : w.days_remaining <= 90 ? 'bg-amber-400' : 'bg-green-400',
                  ]"
                  :style="{ width: progressPercent(w) + '%' }"
                ></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
