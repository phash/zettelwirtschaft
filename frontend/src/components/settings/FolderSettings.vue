<script setup>
import { ref, computed, onMounted } from 'vue'
import { getSystemSettings, updateSystemSettings } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { apiErrorMessage } from '../../utils/apiErrors'

const notify = useNotificationStore()

const folderSettings = ref({ watch_dir: '', export_dir: '', watch_dir_host: '', export_dir_host: '', deployment_mode: '' })
const savingFolders = ref(false)

const emit = defineEmits(['restart-required'])

const PATH_FIELDS = ['watch_dir', 'export_dir', 'watch_dir_host', 'export_dir_host']

// Betriebsmodus kommt vom Backend (settings.deployment_mode). Nur im Docker-
// Modus gibt es Host-Pfade, die per Bind-Mount in den Container gelangen; in
// Native/Dev ist der eingegebene Pfad direkt der Ordner, den das Backend nutzt.
// Fehlt das Feld (altes Backend), bleibt das bisherige Docker-Verhalten.
const isDocker = computed(() => (folderSettings.value.deployment_mode || 'docker') === 'docker')

// Feldnamen je Modus: Docker bearbeitet die Host-Pfade, Native/Dev direkt
// watch_dir/export_dir.
const watchField = computed(() => (isDocker.value ? 'watch_dir_host' : 'watch_dir'))
const exportField = computed(() => (isDocker.value ? 'export_dir_host' : 'export_dir'))

const hasCustomPaths = computed(() =>
  !!(folderSettings.value[watchField.value] || folderSettings.value[exportField.value])
)

