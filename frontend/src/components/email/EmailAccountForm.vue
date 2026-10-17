<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  account: { type: Object, default: null },
  scopes: { type: Array, default: () => [] },
  // Abruf-Regeln vom Backend: { timezone, unseen_only, max_per_run, idle_interval_minutes }
  info: { type: Object, default: null },
})

const emit = defineEmits(['save', 'cancel'])

function defaultForm() {
  return {
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
    is_active: true,
  }
}

function formFromAccount(acc) {
  return {
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
    is_active: acc.is_active ?? true,
  }
}

const form = ref(defaultForm())
const errors = ref({})

// Der Elternteil remountet das Formular pro Konto (key), der Watcher deckt
// zusaetzlich einen Wechsel des account-Props ab — inkl. zurueck auf "neu".
watch(() => props.account, (acc) => {
  form.value = acc ? formFromAccount(acc) : defaultForm()
  errors.value = {}
}, { immediate: true })

const isEdit = computed(() => !!props.account)

const timezoneLabel = computed(() => props.info?.timezone || '')
const idleMinutes = computed(() => props.info?.idle_interval_minutes || 5)

const scheduleOptions = computed(() => [
  { value: 'MANUAL', label: 'Manuell', desc: 'Nur auf Knopfdruck' },
  { value: 'CRON', label: 'Zeitgesteuert', desc: 'Nach Zeitplan (CRON)' },
  { value: 'IDLE', label: 'Automatisch', desc: `Alle ${idleMinutes.value} Minuten` },
])

// Pflichtfelder clientseitig pruefen — sonst kommt der 422 des Backends erst
// nach dem Absenden und nennt nur Feldnamen.
function validate(data) {
  const e = {}
  if (!data.name.trim()) e.name = 'Bitte einen Anzeigenamen eingeben.'
  if (!data.imap_host.trim()) e.imap_host = 'Bitte den IMAP-Server eingeben.'
  if (!data.username.trim()) e.username = 'Bitte den Benutzernamen eingeben.'
  if (!isEdit.value && !data.password) e.password = 'Bitte das Passwort eingeben.'
  if (!Number.isInteger(data.imap_port) || data.imap_port < 1 || data.imap_port > 65535) {
    e.imap_port = 'Port muss zwischen 1 und 65535 liegen.'
  }
  if (!data.folder_inbox.trim()) e.folder_inbox = 'Bitte den Posteingangs-Ordner angeben.'
  if (!data.folder_processed.trim()) e.folder_processed = 'Bitte den Zielordner angeben.'
  if (data.schedule_type === 'CRON' && !data.cron_expression.trim()) {
    e.cron_expression = 'Für den Zeitplan wird ein CRON-Ausdruck benötigt.'
  }
  return e
}

function handleSubmit() {
  const data = { ...form.value }
  for (const key of ['name', 'imap_host', 'username', 'folder_inbox', 'folder_processed', 'cron_expression']) {
    data[key] = (data[key] || '').trim()
  }
  errors.value = validate(data)
  if (Object.keys(errors.value).length) return

  if (isEdit.value && !data.password) {
    delete data.password
  }
  if (!isEdit.value) {
    // Neue Konten sind immer aktiv; das Create-Schema kennt is_active nicht.
    delete data.is_active
  }
  if (data.schedule_type !== 'CRON') {
    data.cron_expression = null
  }
  emit('save', data)
}

function errorId(field) {
  return errors.value[field] ? `email-${field}-error` : undefined
}
</script>

