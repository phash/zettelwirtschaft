<script setup>
import { ref, onMounted } from 'vue'
import {
  createBackup, getBackups, backupDownloadUrl, optimizeDb, rebuildIndex, rebuildVectors,
} from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { useBackgroundTask } from '../../composables/useBackgroundTask'
import { formatBytes, formatDateTime } from '../../utils/formatters'
import { apiErrorMessage } from '../../utils/apiErrors'
import { pluralize } from '../../utils/pluralize'

const notify = useNotificationStore()
const backups = ref([])
const optimizing = ref(false)
const rebuilding = ref(false)

async function loadBackups() {
  try {
    const data = await getBackups()
    backups.value = Array.isArray(data?.backups) ? data.backups : []
  } catch {
    // ignore
  }
}

// --- Backups: Hintergrund-Vorgang (kind "backup", key "global") ----------
// Das Vollbackup packt alle Originaldokumente und dauert entsprechend; der
// Start-Endpoint antwortet mit 202 + Task, das Ergebnis ist
// { filename, is_full, size_bytes, message }.
const backupFull = ref(false)
const backup = useBackgroundTask({
  onSuccess: (result) => {
    const name = result?.filename ? ` (${result.filename})` : ''
    notify.success(`${result?.is_full ? 'Vollbackup' : 'Backup'} erstellt${name}.`)
    loadBackups()
  },
  onError: (message) => notify.error(message || 'Backup fehlgeschlagen.'),
})

function doBackup(full = false) {
  backupFull.value = full
  backup.start(() => createBackup(full))
}

async function doOptimize() {
  optimizing.value = true
  try {
    await optimizeDb()
    notify.success('Datenbank optimiert.')
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Optimierung fehlgeschlagen.'))
  } finally {
    optimizing.value = false
  }
}

async function doRebuildIndex() {
  rebuilding.value = true
  try {
    await rebuildIndex()
    notify.success('Suchindex neu aufgebaut.')
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Index-Rebuild fehlgeschlagen.'))
  } finally {
    rebuilding.value = false
  }
}

// --- Vektor-Index: Hintergrund-Vorgang (kind "rebuild_vectors", key "global")
// Auch die initiale Vektorisierung beim Start laeuft unter diesem Vorgang —
// ein laufender Rebuild wird beim Oeffnen der Seite wieder angezeigt.
const vectors = useBackgroundTask({
  onSuccess: (result) => {
    const chunks = pluralize(result?.chunks || 0, 'Abschnitt', 'Abschnitte')
    const docs = pluralize(result?.processed || 0, 'Dokument', 'Dokumenten')
    if (result?.failed) {
      notify.error(`Vektor-Index aufgebaut, aber ${pluralize(result.failed, 'Dokument', 'Dokumente')} fehlgeschlagen (${chunks} aus ${docs}).`)
    } else {
      notify.success(`Vektor-Index aufgebaut: ${chunks} aus ${docs}.`)
    }
  },
  onError: (message) => notify.error(`Vektor-Index konnte nicht aufgebaut werden: ${message || 'Unbekannter Fehler'}`),
})

function doRebuildVectors() {
  vectors.start(() => rebuildVectors())
}

// H-FE-4: Komponente laedt selbst.
onMounted(() => {
  loadBackups()
  backup.resume({ kind: 'backup', key: 'global' })
  vectors.resume({ kind: 'rebuild_vectors', key: 'global' })
})
</script>

<template>
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
      <button @click="doRebuildVectors" :disabled="vectors.running.value" class="btn-secondary">
        {{ vectors.running.value ? 'Vektorisiere...' : 'Vektor-Index aufbauen' }}
      </button>
    </div>
    <p v-if="vectors.running.value" class="mt-3 text-xs text-gray-500" role="status" data-testid="vector-progress">
      Vektor-Index: {{ vectors.progressText.value || 'wird im Hintergrund aufgebaut …' }}
      <template v-if="vectors.progress.value != null">({{ vectors.progress.value }} %)</template>
    </p>
  </div>

  <!-- Backups -->
  <div class="card">
    <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
      <h2 class="text-lg font-semibold text-gray-900">Backups</h2>
      <div class="flex gap-2">
        <button @click="doBackup(false)" :disabled="backup.running.value" class="btn-secondary text-sm">
          {{ backup.running.value && !backupFull ? 'Erstelle...' : 'Backup (DB)' }}
        </button>
        <button @click="doBackup(true)" :disabled="backup.running.value" class="btn-primary text-sm">
          {{ backup.running.value && backupFull ? 'Erstelle...' : 'Vollbackup' }}
        </button>
      </div>
    </div>
    <p v-if="backup.running.value" class="mb-3 text-xs text-gray-500" role="status" data-testid="backup-progress">
      {{ backup.progressText.value || 'Backup läuft im Hintergrund …' }}
      <template v-if="backup.progress.value != null">({{ backup.progress.value }} %)</template>
    </p>
    <p class="mb-3 text-xs text-gray-400">„Backup (DB)“ sichert die Datenbank, „Vollbackup“ zusätzlich alle Originaldokumente.</p>
    <div v-if="backups.length === 0" class="text-sm text-gray-400">Keine Backups vorhanden.</div>
    <div v-else class="space-y-2">
      <div v-for="b in backups" :key="b.filename" class="flex flex-wrap items-center justify-between gap-2 text-sm">
        <div class="min-w-0">
          <span class="text-gray-700 break-all">{{ b.filename }}</span>
          <span class="text-gray-400 ml-2">{{ formatBytes(b.size_bytes) }}</span>
        </div>
        <div class="flex items-center gap-2 text-gray-500">
          <span>{{ formatDateTime(b.created_at) }}</span>
          <a :href="backupDownloadUrl(b.filename)" class="text-primary-600 hover:text-primary-700">
            Download
          </a>
        </div>
      </div>
    </div>
  </div>
</template>