// "Als Pfad kopieren" im Explorer liefert "C:\Ordner" mit Anführungszeichen;
// auch getippte/automatisch ausgefüllte Werte werden so bereinigt.
function normalizePath(value) {
  if (typeof value !== 'string') return ''
  return value.replace(/^["'\s]+|["'\s]+$/g, '')
}

function normalizeField(field) {
  folderSettings.value[field] = normalizePath(folderSettings.value[field])
}

async function loadFolderSettings() {
  try {
    folderSettings.value = { ...folderSettings.value, ...(await getSystemSettings()) }
  } catch {
    // ignore
  }
}

async function saveFolderSettings() {
  PATH_FIELDS.forEach(normalizeField)
  savingFolders.value = true
  try {
    const { deployment_mode: _mode, ...payload } = folderSettings.value
    const result = await updateSystemSettings(payload)
    folderSettings.value = { ...folderSettings.value, ...result }
    if (isDocker.value && result.restart_required) {
      emit('restart-required')
    }
    notify.success('Ordner gespeichert.')
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Speichern fehlgeschlagen.'))
  } finally {
    savingFolders.value = false
  }
}

function onPastePath(event, field) {
  const pasted = (event.clipboardData || window.clipboardData).getData('text')
  if (pasted) {
    const cleaned = normalizePath(pasted)
    if (cleaned !== pasted) {
      event.preventDefault()
      folderSettings.value[field] = cleaned
    }
  }
}

// Leere Werte = Backend-Standard (das Backend setzt dann den Standard-
// Eingangsordner der Installation ein) — kein hart codierter Container-Pfad.
function resetToDefault() {
  PATH_FIELDS.forEach((f) => { folderSettings.value[f] = '' })
}

// H-FE-4: Komponente laedt selbst, kein defineExpose-Pattern mehr.
onMounted(() => {
  loadFolderSettings()
})
</script>

<template>
  <div class="card">
    <h2 class="text-lg font-semibold text-gray-900 mb-4">Ordner</h2>
    <div class="space-y-4">
      <!-- Tipp: Pfad kopieren -->
      <div class="rounded-lg bg-blue-50 border border-blue-200 p-3 flex items-start gap-2">
        <svg class="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
        <p class="text-xs text-blue-700">
          <strong>Tipp:</strong> Ordner im Windows-Explorer mit <strong>Rechtsklick</strong> &rarr; <strong>Als Pfad kopieren</strong> auswählen, dann hier einfügen (Strg+V). Anführungszeichen und Leerzeichen am Rand werden automatisch entfernt.
        </p>
      </div>

      <!-- Eingangsordner -->
      <div>
        <label for="folder-watch" class="block text-sm font-medium text-gray-700 mb-1">Eingangsordner (Watch-Ordner)</label>
        <p id="folder-watch-help" class="text-xs text-gray-500 mb-2">
          Ordner, der automatisch überwacht wird. Neue Dateien werden eingelesen.
          Aus einem eigenen Ordner werden verarbeitete Dateien in den Unterordner „verarbeitet“ verschoben – gelöscht wird dort nichts.
          Nur im Standard-Eingangsordner wird die Datei nach dem Import entfernt (das Original liegt dann im Archiv).
        </p>
        <div class="relative">
          <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400 pointer-events-none">
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
          </span>
          <input
            id="folder-watch"
            v-model="folderSettings[watchField]"
            @paste="onPastePath($event, watchField)"
            @blur="normalizeField(watchField)"
            aria-describedby="folder-watch-help"
            class="input font-mono text-sm pl-9"
            :placeholder="isDocker ? 'Leer = Standard (data/watch), z.B. V:\\Zettelwirtschaft\\eingang' : 'Leer = Standard-Eingangsordner, z.B. D:\\Scans\\Eingang'"
          />
        </div>
        <template v-if="isDocker">
          <p v-if="folderSettings.watch_dir_host" class="text-xs text-gray-500 mt-1">
            Container-Pfad: <code class="bg-gray-100 px-1 rounded font-mono">/app/external/watch</code> (wird automatisch gesetzt)
          </p>
          <p v-else class="text-xs text-gray-400 mt-1">
            Standard: <code class="bg-gray-100 px-1 rounded font-mono">{Installationsordner}/data/watch</code>
          </p>
        </template>
        <p v-else-if="!folderSettings.watch_dir" class="text-xs text-gray-400 mt-1">
          Kein eigener Ordner gesetzt — der Standard-Eingangsordner im Datenordner wird überwacht.
        </p>
      </div>

      <!-- Zielordner -->
      <div>
        <label for="folder-export" class="block text-sm font-medium text-gray-700 mb-1">Zielordner für verarbeitete Dokumente</label>
        <p id="folder-export-help" class="text-xs text-gray-500 mb-2">Verarbeitete Dokumente werden zusätzlich in diesen Ordner kopiert. Leer lassen zum Deaktivieren.</p>
        <div class="relative">
          <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400 pointer-events-none">
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
          </span>
          <input
            id="folder-export"
            v-model="folderSettings[exportField]"
            @paste="onPastePath($event, exportField)"
            @blur="normalizeField(exportField)"
            aria-describedby="folder-export-help"
            class="input font-mono text-sm pl-9"
            placeholder="Leer = deaktiviert, z.B. V:\Zettelwirtschaft\fertig"
          />
        </div>
        <p v-if="isDocker && folderSettings.export_dir_host" class="text-xs text-gray-500 mt-1">
          Container-Pfad: <code class="bg-gray-100 px-1 rounded font-mono">/app/external/export</code> (wird automatisch gesetzt)
        </p>
        <p v-else-if="!folderSettings[exportField]" class="text-xs text-gray-400 mt-1">
          Kein Zielordner konfiguriert. Dokumente werden nur im Archiv gespeichert.
        </p>
      </div>

      <!-- Container-Pfade (nur Docker, nur ohne Host-Pfad, für fortgeschrittene User) -->
      <details v-if="isDocker && !folderSettings.watch_dir_host && !folderSettings.export_dir_host" class="text-xs" data-testid="folder-advanced">
        <summary class="text-gray-400 cursor-pointer hover:text-gray-600">Erweitert: Container-Pfade manuell setzen</summary>
        <div class="mt-2 space-y-3 pl-2 border-l-2 border-gray-200">
          <div>
            <label for="folder-watch-container" class="block text-xs font-medium text-gray-500 mb-1">Watch-Ordner (Container-Pfad)</label>
            <input id="folder-watch-container" v-model="folderSettings.watch_dir" @blur="normalizeField('watch_dir')" class="input font-mono text-sm" placeholder="/app/data/watch" />
          </div>
          <div>
            <label for="folder-export-container" class="block text-xs font-medium text-gray-500 mb-1">Export-Ordner (Container-Pfad)</label>
            <input id="folder-export-container" v-model="folderSettings.export_dir" @blur="normalizeField('export_dir')" class="input font-mono text-sm" placeholder="Leer = deaktiviert" />
          </div>
        </div>
      </details>

      <div class="flex gap-2">
        <button @click="saveFolderSettings" :disabled="savingFolders" class="btn-primary">
          {{ savingFolders ? 'Speichere...' : 'Speichern' }}
        </button>
        <button v-if="hasCustomPaths" @click="resetToDefault" class="btn-secondary text-sm">
          Auf Standard zurücksetzen
        </button>
      </div>
    </div>
  </div>
</template>
