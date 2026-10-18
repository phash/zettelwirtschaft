<script setup>
import { ref, computed, onMounted } from 'vue'
import { getSystemSettings, updateSystemSettings, testFolderSettings } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { apiErrorMessage } from '../../utils/apiErrors'
import { copyToClipboard } from '../../utils/clipboard'

const notify = useNotificationStore()

const folderSettings = ref({
  watch_dir: '',
  export_dir: '',
  watch_dir_host: '',
  export_dir_host: '',
  deployment_mode: '',
  watch_dir_net_user: '',
  export_dir_net_user: '',
  watch_dir_net_password_set: false,
  export_dir_net_password_set: false,
  watch_poll_interval: 30,
  network_folders_supported: false,
  scanner_share: null,
})
// Passwoerter sind write-only: nie vom Backend vorbefuellt, leer = unveraendert.
const passwords = ref({ watch: '', export: '' })
const testResults = ref({ watch: null, export: null })
const testing = ref({ watch: false, export: false })
const savingFolders = ref(false)

const emit = defineEmits(['restart-required'])

const PATH_FIELDS = ['watch_dir', 'export_dir', 'watch_dir_host', 'export_dir_host']
// Nur Anzeige — gehen nicht zurueck an das Backend.
const RESPONSE_ONLY = [
  'deployment_mode', 'watch_dir_net_password_set', 'export_dir_net_password_set',
  'network_folders_supported', 'scanner_share', 'restart_required',
]

// Betriebsmodus kommt vom Backend (settings.deployment_mode). Nur im Docker-
// Modus gibt es Host-Pfade, die per Bind-Mount in den Container gelangen; in
// Native/Dev ist der eingegebene Pfad direkt der Ordner, den das Backend nutzt.
// Fehlt das Feld (altes Backend), bleibt das bisherige Docker-Verhalten.
const isDocker = computed(() => (folderSettings.value.deployment_mode || 'docker') === 'docker')

// Feldnamen je Modus: Docker bearbeitet die Host-Pfade, Native/Dev direkt
// watch_dir/export_dir.
const watchField = computed(() => (isDocker.value ? 'watch_dir_host' : 'watch_dir'))
const exportField = computed(() => (isDocker.value ? 'export_dir_host' : 'export_dir'))
const fieldFor = (target) => (target === 'watch' ? watchField.value : exportField.value)

const hasCustomPaths = computed(() =>
  !!(folderSettings.value[watchField.value] || folderSettings.value[exportField.value])
)

function isUnc(value) {
  return typeof value === 'string' && /^(\\\\|\/\/)/.test(value.trim())
}

// Zugangsdaten-Felder nur fuer Netzwerkpfade im Native-/Dev-Betrieb (Windows).
function showCredentials(target) {
  return !isDocker.value && folderSettings.value.network_folders_supported && isUnc(folderSettings.value[fieldFor(target)])
}

const canTest = computed(() => !isDocker.value && folderSettings.value.network_folders_supported)

// Pruefintervall (nur Netzwerk-Eingangsordner): 10–600 s. Ein Wert ausserhalb
// wird nicht still verworfen, sondern angezeigt und das Speichern verhindert.
const INTERVAL_MIN = 10
const INTERVAL_MAX = 600
const intervalError = computed(() => {
  if (!showCredentials('watch')) return ''
  const value = Number(folderSettings.value.watch_poll_interval)
  if (!Number.isFinite(value) || value < INTERVAL_MIN || value > INTERVAL_MAX) {
    return `Das Prüfintervall muss zwischen ${INTERVAL_MIN} und ${INTERVAL_MAX} Sekunden liegen.`
  }
  return ''
})
const scannerShare = computed(() => folderSettings.value.scanner_share)

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

function buildPayload() {
  const payload = { ...folderSettings.value }
  RESPONSE_ONLY.forEach((k) => delete payload[k])
  // null = Passwort unveraendert lassen (nie ein leeres Passwort mitschicken).
  payload.watch_dir_net_password = passwords.value.watch || null
  payload.export_dir_net_password = passwords.value.export || null
  if (!showCredentials('watch')) payload.watch_dir_net_user = ''
  if (!showCredentials('export')) payload.export_dir_net_user = ''
  const interval = Number(payload.watch_poll_interval)
  // Nur fuer Netzwerk-Eingangsordner relevant; ungueltig blockiert saveFolderSettings.
  payload.watch_poll_interval = Number.isFinite(interval) && interval >= INTERVAL_MIN && interval <= INTERVAL_MAX
    ? Math.round(interval)
    : null
  return payload
}

