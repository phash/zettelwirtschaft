<script setup>
import { ref, onMounted } from 'vue'
import { getSystemSettings, updateSystemSettings } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'

const notify = useNotificationStore()

const folderSettings = ref({ watch_dir: '', export_dir: '', watch_dir_host: '', export_dir_host: '' })
const savingFolders = ref(false)

const emit = defineEmits(['restart-required'])

defineProps({
  installPath: { type: String, default: '' },
})

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
      emit('restart-required')
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
        <svg class="w-4 h-4 text-blue-500 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
        <p class="text-xs text-blue-700">
          <strong>Tipp:</strong> Ordner im Windows-Explorer mit <strong>Rechtsklick</strong> &rarr; <strong>Als Pfad kopieren</strong> auswählen, dann hier einfügen (Strg+V). Anführungszeichen werden automatisch entfernt.
        </p>
      </div>

      <!-- Watch-Ordner: Host-Pfad -->
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">Eingangsordner (Watch-Ordner)</label>
        <p class="text-xs text-gray-500 mb-2">Windows-Ordner, der automatisch überwacht wird. Neue Dateien werden eingelesen.</p>
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
        <label class="block text-sm font-medium text-gray-700 mb-1">Zielordner für verarbeitete Dokumente</label>
        <p class="text-xs text-gray-500 mb-2">Verarbeitete Dokumente werden zusätzlich in diesen Windows-Ordner kopiert. Leer lassen zum Deaktivieren.</p>
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

      <!-- Container-Pfade (nur anzeigen wenn kein Host-Pfad gesetzt, als Fallback für fortgeschrittene User) -->
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
          Auf Standard zurücksetzen
        </button>
      </div>
    </div>
  </div>
</template>
