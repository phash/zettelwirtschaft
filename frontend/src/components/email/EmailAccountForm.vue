<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  account: { type: Object, default: null },
  scopes: { type: Array, default: () => [] },
})

const emit = defineEmits(['save', 'cancel'])

const form = ref({
  name: '',
  imap_host: '',
  imap_port: 993,
  use_ssl: true,
  username: '',
  password: '',
  folder_inbox: 'INBOX',
  folder_processed: 'Zettelwirtschaft/Verarbeitet',
  schedule_type: 'MANUAL',
  cron_expression: '',
  filing_scope_id: null,
})

watch(() => props.account, (acc) => {
  if (acc) {
    form.value = {
      name: acc.name || '',
      imap_host: acc.imap_host || '',
      imap_port: acc.imap_port || 993,
      use_ssl: acc.use_ssl ?? true,
      username: acc.username || '',
      password: '',
      folder_inbox: acc.folder_inbox || 'INBOX',
      folder_processed: acc.folder_processed || 'Zettelwirtschaft/Verarbeitet',
      schedule_type: acc.schedule_type || 'MANUAL',
      cron_expression: acc.cron_expression || '',
      filing_scope_id: acc.filing_scope_id || null,
    }
  }
}, { immediate: true })

const isEdit = computed(() => !!props.account)

const scheduleOptions = [
  { value: 'MANUAL', label: 'Manuell', desc: 'Nur auf Knopfdruck' },
  { value: 'CRON', label: 'Zeitgesteuert', desc: 'Nach Zeitplan (CRON)' },
  { value: 'IDLE', label: 'Automatisch', desc: 'Alle 5 Minuten' },
]

function handleSubmit() {
  const data = { ...form.value }
  if (isEdit.value && !data.password) {
    delete data.password
  }
  if (data.schedule_type !== 'CRON') {
    data.cron_expression = null
  }
  emit('save', data)
}
</script>

<template>
  <div class="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-4">
    <h3 class="text-sm font-semibold text-gray-900">
      {{ isEdit ? 'E-Mail-Konto bearbeiten' : 'Neues E-Mail-Konto' }}
    </h3>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Anzeigename</label>
        <input v-model="form.name" class="input" placeholder="z.B. Gmail Privat" />
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">IMAP-Server</label>
        <input v-model="form.imap_host" class="input" placeholder="z.B. imap.gmail.com" />
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Benutzername</label>
        <input v-model="form.username" class="input" placeholder="z.B. user@gmail.com" />
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">
          {{ isEdit ? 'Neues Passwort (leer = unveraendert)' : 'Passwort / App-Passwort' }}
        </label>
        <input v-model="form.password" type="password" class="input" :placeholder="isEdit ? 'Nur bei Aenderung eingeben' : 'App-Passwort eingeben'" />
      </div>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Port</label>
        <input v-model.number="form.imap_port" type="number" class="input" />
      </div>
      <div class="flex items-end">
        <label class="flex items-center gap-2 pb-2">
          <input type="checkbox" v-model="form.use_ssl" class="rounded border-gray-300" />
          <span class="text-sm text-gray-600">SSL/TLS verwenden</span>
        </label>
      </div>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Posteingang (IMAP-Ordner)</label>
        <input v-model="form.folder_inbox" class="input" />
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Zielordner nach Verarbeitung</label>
        <input v-model="form.folder_processed" class="input" />
      </div>
    </div>

    <!-- Schedule -->
    <div>
      <label class="block text-xs font-medium text-gray-500 mb-2">Abruf-Modus</label>
      <div class="flex flex-wrap gap-3">
        <label v-for="opt in scheduleOptions" :key="opt.value" class="flex items-start gap-2 cursor-pointer">
          <input type="radio" v-model="form.schedule_type" :value="opt.value" class="mt-0.5 border-gray-300" />
          <div>
            <span class="text-sm font-medium text-gray-700">{{ opt.label }}</span>
            <p class="text-xs text-gray-400">{{ opt.desc }}</p>
          </div>
        </label>
      </div>
    </div>

    <div v-if="form.schedule_type === 'CRON'">
      <label class="block text-xs font-medium text-gray-500 mb-1">CRON-Ausdruck</label>
      <input v-model="form.cron_expression" class="input font-mono" placeholder="*/15 * * * *" />
      <p class="text-xs text-gray-400 mt-1">Beispiele: <code class="bg-gray-100 px-1 rounded">*/15 * * * *</code> (alle 15 Min.), <code class="bg-gray-100 px-1 rounded">0 */2 * * *</code> (alle 2 Std.), <code class="bg-gray-100 px-1 rounded">0 8 * * *</code> (taeglich 8 Uhr)</p>
    </div>

    <!-- Filing Scope -->
    <div v-if="scopes.length > 0">
      <label class="block text-xs font-medium text-gray-500 mb-1">Ablagebereich (optional)</label>
      <select v-model="form.filing_scope_id" class="input">
        <option :value="null">Automatisch zuordnen</option>
        <option v-for="scope in scopes" :key="scope.id" :value="scope.id">{{ scope.name }}</option>
      </select>
      <p class="text-xs text-gray-400 mt-1">Alle Dokumente aus diesem Konto werden dem gewaehlten Bereich zugeordnet.</p>
    </div>

    <div class="flex gap-2 pt-2">
      <button @click="handleSubmit" class="btn-primary text-sm">
        {{ isEdit ? 'Speichern' : 'Konto hinzufuegen' }}
      </button>
      <button @click="$emit('cancel')" class="btn-secondary text-sm">Abbrechen</button>
    </div>
  </div>
</template>
