<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import StatCard from '../components/common/StatCard.vue'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import { getDashboardStats, getDocuments, getJobs, uploadDocuments } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

const router = useRouter()
const notify = useNotificationStore()
const stats = ref(null)
const recentDocs = ref([])
const processingJobs = ref([])
const isDragging = ref(false)

async function loadData() {
  try {
    const [s, docs, jobs] = await Promise.all([
      getDashboardStats(),
      getDocuments({ page: 1, page_size: 5, sort_by: 'created_at', sort_order: 'desc' }),
      getJobs({ status: 'PROCESSING,PENDING', page_size: 5 }),
    ])
    stats.value = s
    recentDocs.value = docs.items
    processingJobs.value = jobs.items?.filter(j => ['PENDING', 'PROCESSING'].includes(j.status)) || []
  } catch {
    notify.error('Dashboard-Daten konnten nicht geladen werden.')
  }
}

async function handleDrop(e) {
  isDragging.value = false
  const files = Array.from(e.dataTransfer.files)
  if (!files.length) return

  try {
    const result = await uploadDocuments(files)
    const count = result.uploaded?.length || 0
    if (count > 0) {
      notify.success(`${count} Datei(en) hochgeladen.`)
      loadData()
    }
    if (result.rejected?.length) {
      result.rejected.forEach(r => notify.error(`${r.filename}: ${r.error}`))
    }
  } catch {
    notify.error('Fehler beim Upload.')
  }
}

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('de-DE')
}

onMounted(loadData)
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-2xl font-bold text-gray-900">Dashboard</h1>

    <!-- Stats -->
    <div v-if="stats" class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Gesamt Dokumente" :value="stats.total_documents" color="blue" />
      <StatCard label="Diesen Monat" :value="stats.documents_this_month" color="green" />
      <StatCard label="Offene Rueckfragen" :value="stats.pending_reviews" color="orange" />
      <StatCard label="Garantien (30 Tage)" :value="stats.expiring_warranties_30d" color="purple" />
    </div>

    <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <!-- Recent documents -->
      <div class="card">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold text-gray-900">Letzte Dokumente</h2>
          <router-link to="/dokumente" class="text-sm text-primary-600 hover:text-primary-700">Alle anzeigen</router-link>
        </div>
        <div v-if="recentDocs.length === 0" class="py-8 text-center text-sm text-gray-400">
          Noch keine Dokumente vorhanden.
        </div>
        <div v-else class="divide-y divide-gray-100">
          <router-link
            v-for="doc in recentDocs"
            :key="doc.id"
            :to="`/dokumente/${doc.id}`"
            class="flex items-center gap-3 py-3 hover:bg-gray-50 rounded-lg px-2 -mx-2 transition-colors"
          >
            <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-gray-100 text-gray-500 text-xs font-medium flex-shrink-0">
              {{ doc.file_type?.toUpperCase() }}
            </div>
            <div class="flex-1 min-w-0">
              <p class="text-sm font-medium text-gray-900 truncate">{{ doc.title }}</p>
              <p class="text-xs text-gray-500">{{ formatDate(doc.created_at) }}</p>
            </div>
            <DocTypeBadge :type="doc.document_type" />
          </router-link>
        </div>
      </div>

      <!-- Quick upload + processing -->
      <div class="space-y-6">
        <!-- Drop zone -->
        <div
          :class="[
            'card cursor-pointer border-2 border-dashed text-center transition-colors',
            isDragging ? 'border-primary-400 bg-primary-50' : 'border-gray-300 hover:border-gray-400',
          ]"
          @dragover.prevent="isDragging = true"
          @dragleave="isDragging = false"
          @drop.prevent="handleDrop"
          @click="$router.push('/upload')"
        >
          <svg class="mx-auto h-10 w-10 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <p class="mt-2 text-sm font-medium text-gray-600">Dateien hierher ziehen oder klicken</p>
          <p class="text-xs text-gray-400">PDF, JPG, PNG, TIFF</p>
        </div>

        <!-- Processing queue -->
        <div v-if="processingJobs.length > 0" class="card">
          <h2 class="text-lg font-semibold text-gray-900 mb-4">In Verarbeitung</h2>
          <div class="space-y-3">
            <div v-for="job in processingJobs" :key="job.id" class="flex items-center gap-3">
              <div class="h-2 w-2 rounded-full animate-pulse"
                :class="job.status === 'PROCESSING' ? 'bg-blue-500' : 'bg-gray-400'"
              ></div>
              <span class="flex-1 text-sm truncate">{{ job.original_filename }}</span>
              <span class="badge bg-blue-100 text-blue-700">{{ job.status === 'PROCESSING' ? 'Wird verarbeitet' : 'Wartet' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
