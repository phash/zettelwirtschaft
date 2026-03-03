<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getSystemHealth, createBackup, getBackups, optimizeDb, rebuildIndex, rebuildVectors, getFilingScopes, createFilingScope, updateFilingScope, deleteFilingScope, getSystemSettings, updateSystemSettings } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import StatCard from '../components/common/StatCard.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmailAccountList from '../components/email/EmailAccountList.vue'

const notify = useNotificationStore()
const emailList = ref(null)
const loading = ref(true)
const health = ref(null)
const backups = ref([])
const backingUp = ref(false)
const optimizing = ref(false)
const rebuilding = ref(false)
const rebuildingVectors = ref(false)
const installPath = ref('')

// Ordner-Einstellungen
const folderSettings = ref({ watch_dir: '', export_dir: '', watch_dir_host: '', export_dir_host: '' })
const savingFolders = ref(false)
const restartRequired = ref(false)

async function loadFolderSettings() {
  try {
    folderSettings.value = await getSystemSettings()
  } catch {
    // ignore
  }
}

async function saveFolderSettings() {
  savingFolders.value = true
  try {
    const result = await updateSystemSettings(folderSettings.value)
    folderSettings.value = result
    if (result.restart_required) {
      restartRequired.value = true
    }
    notify.success('Ordner gespeichert.')
  } catch (e) {
    notify.error(e.response?.data?.detail || 'Speichern fehlgeschlagen.')
  } finally {
    savingFolders.value = false
  }
}

function onPastePath(event, field) {
  const pasted = (event.clipboardData || window.clipboardData).getData('text')
  if (pasted) {
    // "Als Pfad kopieren" fuegt Anfuehrungszeichen hinzu -> entfernen
    const cleaned = pasted.trim().replace(/^"|"$/g, '')
    if (cleaned !== pasted) {
      event.preventDefault()
      folderSettings.value[field] = cleaned
    }
  }
}

function resetHostPaths() {
  folderSettings.value.watch_dir_host = ''
  folderSettings.value.export_dir_host = ''
  folderSettings.value.watch_dir = '/app/data/watch'
  folderSettings.value.export_dir = ''
}

// Filing Scopes
const scopes = ref([])
const scopeForm = ref({ name: '', description: '', keywords: '', is_default: false, color: '#3B82F6' })
const editingScopeId = ref(null)
const showScopeForm = ref(false)
const deletingScopeId = ref(null)

async function loadScopes() {
  try {
    scopes.value = await getFilingScopes()
  } catch {
    // ignore
  }
}

function startNewScope() {
  editingScopeId.value = null
  scopeForm.value = { name: '', description: '', keywords: '', is_default: false, color: '#3B82F6' }
  showScopeForm.value = true
}

function startEditScope(scope) {
  editingScopeId.value = scope.id
  scopeForm.value = {
    name: scope.name,
    description: scope.description || '',
    keywords: (scope.keywords || []).join(', '),
    is_default: scope.is_default,
    color: scope.color || '#3B82F6',
  }
  showScopeForm.value = true
}

async function saveScope() {
  const keywords = scopeForm.value.keywords
    .split(',')
    .map(k => k.trim())
    .filter(k => k)
  const payload = {
    name: scopeForm.value.name,
    description: scopeForm.value.description || null,
    keywords,
    is_default: scopeForm.value.is_default,
    color: scopeForm.value.color,
  }
  try {
    if (editingScopeId.value) {
      await updateFilingScope(editingScopeId.value, payload)
      notify.success('Ablagebereich aktualisiert.')
    } else {
      await createFilingScope(payload)
      notify.success('Ablagebereich erstellt.')
    }
    showScopeForm.value = false
    await loadScopes()
  } catch (e) {
    notify.error(e.response?.data?.detail || 'Fehler beim Speichern.')
  }
}

async function confirmDeleteScope() {
  try {
    await deleteFilingScope(deletingScopeId.value)
    notify.success('Ablagebereich geloescht.')
    deletingScopeId.value = null
    await loadScopes()
  } catch (e) {
    notify.error(e.response?.data?.detail || 'Loeschen fehlgeschlagen.')
    deletingScopeId.value = null
  }
}

function isWindowsPath(p) {
  if (!p) return false
  return /^[A-Za-z]:[\\\/]/.test(p)
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

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => notify.success('Kopiert.'))
}

async function loadHealth(isPolling = false) {
  if (!isPolling) loading.value = true
  try {
    health.value = await getSystemHealth()
    if (health.value.install_path) {
      installPath.value = health.value.install_path
    }
  } catch {
    if (!isPolling) notify.error('Systemstatus konnte nicht geladen werden.')
  } finally {
    if (!isPolling) loading.value = false
  }
}

async function loadBackups() {
  try {
    const data = await getBackups()
    backups.value = data.backups
  } catch {
    // ignore
  }
}

