<script setup>
import { ref, onMounted } from 'vue'
import EmailAccountForm from './EmailAccountForm.vue'
import { getEmailAccounts, createEmailAccount, updateEmailAccount, deleteEmailAccount, testEmailConnection, fetchEmailsNow, getEmailHistory } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { formatDateTime } from '../../utils/formatters'
import ConfirmDialog from '../common/ConfirmDialog.vue'

defineProps({
  scopes: { type: Array, default: () => [] },
})

const notify = useNotificationStore()
const accounts = ref([])
const showForm = ref(false)
const editingAccount = ref(null)
const deletingId = ref(null)
const testingId = ref(null)
const fetchingId = ref(null)
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

async function handleSave(data) {
  try {
    if (editingAccount.value) {
      await updateEmailAccount(editingAccount.value.id, data)
      notify.success('Konto aktualisiert.')
    } else {
      await createEmailAccount(data)
      notify.success('Konto hinzugefügt.')
    }
    showForm.value = false
    editingAccount.value = null
    await loadAccounts()
  } catch (e) {
    notify.error(e.response?.data?.detail || 'Fehler beim Speichern.')
  }
}

async function handleDelete() {
  try {
    await deleteEmailAccount(deletingId.value)
    notify.success('Konto gelöscht.')
    deletingId.value = null
    await loadAccounts()
  } catch (e) {
    notify.error(e.response?.data?.detail || 'Löschen fehlgeschlagen.')
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
      notify.error(`Verbindung fehlgeschlagen: ${result.error}`)
    }
  } catch {
    notify.error('Verbindungstest fehlgeschlagen.')
  } finally {
    testingId.value = null
  }
}

async function handleFetch(account) {
  fetchingId.value = account.id
  try {
    const result = await fetchEmailsNow(account.id)
    const msg = `${result.total} E-Mails gepr\u00fcft: ${result.relevant} relevant, ${result.irrelevant} irrelevant`
    if (result.failed > 0) {
      notify.error(`${msg}, ${result.failed} fehlgeschlagen`)
    } else {
      notify.success(msg)
    }
    await loadAccounts()
  } catch {
    notify.error('E-Mail-Abruf fehlgeschlagen.')
  } finally {
    fetchingId.value = null
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
onMounted(() => {
  loadAccounts()
})
</script>

<template>
  <div class="card">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-semibold text-gray-900">E-Mail-Konten</h2>
      <button @click="startCreate" class="btn-primary text-sm">+ Neues Konto</button>
    </div>

    <!-- Account List -->
    <div class="space-y-3">
      <div v-for="acc in accounts" :key="acc.id" class="rounded-lg border border-gray-200 p-4">
        <div class="flex items-start justify-between">
          <div>
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-gray-900">{{ acc.name }}</span>
              <span :class="['badge text-xs', acc.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500']">
                {{ acc.is_active ? 'Aktiv' : 'Inaktiv' }}
              </span>
              <span class="badge bg-blue-50 text-blue-600 text-xs">{{ acc.schedule_type === 'CRON' ? 'Zeitgesteuert' : acc.schedule_type === 'IDLE' ? 'Automatisch' : 'Manuell' }}</span>
            </div>
            <p class="text-xs text-gray-500 mt-1">{{ acc.username }} @ {{ acc.imap_host }}:{{ acc.imap_port }}</p>
            <p v-if="acc.last_checked_at" class="text-xs text-gray-400 mt-0.5">Letzter Abruf: {{ formatDateTime(acc.last_checked_at) }}</p>
            <p v-if="acc.last_error" class="text-xs text-red-500 mt-0.5">Fehler: {{ acc.last_error }}</p>
          </div>
          <div class="flex gap-1 flex-shrink-0">
            <button @click="handleTest(acc)" :disabled="testingId === acc.id" class="btn-secondary text-xs px-2 py-1">
              {{ testingId === acc.id ? 'Teste...' : 'Testen' }}
            </button>
            <button @click="handleFetch(acc)" :disabled="fetchingId === acc.id" class="btn-secondary text-xs px-2 py-1">
              {{ fetchingId === acc.id ? 'Rufe ab...' : 'Jetzt abrufen' }}
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
              <span class="text-gray-400 flex-shrink-0">{{ item.sender }}</span>
              <span class="text-gray-400 flex-shrink-0">{{ formatDateTime(item.created_at) }}</span>
            </div>
          </div>
        </div>
        <div v-if="historyAccountId === acc.id && historyItems.length === 0" class="mt-3 text-xs text-gray-400 text-center py-2">
          Keine verarbeiteten E-Mails vorhanden.
        </div>
      </div>

      <div v-if="accounts.length === 0 && !showForm" class="text-sm text-gray-400 text-center py-4">
        Keine E-Mail-Konten konfiguriert. Fügen Sie ein Konto hinzu, um E-Mails automatisch zu verarbeiten.
      </div>
    </div>

    <!-- Form (inline) -->
    <div v-if="showForm" class="mt-4">
      <EmailAccountForm
        :account="editingAccount"
        :scopes="scopes"
        @save="handleSave"
        @cancel="showForm = false; editingAccount = null"
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
