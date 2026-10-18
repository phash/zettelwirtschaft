<script setup>
// Verarbeitung: automatische Prüfung von Stapel-Scans auf mehrere Dokumente
// (Spezifikation 12). Die Werte liegen in der DB (split_auto_enabled,
// split_min_pages, split_use_llm) und überschreiben die Config-Defaults.
import { ref, onMounted } from 'vue'
import { getSplitSettings, setSplitSettings } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { apiErrorMessage } from '../../utils/apiErrors'

const notify = useNotificationStore()
const loading = ref(true)
const busy = ref(false)
const autoEnabled = ref(true)
const minPages = ref(3)
const useLlm = ref(true)
const maxPages = ref(100)
// Zuletzt gespeicherte Werte: die Schalter speichern sofort und duerfen eine noch
// nicht gespeicherte (ggf. ungueltige) Eingabe im Zahlenfeld nicht mitsenden.
const saved = { autoEnabled: true, minPages: 3, useLlm: true }

function applySaved(data) {
  autoEnabled.value = data.auto_enabled
  minPages.value = data.min_pages
  useLlm.value = data.use_llm ?? true
  saved.autoEnabled = data.auto_enabled
  saved.minPages = data.min_pages
  saved.useLlm = useLlm.value
}

async function load() {
  loading.value = true
  try {
    const data = await getSplitSettings()
    applySaved(data)
    maxPages.value = data.max_pages
  } catch {
    // Einstellungen sind optional — Felder bleiben auf den Defaults
  } finally {
    loading.value = false
  }
}

async function persist(body) {
  busy.value = true
  try {
    const data = await setSplitSettings(body)
    autoEnabled.value = data.auto_enabled
    useLlm.value = data.use_llm ?? saved.useLlm
    saved.autoEnabled = data.auto_enabled
    saved.minPages = data.min_pages
    saved.useLlm = useLlm.value
    notify.success('Einstellung gespeichert.')
    return data
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Einstellung konnte nicht gespeichert werden.'))
    // Schalter zeigen wieder den gespeicherten Zustand
    autoEnabled.value = saved.autoEnabled
    useLlm.value = saved.useLlm
    return null
  } finally {
    busy.value = false
  }
}

async function saveToggle() {
  await persist({ auto_enabled: autoEnabled.value, min_pages: saved.minPages, use_llm: saved.useLlm })
}

async function saveLlmToggle() {
  await persist({ auto_enabled: saved.autoEnabled, min_pages: saved.minPages, use_llm: useLlm.value })
}

async function saveMinPages() {
  const value = Math.round(Number(minPages.value))
  if (!Number.isFinite(value) || value < 2 || value > maxPages.value) {
    notify.error(`Die Mindestseitenzahl muss zwischen 2 und ${maxPages.value} liegen.`)
    return
  }
  const data = await persist({ auto_enabled: saved.autoEnabled, min_pages: value, use_llm: saved.useLlm })
  if (data) minPages.value = data.min_pages
}

onMounted(load)
</script>

<template>
  <div class="card space-y-4" data-testid="split-settings">
    <h2 class="text-lg font-semibold text-gray-900">Verarbeitung</h2>

    <label class="flex cursor-pointer items-start gap-3">
      <input
        v-model="autoEnabled"
        type="checkbox"
        class="mt-1 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
        :disabled="busy || loading"
        data-testid="split-auto-enabled"
        @change="saveToggle"
      />
      <span class="text-sm text-gray-700">
        <span class="font-medium text-gray-900">Stapel-Scans automatisch prüfen</span><br />
        Mehrseitige PDFs aus Upload, Watch-Ordner und E-Mail werden vor der Analyse auf
        mehrere Dokumente geprüft. Erkennt die Prüfung mehrere Dokumente oder ist sie sich
        unsicher, wartet der Stapel unter „Zu prüfen“ auf deine Entscheidung.
      </span>
    </label>

    <div class="flex flex-wrap items-end gap-3">
      <label class="text-sm text-gray-700">
        <span class="mb-1 block font-medium text-gray-900">Ab wie vielen Seiten prüfen</span>
        <input
          v-model.number="minPages"
          type="number"
          min="2"
          :max="maxPages"
          step="1"
          class="input w-28"
          :disabled="busy || loading || !autoEnabled"
          data-testid="split-min-pages"
        />
      </label>
      <button
        type="button"
        class="btn-secondary"
        aria-label="Mindestseitenzahl speichern"
        :disabled="busy || loading || !autoEnabled"
        @click="saveMinPages"
      >Speichern</button>
    </div>
    <p class="text-xs text-gray-500">
      Kürzere PDFs werden ohne Prüfung als ein Dokument verarbeitet. Beim Upload lässt sich
      die Aufteilung zusätzlich mit „Enthält mehrere Dokumente“ anfordern.
    </p>

    <label class="flex cursor-pointer items-start gap-3">
      <input
        v-model="useLlm"
        type="checkbox"
        class="mt-1 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
        :disabled="busy || loading"
        data-testid="split-use-llm"
        @change="saveLlmToggle"
      />
      <span class="text-sm text-gray-700">
        <span class="font-medium text-gray-900">Unsichere Stellen zusätzlich per KI prüfen</span><br />
        Ist die Erkennung bei einem Seitenübergang unsicher, liest die KI die Seitenränder
        (ein Aufruf je Stapel, auf dem Heimrechner etwa 20–40 Sekunden). Ohne KI entscheidest
        du an diesen Stellen selbst.
      </span>
    </label>
  </div>
</template>