async function doBackup(full = false) {
  backingUp.value = true
  try {
    await createBackup(full)
    notify.success(full ? 'Vollbackup erstellt.' : 'Backup erstellt.')
    await loadBackups()
  } catch {
    notify.error('Backup fehlgeschlagen.')
  } finally {
    backingUp.value = false
  }
}

async function doOptimize() {
  optimizing.value = true
  try {
    await optimizeDb()
    notify.success('Datenbank optimiert.')
    await loadHealth()
  } catch {
    notify.error('Optimierung fehlgeschlagen.')
  } finally {
    optimizing.value = false
  }
}

async function doRebuildIndex() {
  rebuilding.value = true
  try {
    await rebuildIndex()
    notify.success('Suchindex neu aufgebaut.')
  } catch {
    notify.error('Index-Rebuild fehlgeschlagen.')
  } finally {
    rebuilding.value = false
  }
}

async function doRebuildVectors() {
  rebuildingVectors.value = true
  try {
    const result = await rebuildVectors()
    notify.success(result.message || 'Vektor-Index aufgebaut.')
    await loadHealth()
  } catch {
    notify.error('Vektor-Rebuild fehlgeschlagen. Ist ChromaDB erreichbar?')
  } finally {
    rebuildingVectors.value = false
  }
}

function formatBytes(bytes) {
  if (bytes == null || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let val = bytes
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024
    i++
  }
  return `${val.toFixed(1)} ${units[i]}`
}

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleString('de-DE')
}

function componentStatusClass(status) {
  if (status === 'ok') return 'bg-green-500'
  if (status === 'degraded' || status === 'offline') return 'bg-amber-500'
  return 'bg-red-500'
}

let healthTimer = null

