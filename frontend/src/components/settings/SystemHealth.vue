<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getSystemHealth } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { formatBytes, formatDateTime } from '../../utils/formatters'
import { copyToClipboard } from '../../utils/clipboard'

const notify = useNotificationStore()
const loading = ref(true)
const health = ref(null)
const installPath = ref('')

const emit = defineEmits(['health-loaded'])

// Der Health-Check ist teuer (Ollama-/ChromaDB-Probe, Archiv-Groesse per
// rglob) und aendert sich selten — 60 s reichen; im Hintergrund-Tab gar nicht.
const POLL_INTERVAL_MS = 60000

const COMPONENT_LABELS = {
  backend: 'Backend',
  database: 'Datenbank',
  ollama: 'KI-Modelle (Ollama)',
  chromadb: 'Vektor-Datenbank',
  storage: 'Speicher',
}

// Gleiches Vokabular wie /api/health und /api/system/health: ok | warning | error
const STATUS_LABELS = {
  ok: 'OK',
  warning: 'Warnung',
  error: 'Fehler',
}

const MODEL_PURPOSE_LABELS = { llm: 'Analyse & Assistent', embedding: 'Suche (Embeddings)' }

const deploymentMode = computed(() => health.value?.deployment_mode || '')
const appVersion = computed(() => {
  const v = health.value?.app_version
  return v && v !== 'unknown' ? v : ''
})

function componentLabel(name) {
  return COMPONENT_LABELS[name] || name
}

function statusText(comp) {
  if (comp.status === 'ok') return STATUS_LABELS.ok
  return comp.message || STATUS_LABELS[comp.status] || comp.status
}

async function copyText(text, success = 'Kopiert.') {
  if (await copyToClipboard(text)) notify.success(success)
  else notify.error('Kopieren nicht möglich — bitte den Text manuell markieren.')
}

// Diagnose-Hinweise passend zum Betriebsmodus (Docker-Befehle helfen in der
// Native-Installation nicht).
function chromaDiagnostics() {
  if (deploymentMode.value === 'docker') {
    return ['docker compose ps', 'docker compose logs chromadb --tail=50']
  }
  if (deploymentMode.value === 'native') {
    return ['sc query ZettelwirtschaftBackend', 'Log: %USERPROFILE%\\Documents\\Zettelwirtschaft\\logs\\backend.log']
  }
  return ['Backend-Log prüfen (Konsole von uvicorn)']
}

function copyChromaError(comp) {
  const text = [
    '## Vektor-Datenbank (ChromaDB) Fehler-Report',
    '',
    `**Status:** ${comp.status}`,
    `**Fehlermeldung:** ${comp.message || 'keine'}`,
    `**Betriebsmodus:** ${deploymentMode.value || 'unbekannt'}`,
    `**Version:** ${appVersion.value || 'unbekannt'}`,
    `**Zeitpunkt:** ${formatDateTime(new Date())}`,
    '',
    '**Zur Diagnose:**',
    '```',
    ...chromaDiagnostics(),
    '```',
  ].join('\n')
  copyText(text, 'In Zwischenablage kopiert.')
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
  if (status === 'warning') return 'bg-amber-500'
  return 'bg-red-500'
}

let healthTimer = null
let lastLoadedAt = 0

function startPolling() {
  stopPolling()
  healthTimer = setInterval(() => {
    lastLoadedAt = Date.now()
    loadHealth(true)
  }, POLL_INTERVAL_MS)
}

function stopPolling() {
  if (healthTimer) clearInterval(healthTimer)
  healthTimer = null
}

// Tab im Hintergrund: Polling pausieren; beim Zurueckkehren sofort
// aktualisieren, falls der letzte Stand aelter als ein Intervall ist.
function onVisibilityChange() {
  if (document.hidden) {
    stopPolling()
    return
  }
  if (Date.now() - lastLoadedAt >= POLL_INTERVAL_MS) {
    lastLoadedAt = Date.now()
    loadHealth(true)
  }
  startPolling()
}

