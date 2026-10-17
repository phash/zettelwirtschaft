<script setup>
import { ref, shallowReactive, onMounted, getCurrentScope } from 'vue'
import EmailAccountForm from './EmailAccountForm.vue'
import { getEmailAccounts, createEmailAccount, updateEmailAccount, deleteEmailAccount, testEmailConnection, fetchEmailsNow, getEmailHistory, listTasks } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { useBackgroundTask } from '../../composables/useBackgroundTask'
import { formatDateTime } from '../../utils/formatters'
import { apiErrorMessage } from '../../utils/apiErrors'
import { pluralize } from '../../utils/pluralize'
import ConfirmDialog from '../common/ConfirmDialog.vue'

defineProps({
  scopes: { type: Array, default: () => [] },
  // Abruf-Regeln vom Backend (GET /api/email/settings), null = unbekannt.
  info: { type: Object, default: null },
})

const notify = useNotificationStore()
const accounts = ref([])
const showForm = ref(false)
const editingAccount = ref(null)
const deletingId = ref(null)
const testingId = ref(null)
const historyAccountId = ref(null)
const historyItems = ref([])

async function loadAccounts() {
  try {
    accounts.value = await getEmailAccounts()
  } catch {
    notify.error('E-Mail-Konten konnten nicht geladen werden.')
  }
}

function startCreate() {
  editingAccount.value = null
  showForm.value = true
}

function startEdit(account) {
  editingAccount.value = account
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  editingAccount.value = null
}

async function handleSave(data) {
  try {
    if (editingAccount.value) {
      await updateEmailAccount(editingAccount.value.id, data)
      notify.success('Konto aktualisiert.')
    } else {
      await createEmailAccount(data)
      notify.success('Konto hinzugefügt.')
    }
    closeForm()
    await loadAccounts()
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Fehler beim Speichern.'))
  }
}

async function handleDelete() {
  try {
    await deleteEmailAccount(deletingId.value)
    notify.success('Konto gelöscht.')
    deletingId.value = null
    await loadAccounts()
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Löschen fehlgeschlagen.'))
    deletingId.value = null
  }
}

async function handleTest(account) {
  testingId.value = account.id
  try {
    const result = await testEmailConnection(account.id)
    if (result.success) {
      notify.success(`Verbindung zu ${account.name} erfolgreich!`)
    } else {
      notify.error(`Verbindung fehlgeschlagen: ${result.error || 'unbekannter Fehler'}`)
    }
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Verbindungstest fehlgeschlagen.'))
  } finally {
    testingId.value = null
  }
}

// --- Manueller Abruf: Hintergrund-Vorgang pro Konto (kind "email_fetch") --
// Jedes Konto bekommt eine eigene Polling-Instanz, angelegt im Effect-Scope
// dieser Komponente — so endet das Polling beim Verlassen der Seite (der
// Abruf selbst laeuft im Backend weiter und wird beim naechsten Besuch wieder
// aufgenommen).
const componentScope = getCurrentScope()
// shallowReactive: neue Konten-Eintraege sind reaktiv, die Composable-Refs
// darin werden aber nicht ausgepackt (.running.value bleibt gueltig).
const fetchers = shallowReactive({})

function fetchResultMessage(result) {
  const parts = [
    `${pluralize(result.total ?? 0, 'E-Mail', 'E-Mails')} geprüft`,
    `${result.relevant ?? 0} relevant`,
    `${result.irrelevant ?? 0} irrelevant`,
  ]
  if (result.skipped) parts.push(`${result.skipped} bereits bekannt`)
  if (result.failed) parts.push(`${result.failed} fehlgeschlagen`)
  let msg = `${parts[0]}: ${parts.slice(1).join(', ')}`
  if (result.remaining) msg += ` — ${pluralize(result.remaining, 'weitere folgt', 'weitere folgen')} beim nächsten Abruf`
  return msg
}

