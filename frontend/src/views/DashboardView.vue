<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import StatCard from '../components/common/StatCard.vue'
import DocTypeBadge from '../components/common/DocTypeBadge.vue'
import { getDashboardStats, getDocuments, getJobs, uploadDocuments, getQueueStatus, pauseQueue, resumeQueue, retryJob, getEmailStats } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { formatDate } from '../utils/formatters'

const router = useRouter()
const notify = useNotificationStore()
const stats = ref(null)
const recentDocs = ref([])
const processingJobs = ref([])
const failedJobs = ref([])
const isDragging = ref(false)
const queuePaused = ref(false)
const emailStats = ref(null)
const loadingData = ref(false)
let pollTimer = null
// Request-Token: gegen Race-Bedingungen wenn Polling eine zweite loadData
// startet, waehrend die erste E-Mail-Stats noch nicht aufgeloest hat.
let activeRequestId = 0

async function loadData() {
  if (loadingData.value) return
  loadingData.value = true
  const requestId = ++activeRequestId
  try {
    const [s, docs, activeJobs, failJobs, qStatus] = await Promise.all([
      getDashboardStats(),
      getDocuments({ page: 1, page_size: 5, sort_by: 'created_at', sort_order: 'desc' }),
      getJobs({ status: 'PROCESSING,PENDING', page_size: 20 }),
      getJobs({ status: 'FAILED', page_size: 10 }),
      getQueueStatus(),
    ])
    if (requestId !== activeRequestId) return  // veraltet
    stats.value = s
    recentDocs.value = docs.items
    processingJobs.value = activeJobs.items?.filter(j => ['PENDING', 'PROCESSING'].includes(j.status)) || []
    failedJobs.value = failJobs.items || []
    queuePaused.value = qStatus.paused

    // E-Mail-Stats separat laden (blockiert Dashboard nicht wenn E-Mail nicht konfiguriert)
    getEmailStats().then(stats => {
      if (requestId !== activeRequestId) return  // veraltet, nicht ueberschreiben
      if (stats && stats.length > 0) {
        emailStats.value = stats.reduce((acc, s) => ({
          total: acc.total + s.total_processed,
          relevant: acc.relevant + s.relevant,
        }), { total: 0, relevant: 0 })
      }
    }).catch(() => { /* E-Mail nicht konfiguriert, ignorieren */ })

    if (processingJobs.value.length > 0 && !pollTimer) {
      pollTimer = setInterval(loadData, 3000)
    } else if (processingJobs.value.length === 0 && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  } catch {
    notify.error('Dashboard-Daten konnten nicht geladen werden.')
  } finally {
    loadingData.value = false
  }
}

async function toggleQueue() {
  try {
    if (queuePaused.value) {
      await resumeQueue()
      queuePaused.value = false
      notify.success('Verarbeitung fortgesetzt.')
    } else {
      await pauseQueue()
      queuePaused.value = true
      notify.success('Verarbeitung pausiert.')
    }
  } catch {
    notify.error('Queue-Status konnte nicht geändert werden.')
  }
}

async function handleRetry(job) {
  try {
    await retryJob(job.id)
    failedJobs.value = failedJobs.value.filter(j => j.id !== job.id)
    notify.success(`"${job.original_filename}" wird erneut verarbeitet.`)
    await loadData()
  } catch {
    notify.error('Wiederholung fehlgeschlagen.')
  }
}

function copyForClaude(job) {
  const text = [
    '## Zettelwirtschaft Fehler-Report',
    '',
    `**Datei:** ${job.original_filename}`,
    `**Status:** ${job.status}`,
    `**Fehler:** ${job.error_message || 'Keine Fehlermeldung verfügbar'}`,
    `**Zeitpunkt:** ${new Date(job.updated_at || job.created_at).toLocaleString('de-DE')}`,
    '',
    '**Zur Diagnose bitte in der Konsole ausführen:**',
    '```',
    'docker compose logs backend --tail=100',
    'docker compose logs ollama --tail=50',
    'docker compose ps',
    '```',
    '',
    '**Mögliche Ursachen:**',
    '- Ollama nicht erreichbar oder Modell nicht geladen',
    '- OCR-Fehler bei beschädigter Datei',
    '- Speicherplatz voll',
  ].join('\n')
  navigator.clipboard.writeText(text).then(() => notify.success('Fehlerinfo kopiert.'))
}

onUnmounted(() => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
})

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

