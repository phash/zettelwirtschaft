<script setup>
import { ref, onMounted } from 'vue'
import { useNotificationStore } from '../stores/notifications'
import FolderSettings from '../components/settings/FolderSettings.vue'
import FilingScopeManager from '../components/settings/FilingScopeManager.vue'
import SystemHealth from '../components/settings/SystemHealth.vue'
import MaintenanceActions from '../components/settings/MaintenanceActions.vue'
import EmailSettings from '../components/settings/EmailSettings.vue'

const notify = useNotificationStore()

const folderSettingsRef = ref(null)
const filingScopeRef = ref(null)
const systemHealthRef = ref(null)
const maintenanceRef = ref(null)
const emailSettingsRef = ref(null)

const restartRequired = ref(false)
const installPath = ref('')

function onHealthLoaded(health) {
  if (health.install_path) {
    installPath.value = health.install_path
  }
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => notify.success('Kopiert.'))
}

onMounted(async () => {
  await Promise.all([
    folderSettingsRef.value?.loadFolderSettings(),
    filingScopeRef.value?.loadScopes(),
    maintenanceRef.value?.loadBackups(),
  ])
  emailSettingsRef.value?.loadAccounts()
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
    <FolderSettings
      ref="folderSettingsRef"
      :install-path="installPath"
      @restart-required="restartRequired = true"
    />

    <!-- Ablagebereiche -->
    <FilingScopeManager ref="filingScopeRef" />

    <!-- E-Mail-Konten -->
    <EmailSettings
      ref="emailSettingsRef"
      :scopes="filingScopeRef?.scopes || []"
    />

    <!-- System Health + Komponenten + Speicher -->
    <SystemHealth ref="systemHealthRef" @health-loaded="onHealthLoaded" />

    <!-- Wartung + Backups -->
    <MaintenanceActions ref="maintenanceRef" />
  </div>
</template>
