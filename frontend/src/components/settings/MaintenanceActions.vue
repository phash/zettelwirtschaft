<script setup>
import { ref } from 'vue'
import { createBackup, getBackups, optimizeDb, rebuildIndex, rebuildVectors } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { formatBytes, formatDate } from '../../utils/formatters'

const notify = useNotificationStore()
const backups = ref([])
const backingUp = ref(false)
const optimizing = ref(false)
const rebuilding = ref(false)
const rebuildingVectors = ref(false)

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
  } catch {
    notify.error('Vektor-Rebuild fehlgeschlagen. Ist ChromaDB erreichbar?')
  } finally {
    rebuildingVectors.value = false
  }
}

function formatBackupDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleString('de-DE')
}

defineExpose({ loadBackups })
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
          <span>{{ formatBackupDate(b.created_at) }}</span>
          <a :href="`/api/system/backups/${b.filename}`" class="text-primary-600 hover:text-primary-700">
            Download
          </a>
        </div>
      </div>
    </div>
  </div>
</template>