<template>
  <div class="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-4">
    <h3 class="text-sm font-semibold text-gray-900">
      {{ isEdit ? 'E-Mail-Konto bearbeiten' : 'Neues E-Mail-Konto' }}
    </h3>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label for="email-name" class="block text-xs font-medium text-gray-500 mb-1">Anzeigename</label>
        <input id="email-name" v-model="form.name" class="input" placeholder="z.B. Gmail Privat" :aria-invalid="!!errors.name" :aria-describedby="errorId('name')" />
        <p v-if="errors.name" id="email-name-error" class="mt-1 text-xs text-red-600">{{ errors.name }}</p>
      </div>
      <div>
        <label for="email-host" class="block text-xs font-medium text-gray-500 mb-1">IMAP-Server</label>
        <input id="email-host" v-model="form.imap_host" class="input" placeholder="z.B. imap.gmail.com" :aria-invalid="!!errors.imap_host" :aria-describedby="errorId('imap_host')" />
        <p v-if="errors.imap_host" id="email-imap_host-error" class="mt-1 text-xs text-red-600">{{ errors.imap_host }}</p>
      </div>
      <div>
        <label for="email-username" class="block text-xs font-medium text-gray-500 mb-1">Benutzername</label>
        <input id="email-username" v-model="form.username" class="input" placeholder="z.B. user@gmail.com" autocomplete="username" :aria-invalid="!!errors.username" :aria-describedby="errorId('username')" />
        <p v-if="errors.username" id="email-username-error" class="mt-1 text-xs text-red-600">{{ errors.username }}</p>
      </div>
      <div>
        <label for="email-password" class="block text-xs font-medium text-gray-500 mb-1">
          {{ isEdit ? 'Neues Passwort (leer = unverändert)' : 'Passwort / App-Passwort' }}
        </label>
        <input id="email-password" v-model="form.password" type="password" class="input" autocomplete="new-password" :placeholder="isEdit ? 'Nur bei Änderung eingeben' : 'App-Passwort eingeben'" :aria-invalid="!!errors.password" :aria-describedby="errorId('password')" />
        <p v-if="errors.password" id="email-password-error" class="mt-1 text-xs text-red-600">{{ errors.password }}</p>
      </div>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <div>
        <label for="email-port" class="block text-xs font-medium text-gray-500 mb-1">Port</label>
        <input id="email-port" v-model.number="form.imap_port" type="number" class="input" :aria-invalid="!!errors.imap_port" :aria-describedby="errorId('imap_port')" />
        <p v-if="errors.imap_port" id="email-imap_port-error" class="mt-1 text-xs text-red-600">{{ errors.imap_port }}</p>
      </div>
      <div class="flex items-end">
        <label class="flex items-center gap-2 pb-2">
          <input type="checkbox" v-model="form.use_ssl" class="rounded border-gray-300" />
          <span class="text-sm text-gray-600">SSL/TLS verwenden</span>
        </label>
      </div>
      <div v-if="isEdit" class="flex items-end">
        <label class="flex items-center gap-2 pb-2">
          <input type="checkbox" v-model="form.is_active" class="rounded border-gray-300" data-testid="email-active-toggle" />
          <span class="text-sm text-gray-600">Aktiv (automatischer Abruf)</span>
        </label>
      </div>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label for="email-inbox" class="block text-xs font-medium text-gray-500 mb-1">Posteingang (IMAP-Ordner)</label>
        <input id="email-inbox" v-model="form.folder_inbox" class="input" :aria-invalid="!!errors.folder_inbox" :aria-describedby="errorId('folder_inbox')" />
        <p v-if="errors.folder_inbox" id="email-folder_inbox-error" class="mt-1 text-xs text-red-600">{{ errors.folder_inbox }}</p>
      </div>
      <div>
        <label for="email-processed" class="block text-xs font-medium text-gray-500 mb-1">Zielordner nach Verarbeitung</label>
        <input id="email-processed" v-model="form.folder_processed" class="input" :aria-invalid="!!errors.folder_processed" :aria-describedby="errorId('folder_processed')" />
        <p v-if="errors.folder_processed" id="email-folder_processed-error" class="mt-1 text-xs text-red-600">{{ errors.folder_processed }}</p>
      </div>
    </div>
    <p class="text-xs text-gray-400 -mt-2">
      Abgerufen werden nur <strong>ungelesene</strong> E-Mails aus dem Posteingang. Bereits gelesene Mails bleiben unberührt.
    </p>

    <!-- Schedule -->
    <fieldset>
      <legend class="block text-xs font-medium text-gray-500 mb-2">Abruf-Modus</legend>
      <div class="flex flex-wrap gap-3">
        <label v-for="opt in scheduleOptions" :key="opt.value" class="flex items-start gap-2 cursor-pointer">
          <input type="radio" v-model="form.schedule_type" :value="opt.value" class="mt-0.5 border-gray-300" />
          <div>
            <span class="text-sm font-medium text-gray-700">{{ opt.label }}</span>
            <p class="text-xs text-gray-400">{{ opt.desc }}</p>
          </div>
        </label>
      </div>
    </fieldset>

    <div v-if="form.schedule_type === 'CRON'">
      <label for="email-cron" class="block text-xs font-medium text-gray-500 mb-1">CRON-Ausdruck</label>
      <input id="email-cron" v-model="form.cron_expression" class="input font-mono" placeholder="*/15 * * * *" :aria-invalid="!!errors.cron_expression" aria-describedby="email-cron-help" />
      <p v-if="errors.cron_expression" class="mt-1 text-xs text-red-600">{{ errors.cron_expression }}</p>
      <p id="email-cron-help" class="text-xs text-gray-400 mt-1" data-testid="cron-help">
        Beispiele: <code class="bg-gray-100 px-1 rounded">*/15 * * * *</code> (alle 15 Min.), <code class="bg-gray-100 px-1 rounded">0 */2 * * *</code> (alle 2 Std.), <code class="bg-gray-100 px-1 rounded">0 8 * * *</code> (täglich 8 Uhr).
        <template v-if="timezoneLabel">Uhrzeiten gelten in der Zeitzone <strong>{{ timezoneLabel }}</strong>.</template>
        <template v-else>Uhrzeiten gelten in der Zeitzone des Servers.</template>
      </p>
    </div>

    <!-- Filing Scope -->
    <div v-if="scopes.length > 0">
      <label for="email-scope" class="block text-xs font-medium text-gray-500 mb-1">Ablagebereich (optional)</label>
      <select id="email-scope" v-model="form.filing_scope_id" class="input">
        <option :value="null">Automatisch zuordnen</option>
        <option v-for="scope in scopes" :key="scope.id" :value="scope.id">{{ scope.name }}</option>
      </select>
      <p class="text-xs text-gray-400 mt-1">Alle Dokumente aus diesem Konto werden dem gewählten Bereich zugeordnet.</p>
    </div>

    <div class="flex gap-2 pt-2">
      <button @click="handleSubmit" class="btn-primary text-sm">
        {{ isEdit ? 'Speichern' : 'Konto hinzufügen' }}
      </button>
      <button @click="$emit('cancel')" class="btn-secondary text-sm">Abbrechen</button>
    </div>
  </div>
</template>