function fetcherFor(account) {
  const id = String(account.id)
  if (!fetchers[id]) {
    fetchers[id] = componentScope.run(() => useBackgroundTask({
      onSuccess: (result) => {
        const msg = `${account.name}: ${fetchResultMessage(result || {})}`
        if (result?.error || result?.failed) notify.error(result.error ? `${msg} (${result.error})` : msg)
        else notify.success(msg)
        loadAccounts()
      },
      onError: (message) => {
        notify.error(`${account.name}: ${message || 'E-Mail-Abruf fehlgeschlagen.'}`)
        loadAccounts()
      },
    }))
  }
  return fetchers[id]
}

function isFetching(account) {
  return !!fetchers[String(account.id)]?.running.value
}

function fetchProgress(account) {
  return fetchers[String(account.id)]?.progressText.value || ''
}

function handleFetch(account) {
  fetcherFor(account).start(() => fetchEmailsNow(account.id))
}

// Beim Oeffnen der Seite noch laufende Abrufe wieder aufnehmen (eine Abfrage
// fuer alle Konten).
async function resumeRunningFetches() {
  try {
    const running = await listTasks({ kind: 'email_fetch', active: true, limit: 100 })
    for (const task of running || []) {
      const account = accounts.value.find(a => String(a.id) === String(task.key))
      if (account) fetcherFor(account).attach(task)
    }
  } catch {
    // ignore
  }
}

async function toggleHistory(accountId) {
  if (historyAccountId.value === accountId) {
    historyAccountId.value = null
    historyItems.value = []
    return
  }
  try {
    historyItems.value = await getEmailHistory(accountId, { limit: 20 })
    historyAccountId.value = accountId
  } catch {
    notify.error('Verlauf konnte nicht geladen werden.')
  }
}

function statusBadgeClass(status) {
  if (status === 'RELEVANT') return 'bg-green-100 text-green-700'
  if (status === 'IRRELEVANT') return 'bg-gray-100 text-gray-600'
  return 'bg-red-100 text-red-700'
}

function statusLabel(status) {
  if (status === 'RELEVANT') return 'Relevant'
  if (status === 'IRRELEVANT') return 'Irrelevant'
  return 'Fehler'
}

// H-FE-4: Komponente laedt selbst.
onMounted(async () => {
  await loadAccounts()
  resumeRunningFetches()
})
</script>

