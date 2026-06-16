<script setup>
import { ref, onMounted } from 'vue'
import { getUpdateCheck, setUpdateCheckEnabled } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'

const notify = useNotificationStore()
const loading = ref(true)
const busy = ref(false)
const status = ref(null)

async function load() {
  loading.value = true
  try {
    status.value = await getUpdateCheck(false)
  } catch {
    // Opt-in-Feature: bei Fehler nicht nerven
  } finally {
    loading.value = false
  }
}

async function toggle(enabled) {
  busy.value = true
  try {
    status.value = await setUpdateCheckEnabled(enabled)
    if (enabled && status.value?.error) notify.error('Update-Server nicht erreichbar.')
    else if (enabled) notify.success('Update-Prüfung aktiviert.')
    else notify.success('Update-Prüfung deaktiviert.')
  } catch {
    notify.error('Einstellung konnte nicht gespeichert werden.')
  } finally {
    busy.value = false
  }
}

async function checkNow() {
  busy.value = true
  try {
    status.value = await getUpdateCheck(true)
    if (status.value?.error) notify.error('Update-Server nicht erreichbar.')
    else if (status.value?.update_available) notify.success(`Update verfügbar: ${status.value.latest_version}`)
    else notify.success('Du verwendest die aktuelle Version.')
  } catch {
    notify.error('Prüfung fehlgeschlagen.')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="card space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-gray-900">Updates</h2>
      <span v-if="status" class="badge">Installiert: {{ status.current_version }}</span>
    </div>

    <label class="flex items-start gap-3 cursor-pointer">
      <input
        type="checkbox"
        class="mt-1 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
        :checked="status?.enabled"
        :disabled="busy || loading"
        @change="toggle($event.target.checked)"
      />
      <span class="text-sm text-gray-700">
        <span class="font-medium text-gray-900">Automatisch auf Updates prüfen</span><br />
        Vergleicht die installierte Version mit
        <code class="bg-gray-100 px-1 rounded">zettelwirtschaft.mr-development.de</code>.
        Dabei wird nur diese Abfrage gesendet (deine IP-Adresse) — keine Dokument- oder
        Nutzungsdaten. Standardmäßig deaktiviert.
      </span>
    </label>

    <template v-if="status?.enabled">
      <!-- Update verfügbar -->
      <div
        v-if="status.update_available"
        class="rounded-lg border border-emerald-300 bg-emerald-50 p-4 space-y-2"
      >
        <p class="text-sm font-semibold text-emerald-800">
          Update verfügbar: Version {{ status.latest_version }}
          <span v-if="status.published_at" class="font-normal text-emerald-700">
            ({{ status.published_at }})
          </span>
        </p>
        <p v-if="status.notes" class="text-xs text-emerald-700">{{ status.notes }}</p>
        <a
          v-if="status.release_url"
          :href="status.release_url"
          target="_blank"
          rel="noopener noreferrer"
          class="btn-primary inline-block text-sm"
        >Zum Download &rarr;</a>
      </div>

      <!-- Fehler -->
      <div v-else-if="status.error" class="rounded-lg border border-amber-300 bg-amber-50 p-3">
        <p class="text-sm text-amber-800">{{ status.error }}</p>
      </div>

      <!-- aktuell -->
      <div v-else class="rounded-lg border border-gray-200 bg-gray-50 p-3">
        <p class="text-sm text-gray-700">
          Du verwendest die aktuelle Version<span v-if="status.latest_version"> ({{ status.latest_version }})</span>.
        </p>
      </div>

      <button @click="checkNow" :disabled="busy" class="btn-secondary text-sm">Jetzt prüfen</button>
    </template>
  </div>
</template>