async function saveFolderSettings() {
  if (savingFolders.value) return  // kein zweiter, ueberlappender Speichervorgang
  PATH_FIELDS.forEach(normalizeField)
  if (intervalError.value) {
    notify.error(intervalError.value)
    return
  }
  savingFolders.value = true
  try {
    const result = await updateSystemSettings(buildPayload())
    folderSettings.value = { ...folderSettings.value, ...result }
    passwords.value = { watch: '', export: '' }
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

async function testFolder(target) {
  const field = fieldFor(target)
  normalizeField(field)
  testing.value[target] = true
  testResults.value[target] = null
  try {
    const body = { target, path: folderSettings.value[field] }
    if (showCredentials(target)) {
      body.net_user = folderSettings.value[`${target}_dir_net_user`] || ''
      if (passwords.value[target]) body.net_password = passwords.value[target]
    }
    testResults.value[target] = await testFolderSettings(body)
  } catch (e) {
    testResults.value[target] = { ok: false, message: apiErrorMessage(e, 'Test fehlgeschlagen.'), suggested_paths: [] }
  } finally {
    testing.value[target] = false
  }
}

function useSuggestion(target, path) {
  folderSettings.value[fieldFor(target)] = path
  testResults.value[target] = null
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
  testResults.value = { watch: null, export: null }
}

async function copyText(text) {
  if (await copyToClipboard(text)) notify.success('Kopiert.')
  else notify.error('Kopieren nicht möglich — bitte den Text manuell markieren.')
}

const CIFS_EXAMPLE = `services:
  backend:
    volumes: ["scans:/app/external/watch"]
    environment: ["WATCH_FORCE_POLLING=true"]
volumes:
  scans:
    driver: local
    driver_opts:
      type: cifs
      device: "//nas/scans"
      o: "credentials=/root/.smb-scans,vers=3.0,uid=1000,gid=1000"`

const FOLDERS = [
  { target: 'watch', id: 'folder-watch' },
  { target: 'export', id: 'folder-export' },
]

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
          <template v-if="!isDocker">
            Netzlaufwerke (z.B. <code>V:</code>) sieht der Hintergrunddienst nicht — für eine NAS den Netzwerkpfad
            (<code>\\nas\freigabe\ordner</code>) mit Benutzer und Passwort der Freigabe eintragen.
          </template>
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
            :placeholder="isDocker ? 'Leer = Standard (data/watch), z.B. D:\\Zettelwirtschaft\\eingang' : 'Leer = Standard-Eingangsordner, z.B. D:\\Scans\\Eingang oder \\\\nas\\scans'"
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
        <p id="folder-export-help" class="text-xs text-gray-500 mb-2">Verarbeitete Dokumente werden zusätzlich in diesen Ordner kopiert, z.B. in einen OneDrive-Ordner als externe Sicherung. Leer lassen zum Deaktivieren.</p>
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
            :placeholder="isDocker ? 'Leer = deaktiviert, z.B. D:\\Zettelwirtschaft\\fertig' : 'Leer = deaktiviert, z.B. D:\\Belege oder \\\\nas\\archiv'"
          />
        </div>
        <p v-if="isDocker && folderSettings.export_dir_host" class="text-xs text-gray-500 mt-1">
          Container-Pfad: <code class="bg-gray-100 px-1 rounded font-mono">/app/external/export</code> (wird automatisch gesetzt)
        </p>
        <p v-else-if="!folderSettings[exportField]" class="text-xs text-gray-400 mt-1">
          Kein Zielordner konfiguriert. Dokumente werden nur im Archiv gespeichert.
        </p>
      </div>

      <!-- Netzwerkordner: Zugangsdaten, Test, Vorschlaege (Native/Dev) -->
      <template v-for="f in FOLDERS" :key="f.target">
        <div
          v-if="showCredentials(f.target)"
          class="rounded-lg border border-gray-200 p-3 space-y-3"
          :data-testid="`net-credentials-${f.target}`"
        >
          <p class="text-xs text-gray-600">
            {{ f.target === 'watch' ? 'Eingangsordner' : 'Zielordner' }} liegt im Netzwerk: Der Dienst verbindet sich mit diesen Zugangsdaten der Freigabe
            (z.B. NAS-Benutzer). Das Passwort wird verschlüsselt gespeichert und nie angezeigt.
          </p>
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label :for="`${f.id}-user`" class="block text-xs font-medium text-gray-700 mb-1">Benutzer der Freigabe</label>
              <input
                :id="`${f.id}-user`"
                v-model="folderSettings[`${f.target}_dir_net_user`]"
                class="input text-sm"
                autocomplete="off"
                placeholder="z.B. scanner oder NAS\scanner"
              />
            </div>
            <div>
              <label :for="`${f.id}-password`" class="block text-xs font-medium text-gray-700 mb-1">Passwort</label>
              <input
                :id="`${f.id}-password`"
                v-model="passwords[f.target]"
                type="password"
                class="input text-sm"
                autocomplete="new-password"
                :placeholder="folderSettings[`${f.target}_dir_net_password_set`] ? 'gespeichert – leer lassen für unverändert' : 'Passwort der Freigabe'"
              />
            </div>
          </div>
          <div v-if="f.target === 'watch'" class="max-w-xs">
            <label for="folder-watch-interval" class="block text-xs font-medium text-gray-700 mb-1">Prüfintervall (Sekunden)</label>
            <input
              id="folder-watch-interval"
              v-model.number="folderSettings.watch_poll_interval"
              type="number"
              min="10"
              max="600"
              class="input text-sm"
              :aria-invalid="intervalError ? 'true' : 'false'"
              aria-describedby="folder-watch-interval-help"
            />
            <p v-if="intervalError" id="folder-watch-interval-help" class="text-xs text-red-700 mt-1" data-testid="interval-error">{{ intervalError }}</p>
            <p v-else id="folder-watch-interval-help" class="text-xs text-gray-400 mt-1">Netzwerkordner werden regelmäßig abgefragt (10–600 s).</p>
          </div>
        </div>

        <div v-if="canTest && folderSettings[fieldFor(f.target)]" class="space-y-2">
          <button
            type="button"
            class="btn-secondary text-sm"
            :disabled="testing[f.target]"
            :data-testid="`test-folder-${f.target}`"
            @click="testFolder(f.target)"
          >
            {{ testing[f.target] ? 'Prüfe…' : (isUnc(folderSettings[fieldFor(f.target)]) ? 'Verbindung testen' : 'Ordner prüfen') }}
            <span class="sr-only">({{ f.target === 'watch' ? 'Eingangsordner' : 'Zielordner' }})</span>
          </button>
          <div
            v-if="testResults[f.target]"
            :class="['rounded-lg border p-3 text-xs', testResults[f.target].ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800']"
            :data-testid="`test-result-${f.target}`"
            role="status"
          >
            <p>{{ testResults[f.target].message }}</p>
            <div v-for="s in testResults[f.target].suggested_paths || []" :key="s" class="mt-2 flex flex-wrap items-center gap-2">
              <code class="bg-white/60 px-1 rounded font-mono break-all">{{ s }}</code>
              <button type="button" class="underline hover:no-underline" :data-testid="`use-suggestion-${f.target}`" @click="useSuggestion(f.target, s)">
                Netzwerkpfad übernehmen
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- Docker: Netzwerkordner als CIFS-Volume -->
      <details v-if="isDocker" class="text-xs" data-testid="docker-cifs-hint">
        <summary class="text-gray-500 cursor-pointer hover:text-gray-700">Netzwerkordner (NAS) im Docker-Betrieb</summary>
        <div class="mt-2 space-y-2 pl-2 border-l-2 border-gray-200 text-gray-600">
          <p>Netzwerkordner bitte als CIFS-Volume einbinden — in einer <strong>eigenen</strong> Datei <code class="bg-gray-100 px-1 rounded font-mono">docker-compose.cifs.yml</code> (die automatisch erzeugte Override-Datei wird beim Start überschrieben):</p>
          <pre class="bg-gray-100 rounded p-2 overflow-x-auto font-mono">{{ CIFS_EXAMPLE }}</pre>
          <p>Die Zugangsdaten stehen in einer Datei auf dem Docker-Host (<code class="bg-gray-100 px-1 rounded font-mono">username=…</code>/<code class="bg-gray-100 px-1 rounded font-mono">password=…</code>, nur für Administratoren lesbar) statt im Klartext in der Compose-Datei.</p>
        </div>
      </details>

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

      <!-- (B) Scanner-Freigabe des lokalen Eingangsordners -->
      <div v-if="!isDocker && scannerShare" class="rounded-lg border border-gray-200 p-3 text-xs space-y-2" data-testid="scanner-share">
        <p class="text-sm font-medium text-gray-700">Scanner-Freigabe</p>
        <template v-if="scannerShare.exists">
          <p class="text-green-700">Eingerichtet – im Netzwerk-Scanner als Zielordner eintragen:</p>
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-gray-500">Netzwerkordner:</span>
            <code class="bg-gray-100 px-1 rounded font-mono select-all break-all" data-testid="scanner-share-unc">{{ scannerShare.unc_path }}</code>
            <button type="button" class="text-primary-600 underline" @click="copyText(scannerShare.unc_path)">kopieren</button>
          </div>
          <div v-if="scannerShare.user" class="flex flex-wrap items-center gap-2">
            <span class="text-gray-500">Benutzer:</span>
            <code class="bg-gray-100 px-1 rounded font-mono select-all">{{ scannerShare.user }}</code>
            <button type="button" class="text-primary-600 underline" @click="copyText(scannerShare.user)">kopieren</button>
          </div>
          <p class="text-gray-500">Das Passwort wurde bei der Einrichtung einmal angezeigt und wird nicht gespeichert. Neues Passwort: Startmenü › Zettelwirtschaft › Scanner-Freigabe einrichten.</p>
          <p v-if="!scannerShare.matches_watch_dir" class="text-amber-700" data-testid="scanner-share-mismatch">
            Die Freigabe zeigt auf einen anderen Ordner als den aktuellen Eingangsordner – Skript erneut ausführen.
          </p>
        </template>
        <p v-else class="text-gray-600" data-testid="scanner-share-missing">
          Nicht eingerichtet – damit ein Netzwerk-Scanner direkt in den Eingangsordner scannt:
          Startmenü › Zettelwirtschaft › Scanner-Freigabe einrichten (Administratorrechte).
        </p>
      </div>

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