<template>
  <div class="card">
    <div class="flex items-center justify-between mb-2">
      <h2 class="text-lg font-semibold text-gray-900">E-Mail-Konten</h2>
      <button @click="startCreate" class="btn-primary text-sm">+ Neues Konto</button>
    </div>
    <p class="mb-4 text-xs text-gray-500" data-testid="email-import-rules">
      Importiert werden nur <strong>ungelesene</strong> E-Mails aus dem eingestellten Posteingangs-Ordner; verarbeitete Mails
      werden anschließend in den Zielordner verschoben.
      <template v-if="info?.max_per_run">Pro Abruf höchstens {{ pluralize(info.max_per_run, 'E-Mail', 'E-Mails') }}.</template>
      <template v-if="info?.timezone">Zeitpläne (CRON) gelten in der Zeitzone {{ info.timezone }}.</template>
    </p>

    <!-- Account List -->
    <div class="space-y-3">
      <div v-for="acc in accounts" :key="acc.id" class="rounded-lg border border-gray-200 p-4">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-sm font-medium text-gray-900">{{ acc.name }}</span>
              <span :class="['badge text-xs', acc.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500']">
                {{ acc.is_active ? 'Aktiv' : 'Inaktiv' }}
              </span>
              <span class="badge bg-blue-50 text-blue-600 text-xs">{{ acc.schedule_type === 'CRON' ? 'Zeitgesteuert' : acc.schedule_type === 'IDLE' ? 'Automatisch' : 'Manuell' }}</span>
            </div>
            <p class="text-xs text-gray-500 mt-1 break-all">{{ acc.username }} @ {{ acc.imap_host }}:{{ acc.imap_port }}</p>
            <p v-if="acc.schedule_type === 'CRON' && acc.cron_expression" class="text-xs text-gray-400 mt-0.5">
              Zeitplan: <code class="font-mono">{{ acc.cron_expression }}</code><template v-if="info?.timezone"> ({{ info.timezone }})</template>
            </p>
            <p v-if="acc.last_checked_at" class="text-xs text-gray-400 mt-0.5">Letzter Abruf: {{ formatDateTime(acc.last_checked_at) }}</p>
            <p v-if="acc.last_error" class="text-xs text-red-500 mt-0.5">Fehler: {{ acc.last_error }}</p>
            <p v-if="isFetching(acc)" class="text-xs text-primary-600 mt-0.5" role="status" :data-testid="`email-fetch-progress-${acc.id}`">
              {{ fetchProgress(acc) || 'Abruf läuft im Hintergrund …' }}
            </p>
          </div>
          <div class="flex min-w-0 flex-wrap gap-1">
            <button @click="handleTest(acc)" :disabled="testingId === acc.id" class="btn-secondary text-xs px-2 py-1">
              {{ testingId === acc.id ? 'Teste...' : 'Testen' }}
            </button>
            <button @click="handleFetch(acc)" :disabled="isFetching(acc)" class="btn-secondary text-xs px-2 py-1">
              {{ isFetching(acc) ? 'Rufe ab...' : 'Jetzt abrufen' }}
            </button>
            <button @click="toggleHistory(acc.id)" class="btn-secondary text-xs px-2 py-1">
              {{ historyAccountId === acc.id ? 'Verlauf schließen' : 'Verlauf' }}
            </button>
            <button @click="startEdit(acc)" class="text-xs text-primary-600 hover:text-primary-700 px-2 py-1">Bearbeiten</button>
            <button @click="deletingId = acc.id" class="text-xs text-red-500 hover:text-red-700 px-2 py-1">Löschen</button>
          </div>
        </div>

        <!-- Inline History -->
        <div v-if="historyAccountId === acc.id && historyItems.length > 0" class="mt-3 border-t border-gray-100 pt-3">
          <div class="space-y-1">
            <div v-for="item in historyItems" :key="item.id" class="flex items-center gap-3 text-xs py-1.5">
              <span :class="['badge', statusBadgeClass(item.status)]">{{ statusLabel(item.status) }}</span>
              <span class="flex-1 truncate text-gray-700">{{ item.subject || '(kein Betreff)' }}</span>
              <span class="text-gray-400 truncate max-w-[40%]">{{ item.sender }}</span>
              <span class="text-gray-400 flex-shrink-0">{{ formatDateTime(item.created_at) }}</span>
            </div>
          </div>
        </div>
        <div v-if="historyAccountId === acc.id && historyItems.length === 0" class="mt-3 text-xs text-gray-400 text-center py-2">
          Keine verarbeiteten E-Mails vorhanden.
        </div>
      </div>

      <div v-if="accounts.length === 0 && !showForm" class="text-sm text-gray-400 text-center py-4">
        Keine E-Mail-Konten konfiguriert. Füge ein Konto hinzu, um E-Mails automatisch zu verarbeiten.
      </div>
    </div>

    <!-- Form (inline); key erzwingt frischen Zustand beim Wechsel Bearbeiten -> Neu -->
    <div v-if="showForm" class="mt-4">
      <EmailAccountForm
        :key="editingAccount?.id ?? 'new'"
        :account="editingAccount"
        :scopes="scopes"
        :info="info"
        @save="handleSave"
        @cancel="closeForm"
      />
    </div>

    <ConfirmDialog
      :show="!!deletingId"
      title="E-Mail-Konto löschen"
      message="Soll dieses E-Mail-Konto wirklich gelöscht werden? Bereits importierte Dokumente bleiben erhalten."
      confirm-text="Löschen"
      :danger="true"
      @confirm="handleDelete"
      @cancel="deletingId = null"
    />
  </div>
</template>