onMounted(async () => {
  await Promise.all([loadHealth(), loadBackups(), loadScopes(), loadFolderSettings()])
  // Auto-Polling: Health-Status alle 10 Sekunden aktualisieren
  healthTimer = setInterval(() => loadHealth(true), 10000)
  // E-Mail-Konten laden (nach Mount, da Ref erst dann verfuegbar)
  emailList.value?.loadAccounts()
})

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-2xl font-bold text-gray-900">System</h1>

    <!-- Neustart-Banner -->
    <div v-if="restartRequired" class="rounded-lg border border-amber-300 bg-amber-50 p-4 flex items-start gap-3">
      <span class="text-amber-500 text-xl flex-shrink-0">!</span>
      <div>
        <p class="text-sm font-semibold text-amber-800">Neustart erforderlich</p>
        <p class="text-xs text-amber-700 mt-1">
          Die Host-Ordner wurden geaendert. Bitte das System stoppen und neu starten
          (<code class="bg-amber-100 px-1 rounded">stop.bat</code> &rarr; <code class="bg-amber-100 px-1 rounded">start.bat</code>),
          damit die Ordner im Container eingebunden werden.
        </p>
        <div v-if="installPath" class="mt-2 flex items-center gap-2">
          <span class="text-xs text-amber-700">Installationsordner:</span>
          <code class="bg-amber-100 px-2 py-0.5 rounded text-xs font-mono text-amber-900 select-all">{{ installPath }}</code>
          <button @click="copyText(installPath)" class="text-xs text-amber-700 underline hover:text-amber-900">Kopieren</button>
          <button @click="copyText('explorer.exe &quot;' + installPath + '&quot;')" class="text-xs text-amber-700 underline hover:text-amber-900">Explorer-Befehl</button>
        </div>
      </div>
    </div>

    <!-- Ordner-Konfiguration -->
    <div class="card">
      <h2 class="text-lg font-semibold text-gray-900 mb-4">Ordner</h2>
      <div class="space-y-4">
        <!-- Tipp: Pfad kopieren -->
        <div class="rounded-lg bg-blue-50 border border-blue-200 p-3 flex items-start gap-2">
          <svg class="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
          <p class="text-xs text-blue-700">
            <strong>Tipp:</strong> Ordner im Windows-Explorer mit <strong>Rechtsklick</strong> &rarr; <strong>Als Pfad kopieren</strong> auswaehlen, dann hier einfuegen (Strg+V). Anfuehrungszeichen werden automatisch entfernt.
          </p>
        </div>

        <!-- Watch-Ordner: Host-Pfad -->
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Eingangsordner (Watch-Ordner)</label>
          <p class="text-xs text-gray-500 mb-2">Windows-Ordner, der automatisch ueberwacht wird. Neue Dateien werden eingelesen.</p>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400 pointer-events-none">
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
            </span>
            <input v-model="folderSettings.watch_dir_host" @paste="onPastePath($event, 'watch_dir_host')" class="input font-mono text-sm pl-9" placeholder="Leer = Standard (data/watch), z.B. V:\Zettelwirtschaft\eingang" />
          </div>
          <p v-if="folderSettings.watch_dir_host" class="text-xs text-gray-500 mt-1">
            Container-Pfad: <code class="bg-gray-100 px-1 rounded font-mono">/app/external/watch</code> (wird automatisch gesetzt)
          </p>
          <p v-else class="text-xs text-gray-400 mt-1">
            Standard: <code class="bg-gray-100 px-1 rounded font-mono">{Installationsordner}/data/watch</code>
          </p>
        </div>

        <!-- Export-Ordner: Host-Pfad -->
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">Zielordner fuer verarbeitete Dokumente</label>
          <p class="text-xs text-gray-500 mb-2">Verarbeitete Dokumente werden zusaetzlich in diesen Windows-Ordner kopiert. Leer lassen zum Deaktivieren.</p>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400 pointer-events-none">
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
            </span>
            <input v-model="folderSettings.export_dir_host" @paste="onPastePath($event, 'export_dir_host')" class="input font-mono text-sm pl-9" placeholder="Leer = deaktiviert, z.B. V:\Zettelwirtschaft\fertig" />
          </div>
          <p v-if="folderSettings.export_dir_host" class="text-xs text-gray-500 mt-1">
            Container-Pfad: <code class="bg-gray-100 px-1 rounded font-mono">/app/external/export</code> (wird automatisch gesetzt)
          </p>
          <p v-else class="text-xs text-gray-400 mt-1">
            Kein Zielordner konfiguriert. Dokumente werden nur im Archiv gespeichert.
          </p>
        </div>

        <!-- Container-Pfade (nur anzeigen wenn kein Host-Pfad gesetzt, als Fallback fuer fortgeschrittene User) -->
        <details v-if="!folderSettings.watch_dir_host && !folderSettings.export_dir_host" class="text-xs">
          <summary class="text-gray-400 cursor-pointer hover:text-gray-600">Erweitert: Container-Pfade manuell setzen</summary>
          <div class="mt-2 space-y-3 pl-2 border-l-2 border-gray-200">
            <div>
              <label class="block text-xs font-medium text-gray-500 mb-1">Watch-Ordner (Container-Pfad)</label>
              <input v-model="folderSettings.watch_dir" class="input font-mono text-sm" placeholder="/app/data/watch" />
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-500 mb-1">Export-Ordner (Container-Pfad)</label>
              <input v-model="folderSettings.export_dir" class="input font-mono text-sm" placeholder="Leer = deaktiviert" />
            </div>
          </div>
        </details>

        <div class="flex gap-2">
          <button @click="saveFolderSettings" :disabled="savingFolders" class="btn-primary">
            {{ savingFolders ? 'Speichere...' : 'Speichern' }}
          </button>
          <button v-if="folderSettings.watch_dir_host || folderSettings.export_dir_host" @click="resetHostPaths" class="btn-secondary text-sm">
            Auf Standard zuruecksetzen
          </button>
        </div>
      </div>
    </div>

    <!-- Ablagebereiche -->
    <div class="card">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-gray-900">Ablagebereiche</h2>
        <button @click="startNewScope" class="btn-primary text-sm">+ Neuer Bereich</button>
      </div>

      <!-- Scope-Liste -->
      <div class="space-y-3">
        <div v-for="scope in scopes" :key="scope.id" class="flex items-center justify-between rounded-lg border border-gray-200 p-3">
          <div class="flex items-center gap-3">
            <span class="h-3 w-3 rounded-full flex-shrink-0" :style="{ backgroundColor: scope.color || '#6B7280' }"></span>
            <div>
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-gray-900">{{ scope.name }}</span>
                <span v-if="scope.is_default" class="badge bg-primary-100 text-primary-700 text-xs">Standard</span>
              </div>
              <p v-if="scope.description" class="text-xs text-gray-500">{{ scope.description }}</p>
              <div v-if="scope.keywords?.length" class="mt-1 flex flex-wrap gap-1">
                <span v-for="kw in scope.keywords" :key="kw" class="badge bg-gray-100 text-gray-600 text-xs">{{ kw }}</span>
              </div>
            </div>
          </div>
          <div class="flex gap-2">
            <button @click="startEditScope(scope)" class="text-sm text-primary-600 hover:text-primary-700">Bearbeiten</button>
            <button v-if="!scope.is_default" @click="deletingScopeId = scope.id" class="text-sm text-red-500 hover:text-red-700">Loeschen</button>
          </div>
        </div>
        <div v-if="scopes.length === 0" class="text-sm text-gray-400">Keine Ablagebereiche konfiguriert.</div>
      </div>

      <!-- Inline-Formular -->
      <div v-if="showScopeForm" class="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-3">
        <h3 class="text-sm font-semibold text-gray-900">
          {{ editingScopeId ? 'Ablagebereich bearbeiten' : 'Neuer Ablagebereich' }}
        </h3>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Name</label>
          <input v-model="scopeForm.name" class="input" placeholder="z.B. Praxis Dr. Mueller" />
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Beschreibung</label>
          <input v-model="scopeForm.description" class="input" placeholder="Optional" />
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 mb-1">Schluesselwoerter (kommagetrennt)</label>
          <input v-model="scopeForm.keywords" class="input" placeholder="z.B. KBV, Kassenaerztliche, Praxis" />
        </div>
        <div class="flex items-center gap-4">
          <div class="flex items-center gap-2">
            <label class="block text-xs font-medium text-gray-500">Farbe</label>
            <input v-model="scopeForm.color" type="color" class="h-8 w-8 rounded border border-gray-300 cursor-pointer" />
          </div>
          <label class="flex items-center gap-2">
            <input type="checkbox" v-model="scopeForm.is_default" class="rounded border-gray-300" />
            <span class="text-sm text-gray-600">Standard-Bereich</span>
          </label>
        </div>
        <div class="flex gap-2">
          <button @click="saveScope" class="btn-primary text-sm">Speichern</button>
          <button @click="showScopeForm = false" class="btn-secondary text-sm">Abbrechen</button>
        </div>
      </div>
    </div>

    <ConfirmDialog
      :show="!!deletingScopeId"
      title="Ablagebereich loeschen"
      message="Soll dieser Ablagebereich wirklich geloescht werden? Zugeordnete Dokumente werden dem Standard-Bereich zugewiesen."
      confirm-text="Loeschen"
      :danger="true"
      @confirm="confirmDeleteScope"
      @cancel="deletingScopeId = null"
    />

    <!-- E-Mail-Konten -->
    <EmailAccountList :scopes="scopes" ref="emailList" />

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
              System {{ health.status === 'ok' ? 'betriebsbereit' : 'eingeschraenkt' }}
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
          <button @click="copyText('explorer.exe &quot;' + installPath + '&quot;')" class="text-primary-600 hover:text-primary-700 underline" title="Explorer-Befehl kopieren">Ordner oeffnen</button>
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
              <p>Moegliche Ursachen und Loesungen:</p>
              <ul class="list-disc list-inside space-y-1 text-red-600">
                <li>ChromaDB-Container ist nicht gestartet → <code class="bg-red-100 px-1 rounded">docker compose up -d chromadb</code></li>
                <li>Port-Konflikt → <code class="bg-red-100 px-1 rounded">docker compose ps</code> pruefen</li>
                <li>Neustart: <code class="bg-red-100 px-1 rounded">docker compose restart chromadb</code></li>
              </ul>
              <button
                @click="copyChromaError(comp)"
                class="mt-1 flex items-center gap-1 text-xs text-red-600 hover:text-red-800 underline"
              >
                Fehler fuer Issue-Report kopieren
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

      <!-- Wartung -->
      <div class="card">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Wartung</h2>
        <div class="flex flex-wrap gap-3">
          <button @click="doOptimize" :disabled="optimizing" class="btn-secondary">
            {{ optimizing ? 'Optimiere...' : 'Datenbank optimieren' }}
          </button>
          <button @click="doRebuildIndex" :disabled="rebuilding" class="btn-secondary">
            {{ rebuilding ? 'Baue auf...' : 'Suchindex neu aufbauen' }}
          </button>
          <button @click="doRebuildVectors" :disabled="rebuildingVectors" class="btn-secondary">
            {{ rebuildingVectors ? 'Vektorisiere...' : 'Vektor-Index aufbauen' }}
          </button>
        </div>
      </div>

      <!-- Backups -->
      <div class="card">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold text-gray-900">Backups</h2>
          <div class="flex gap-2">
            <button @click="doBackup(false)" :disabled="backingUp" class="btn-secondary text-sm">
              {{ backingUp ? 'Erstelle...' : 'Backup (DB)' }}
            </button>
            <button @click="doBackup(true)" :disabled="backingUp" class="btn-primary text-sm">
              Vollbackup
            </button>
          </div>
        </div>
        <div v-if="backups.length === 0" class="text-sm text-gray-400">Keine Backups vorhanden.</div>
        <div v-else class="space-y-2">
          <div v-for="b in backups" :key="b.filename" class="flex items-center justify-between text-sm">
            <div>
              <span class="text-gray-700">{{ b.filename }}</span>
              <span class="text-gray-400 ml-2">{{ formatBytes(b.size_bytes) }}</span>
            </div>
            <div class="flex items-center gap-2 text-gray-500">
              <span>{{ formatDate(b.created_at) }}</span>
              <a :href="`/api/system/backups/${b.filename}`" class="text-primary-600 hover:text-primary-700">
                Download
              </a>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
