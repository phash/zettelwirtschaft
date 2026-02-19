<script setup>
import { ref, onMounted } from 'vue'
import { getSystemHealth, createBackup, getBackups, optimizeDb, rebuildIndex, getFilingScopes, createFilingScope, updateFilingScope, deleteFilingScope } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import StatCard from '../components/common/StatCard.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'

const notify = useNotificationStore()
const loading = ref(true)
const health = ref(null)
const backups = ref([])
const backingUp = ref(false)
const optimizing = ref(false)
const rebuilding = ref(false)

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

async function loadHealth() {
  loading.value = true
  try {
    health.value = await getSystemHealth()
  } catch {
    notify.error('Systemstatus konnte nicht geladen werden.')
  } finally {
    loading.value = false
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

onMounted(async () => {
  await Promise.all([loadHealth(), loadBackups(), loadScopes()])
})
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-2xl font-bold text-gray-900">System</h1>

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

    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-16">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
    </div>

    <template v-else-if="health">
      <!-- Gesamtstatus -->
      <div class="card !p-4 flex items-center gap-3">
        <span :class="['h-3 w-3 rounded-full', componentStatusClass(health.status)]"></span>
        <span class="text-sm font-medium text-gray-900">
          System {{ health.status === 'ok' ? 'betriebsbereit' : 'eingeschraenkt' }}
        </span>
      </div>

      <!-- Komponenten -->
      <div class="card">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Komponenten</h2>
        <div class="space-y-3">
          <div v-for="(comp, name) in health.components" :key="name" class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span :class="['h-2.5 w-2.5 rounded-full', componentStatusClass(comp.status)]"></span>
              <span class="text-sm font-medium text-gray-700 capitalize">{{ name }}</span>
            </div>
            <div class="text-sm text-gray-500">
              <span v-if="comp.status === 'ok'" class="text-green-600">OK</span>
              <span v-else>{{ comp.message || comp.status }}</span>
              <span v-if="comp.models" class="ml-2 text-xs text-gray-400">
                ({{ comp.models.join(', ') }})
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Speicher -->
      <div class="card">
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