onMounted(async () => {
  lastLoadedAt = Date.now()
  await loadHealth()
  if (!document.hidden) startPolling()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

// H-FE-4: defineExpose entfernt — Parent kommuniziert via @health-loaded Event.
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
        <span v-if="appVersion" class="text-xs font-mono text-gray-400 bg-gray-100 px-2 py-1 rounded" data-testid="app-version">
          v{{ appVersion }}
        </span>
      </div>
      <div v-if="installPath" class="flex flex-wrap items-center gap-2 text-xs text-gray-500">
        <svg class="w-3.5 h-3.5 text-gray-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
        <span>Installationsordner:</span>
        <code class="bg-gray-100 px-1.5 py-0.5 rounded font-mono text-gray-600 select-all break-all">{{ installPath }}</code>
        <button @click="copyText('explorer.exe &quot;' + installPath + '&quot;')" class="text-primary-600 hover:text-primary-700 underline" title="Explorer-Befehl kopieren">Ordner öffnen</button>
      </div>
    </div>

    <!-- Komponenten -->
    <div class="card">
      <h2 class="text-lg font-semibold text-gray-900 mb-4">Komponenten</h2>
      <div class="space-y-3">
        <div v-for="(comp, name) in health.components" :key="name" class="space-y-1" :data-testid="`component-${name}`">
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-center gap-2 min-w-0">
              <span :class="['h-2.5 w-2.5 flex-shrink-0 rounded-full', componentStatusClass(comp.status)]"></span>
              <span class="text-sm font-medium text-gray-700">{{ componentLabel(name) }}</span>
            </div>
            <div class="text-sm text-right">
              <span :class="comp.status === 'ok' ? 'text-green-600' : (comp.status === 'error' ? 'text-red-600' : 'text-amber-700')">
                {{ statusText(comp) }}
              </span>
            </div>
          </div>
          <!-- Konfigurierte Modelle: nur die benoetigten, mit Verfuegbarkeit -->
          <ul v-if="comp.configured_models?.length" class="ml-5 space-y-0.5 text-xs" data-testid="configured-models">
            <li v-for="m in comp.configured_models" :key="m.name" class="flex flex-wrap items-center gap-x-2">
              <code class="font-mono text-gray-600">{{ m.name }}</code>
              <span v-if="MODEL_PURPOSE_LABELS[m.purpose]" class="text-gray-400">{{ MODEL_PURPOSE_LABELS[m.purpose] }}</span>
              <span :class="m.available ? 'text-green-600' : 'text-red-600 font-medium'">
                {{ m.available ? 'installiert' : (m.downloading ? 'wird heruntergeladen' : 'fehlt') }}
              </span>
            </li>
          </ul>
          <p v-else-if="comp.missing?.length" class="ml-5 text-xs text-red-600">
            Fehlt: <code class="font-mono">{{ comp.missing.join(', ') }}</code>
          </p>
          <!-- ChromaDB Fehler-Hilfe -->
          <div v-if="name === 'chromadb' && comp.status !== 'ok'" class="ml-5 rounded-lg bg-red-50 border border-red-100 p-3 text-xs text-red-700 space-y-2">
            <p class="font-medium">Die Vektor-Datenbank ist nicht erreichbar. Der KI-Assistent ist dadurch deaktiviert.</p>
            <template v-if="deploymentMode === 'docker'">
              <p>Mögliche Ursachen und Lösungen:</p>
              <ul class="list-disc list-inside space-y-1 text-red-600">
                <li>ChromaDB-Container ist nicht gestartet &rarr; <code class="bg-red-100 px-1 rounded">docker compose up -d chromadb</code></li>
                <li>Status prüfen &rarr; <code class="bg-red-100 px-1 rounded">docker compose ps</code></li>
                <li>Neustart: <code class="bg-red-100 px-1 rounded">docker compose restart chromadb</code></li>
              </ul>
            </template>
            <template v-else-if="deploymentMode === 'native'">
              <p>Die Vektor-Datenbank läuft im Dienst „ZettelwirtschaftBackend“ mit. Starte den Dienst neu (Dienste-Verwaltung <code class="bg-red-100 px-1 rounded">services.msc</code>) und prüfe das Log unter <code class="bg-red-100 px-1 rounded">Dokumente\Zettelwirtschaft\logs\backend.log</code>.</p>
            </template>
            <p v-else>Prüfe die Ausgabe des Backends auf Fehlermeldungen der Vektor-Datenbank.</p>
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