onMounted(loadData)
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-2xl font-bold text-gray-900">Dashboard</h1>

    <!-- Stats -->
    <div v-if="stats" class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Gesamt Dokumente" :value="stats.total_documents" color="blue" />
      <StatCard label="Diesen Monat" :value="stats.documents_this_month" color="green" />
      <StatCard label="Offene Rückfragen" :value="stats.pending_reviews" color="orange" />
      <StatCard label="Garantien (30 Tage)" :value="stats.expiring_warranties_30d" color="purple" />
      <StatCard v-if="emailStats" label="E-Mails importiert" :value="emailStats.relevant" color="indigo" />
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
        <div v-if="processingJobs.length > 0 || queuePaused" class="card">
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-lg font-semibold text-gray-900">In Verarbeitung</h2>
            <button
              @click="toggleQueue"
              :class="[
                'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                queuePaused
                  ? 'bg-green-100 text-green-700 hover:bg-green-200'
                  : 'bg-orange-100 text-orange-700 hover:bg-orange-200',
              ]"
            >
              <svg v-if="queuePaused" class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
                <path d="M6.3 2.84A1.5 1.5 0 004 4.11v11.78a1.5 1.5 0 002.3 1.27l9.344-5.891a1.5 1.5 0 000-2.538L6.3 2.84z" />
              </svg>
              <svg v-else class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
                <path d="M5.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75A.75.75 0 007.25 3h-1.5zM12.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75a.75.75 0 00-.75-.75h-1.5z" />
              </svg>
              {{ queuePaused ? 'Fortsetzen' : 'Pausieren' }}
            </button>
          </div>
          <div v-if="queuePaused && processingJobs.length === 0" class="py-4 text-center text-sm text-orange-600">
            Verarbeitung ist pausiert.
          </div>
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

        <!-- Failed jobs -->
        <div v-if="failedJobs.length > 0" class="card border border-red-100">
          <h2 class="text-lg font-semibold text-red-700 mb-4">Systemfehler</h2>
          <div class="space-y-3">
            <div v-for="job in failedJobs" :key="job.id" class="rounded-lg bg-red-50 p-3">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0 flex-1">
                  <p class="text-sm font-medium text-gray-900 truncate">{{ job.original_filename }}</p>
                  <p class="mt-1 text-xs text-gray-500">{{ new Date(job.updated_at || job.created_at).toLocaleString('de-DE') }}</p>
                </div>
                <div class="flex gap-1 flex-shrink-0">
                  <button
                    @click="handleRetry(job)"
                    title="Erneut verarbeiten"
                    class="rounded-md bg-white px-2 py-1 text-xs font-medium text-primary-600 shadow-sm ring-1 ring-gray-200 hover:bg-primary-50"
                  >
                    Wiederholen
                  </button>
                  <button
                    @click="copyForClaude(job)"
                    title="Fehlerinfo kopieren"
                    class="rounded-md bg-white px-2 py-1 text-xs font-medium text-gray-600 shadow-sm ring-1 ring-gray-200 hover:bg-gray-50"
                  >
                    Kopieren
                  </button>
                </div>
              </div>
              <div class="mt-2 rounded bg-red-100 p-2 text-xs text-red-700 font-mono break-all select-all cursor-text">
                {{ job.error_message || 'Keine Fehlermeldung verfügbar. Bitte "Kopieren" nutzen und Logs prüfen.' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
