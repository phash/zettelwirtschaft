<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getSystemHealth } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { formatBytes } from '../../utils/formatters'

const notify = useNotificationStore()
const loading = ref(true)
const health = ref(null)
const installPath = ref('')

const emit = defineEmits(['health-loaded'])

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => notify.success('Kopiert.'))
}

function copyChromaError(comp) {
  const text = [
    '## ChromaDB Fehler-Report',
    '',
    `**Status:** ${comp.status}`,
    `**Fehlermeldung:** ${comp.message || 'keine'}`,
    `**Zeitpunkt:** ${new Date().toLocaleString('de-DE')}`,
    '',
    '**Zur Diagnose:**',
    '```',
    'docker compose ps',
    'docker compose logs chromadb --tail=50',
    '```',
  ].join('\n')
  navigator.clipboard.writeText(text).then(() => notify.success('In Zwischenablage kopiert.'))
}

async function loadHealth(isPolling = false) {
  if (!isPolling) loading.value = true
  try {
    health.value = await getSystemHealth()
    if (health.value.install_path) {
      installPath.value = health.value.install_path
    }
    emit('health-loaded', health.value)
  } catch {
    if (!isPolling) notify.error('Systemstatus konnte nicht geladen werden.')
  } finally {
    if (!isPolling) loading.value = false
  }
}

function componentStatusClass(status) {
  if (status === 'ok') return 'bg-green-500'
  if (status === 'degraded' || status === 'offline') return 'bg-amber-500'
  return 'bg-red-500'
}

let healthTimer = null

onMounted(async () => {
  await loadHealth()
  healthTimer = setInterval(() => loadHealth(true), 10000)
})

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
})

defineExpose({ loadHealth, health, installPath })
</script>

<template>
  <!-- Loading -->
  <div v-if="loading" class="flex items-center justify-center py-16">
    <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
  </div>

  <template v-else-if="health">
    <!-- Gesamtstatus + Version -->
    <div class="card !p-4 space-y-2">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span :class="['h-3 w-3 rounded-full', componentStatusClass(health.status)]"></span>
          <span class="text-sm font-medium text-gray-900">
            System {{ health.status === 'ok' ? 'betriebsbereit' : 'eingeschränkt' }}
          </span>
        </div>
        <span v-if="health.app_version" class="text-xs font-mono text-gray-400 bg-gray-100 px-2 py-1 rounded">
          v{{ health.app_version }}
        </span>
      </div>
      <div v-if="installPath" class="flex items-center gap-2 text-xs text-gray-500">
        <svg class="w-3.5 h-3.5 text-gray-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
        <span>Installationsordner:</span>
        <code class="bg-gray-100 px-1.5 py-0.5 rounded font-mono text-gray-600 select-all">{{ installPath }}</code>
        <button @click="copyText('explorer.exe &quot;' + installPath + '&quot;')" class="text-primary-600 hover:text-primary-700 underline" title="Explorer-Befehl kopieren">Ordner öffnen</button>
      </div>
    </div>

    <!-- Komponenten -->
    <div class="card">
      <h2 class="text-lg font-semibold text-gray-900 mb-4">Komponenten</h2>
      <div class="space-y-3">
        <div v-for="(comp, name) in health.components" :key="name" class="space-y-1">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span :class="['h-2.5 w-2.5 rounded-full', componentStatusClass(comp.status)]"></span>
              <span class="text-sm font-medium text-gray-700 capitalize">{{ name }}</span>
            </div>
            <div class="text-sm text-gray-500">
              <span v-if="comp.status === 'ok'" class="text-green-600">OK</span>
              <span v-else class="text-red-600">{{ comp.message || comp.status }}</span>
              <span v-if="comp.models" class="ml-2 text-xs text-gray-400">
                ({{ comp.models.join(', ') }})
              </span>
            </div>
          </div>
          <!-- ChromaDB Fehler-Hilfe -->
          <div v-if="name === 'chromadb' && comp.status !== 'ok'" class="ml-5 rounded-lg bg-red-50 border border-red-100 p-3 text-xs text-red-700 space-y-2">
            <p class="font-medium">ChromaDB ist nicht erreichbar. Der KI-Assistent (RAG) ist dadurch deaktiviert.</p>
            <p>Mögliche Ursachen und Lösungen:</p>
            <ul class="list-disc list-inside space-y-1 text-red-600">
              <li>ChromaDB-Container ist nicht gestartet &rarr; <code class="bg-red-100 px-1 rounded">docker compose up -d chromadb</code></li>
              <li>Port-Konflikt &rarr; <code class="bg-red-100 px-1 rounded">docker compose ps</code> prüfen</li>
              <li>Neustart: <code class="bg-red-100 px-1 rounded">docker compose restart chromadb</code></li>
            </ul>
            <button
              @click="copyChromaError(comp)"
              class="mt-1 flex items-center gap-1 text-xs text-red-600 hover:text-red-800 underline"
            >
              Fehler für Issue-Report kopieren
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Speicher -->
    <div v-if="health.statistics" class="card">
      <h2 class="text-lg font-semibold text-gray-900 mb-4">Speicher</h2>
      <div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <p class="text-xs text-gray-500">Datenbank</p>
          <p class="text-sm font-semibold">{{ formatBytes(health.statistics.database_size_bytes) }}</p>
        </div>
        <div>
          <p class="text-xs text-gray-500">Archiv</p>
          <p class="text-sm font-semibold">{{ formatBytes(health.statistics.archive_size_bytes) }}</p>
        </div>
        <div>
          <p class="text-xs text-gray-500">Uploads</p>
          <p class="text-sm font-semibold">{{ formatBytes(health.statistics.upload_size_bytes) }}</p>
        </div>
        <div>
          <p class="text-xs text-gray-500">Festplatte frei</p>
          <p class="text-sm font-semibold">{{ formatBytes(health.statistics.disk_free_bytes) }}</p>
        </div>
      </div>
      <!-- Disk-Balken -->
      <div v-if="health.statistics.disk_total_bytes" class="mt-4">
        <div class="h-2 rounded-full bg-gray-100 overflow-hidden">
          <div
            class="h-full bg-primary-500 rounded-full"
            :style="{ width: ((health.statistics.disk_used_bytes / health.statistics.disk_total_bytes) * 100) + '%' }"
          ></div>
        </div>
        <p class="text-xs text-gray-400 mt-1">
          {{ formatBytes(health.statistics.disk_used_bytes) }} / {{ formatBytes(health.statistics.disk_total_bytes) }} belegt
        </p>
      </div>
    </div>
  </template>
</template>
