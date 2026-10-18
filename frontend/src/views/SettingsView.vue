<script setup>
import { ref } from 'vue'
import { useNotificationStore } from '../stores/notifications'
import FolderSettings from '../components/settings/FolderSettings.vue'
import FilingScopeManager from '../components/settings/FilingScopeManager.vue'
import SystemHealth from '../components/settings/SystemHealth.vue'
import UpdateCheck from '../components/settings/UpdateCheck.vue'
import MaintenanceActions from '../components/settings/MaintenanceActions.vue'
import EmailSettings from '../components/settings/EmailSettings.vue'
import SplitSettings from '../components/settings/SplitSettings.vue'
import { copyToClipboard } from '../utils/clipboard'

// H-FE-4: Kindkomponenten laden selbst (onMounted in jeder Komponente).
// Vorher: SettingsView via Refs + defineExpose — fragil bei Hot-Reload und
// Mount-Order-Race-Conditions.

const notify = useNotificationStore()

const restartRequired = ref(false)
const installPath = ref('')

function onHealthLoaded(health) {
  if (health.install_path) {
    installPath.value = health.install_path
  }
}

async function copyText(text) {
  if (await copyToClipboard(text)) notify.success('Kopiert.')
  else notify.error('Kopieren nicht möglich — bitte den Text manuell markieren.')
}
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-2xl font-bold text-gray-900">System</h1>

    <!-- Neustart-Banner (nur Docker: Host-Ordner werden per Bind-Mount beim
         Start eingebunden; FolderSettings meldet restart-required nur dort) -->
    <div v-if="restartRequired" class="rounded-lg border border-amber-300 bg-amber-50 p-4 flex items-start gap-3">
      <span class="text-amber-500 text-xl flex-shrink-0">!</span>
      <div>
        <p class="text-sm font-semibold text-amber-800">Neustart erforderlich</p>
        <p class="text-xs text-amber-700 mt-1">
          Die Host-Ordner wurden geändert. Bitte das System stoppen und neu starten
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
    <FolderSettings @restart-required="restartRequired = true" />

    <!-- Ablagebereiche -->
    <FilingScopeManager />

    <!-- E-Mail-Konten -->
    <EmailSettings />

    <!-- Verarbeitung: automatische Prüfung von Stapel-Scans -->
    <SplitSettings />

    <!-- System Health + Komponenten + Speicher -->
    <SystemHealth @health-loaded="onHealthLoaded" />

    <!-- Update-Prüfung (opt-in) -->
    <UpdateCheck />

    <!-- Wartung + Backups -->
    <MaintenanceActions />
  </div>
</template>
