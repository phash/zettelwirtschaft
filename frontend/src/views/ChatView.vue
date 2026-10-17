<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { askQuestion, getChatHistory, clearChatHistory } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { useFilingScopes } from '../composables/useFilingScopes'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import { formatDateTime } from '../utils/formatters'
import { apiErrorMessage } from '../utils/apiErrors'

const notify = useNotificationStore()
const messages = ref([])
const question = ref('')
const loading = ref(false)
const loadingHistory = ref(true)
// H-FE-5: scopes aus dem shared Composable — Cache verhindert redundante Calls.
const { scopes, ensureLoaded } = useFilingScopes()
const selectedScopeId = ref(null)
const messagesContainer = ref(null)
const showClearConfirm = ref(false)

// Verlauf seitenweise: der Server liefert die neuesten HISTORY_PAGE Nachrichten,
// "Ältere laden" holt weitere per offset. historyOffset = Anzahl der bereits
// geladenen bzw. seitdem hinzugekommenen Server-Nachrichten.
const HISTORY_PAGE = 50
const historyTotal = ref(0)
const historyOffset = ref(0)
const loadingOlder = ref(false)
const hasOlder = computed(() => historyOffset.value < historyTotal.value)

const exampleQuestions = [
  'Wie viel haben wir dieses Jahr für Versicherungen ausgegeben?',
  'Wann läuft die Garantie für die Waschmaschine ab?',
  'Welche Handwerkerrechnungen hatten wir letztes Jahr?',
  'Was steht in meinem letzten Steuerbescheid?',
]

async function loadHistory() {
  loadingHistory.value = true
  try {
    const data = await getChatHistory(HISTORY_PAGE, 0)
    messages.value = data.messages
    historyTotal.value = data.total ?? data.messages.length
    historyOffset.value = data.messages.length
  } catch {
    // ignore
  } finally {
    loadingHistory.value = false
    await scrollToBottom()
  }
}

async function loadOlder() {
  if (loadingOlder.value) return
  loadingOlder.value = true
  const container = messagesContainer.value
  const prevHeight = container?.scrollHeight ?? 0
  try {
    const data = await getChatHistory(HISTORY_PAGE, historyOffset.value)
    // Doppelte vermeiden (z.B. wenn eine abgebrochene Frage inzwischen
    // serverseitig gespeichert wurde und sich der offset verschoben hat).
    const known = new Set(messages.value.map(m => m.id))
    const older = data.messages.filter(m => !known.has(m.id))
    messages.value = [...older, ...messages.value]
    historyTotal.value = data.total ?? historyTotal.value
    historyOffset.value = data.messages.length
      ? historyOffset.value + data.messages.length
      : historyTotal.value
    // Scrollposition halten: die bisher oberste Nachricht bleibt sichtbar.
    await nextTick()
    if (container) container.scrollTop = container.scrollHeight - prevHeight
  } catch {
    notify.error('Ältere Nachrichten konnten nicht geladen werden.')
  } finally {
    loadingOlder.value = false
  }
}

// AbortController fuer laufende Frage — User kann via "Abbrechen"-Button
// (L-36) den Request stornieren. Pro Frage neu erstellt, beim Abbruch oder
// Erfolg auf null zurueckgesetzt.
let activeController = null

function cancelQuestion() {
  if (activeController) {
    activeController.abort()
    activeController = null
  }
}

async function sendQuestion(text = null) {
  const q = text || question.value.trim()
  if (!q || loading.value) return

  question.value = ''

  // Optimistisch die Benutzer-Nachricht anzeigen
  messages.value.push({
    id: 'temp-user-' + Date.now(),
    role: 'user',
    content: q,
    sources: [],
    created_at: new Date().toISOString(),
  })
  await scrollToBottom()

  loading.value = true
  activeController = new AbortController()
  try {
    const result = await askQuestion(q, selectedScopeId.value, activeController.signal)
    messages.value.push({
      id: 'temp-assistant-' + Date.now(),
      role: 'assistant',
      content: result.answer,
      sources: result.sources || [],
      created_at: new Date().toISOString(),
    })
    // Frage + Antwort liegen jetzt auch serverseitig im Verlauf.
    historyOffset.value += 2
    historyTotal.value += 2
  } catch (e) {
    // Bei Abbruch und Zeitüberschreitung rechnet das Backend ggf. weiter und
    // speichert die Antwort später im Verlauf — darauf hinweisen und das
    // Nachladen anbieten, statt einen Fehler zu behaupten.
    let errorMsg
    let mayStillAnswer = false
    if (e.code === 'ERR_CANCELED' || e.name === 'CanceledError') {
      errorMsg = 'Anfrage abgebrochen. Falls der Assistent schon an der Antwort gearbeitet hat, kann sie später im Verlauf erscheinen.'
      mayStillAnswer = true
    } else if (e.code === 'ECONNABORTED' || e.code === 'ETIMEDOUT') {
      errorMsg = 'Die Antwort dauert länger als erwartet. Der Assistent arbeitet im Hintergrund weiter — lade den Verlauf in ein paar Minuten neu.'
      mayStillAnswer = true
    } else if (e.response?.status === 503) {
      errorMsg = apiErrorMessage(e, 'Der KI-Assistent ist derzeit nicht verfügbar. Prüfe, ob Ollama und die Vektor-Datenbank laufen.')
    } else {
      errorMsg = apiErrorMessage(e, 'Fehler bei der Verarbeitung. Bitte versuche es erneut.')
    }
    messages.value.push({
      id: 'temp-error-' + Date.now(),
      role: 'assistant',
      content: errorMsg,
      sources: [],
      created_at: new Date().toISOString(),
      isError: true,
      offerReload: mayStillAnswer,
    })
  } finally {
    loading.value = false
    activeController = null
    await scrollToBottom()
  }
}

async function doClearHistory() {
  showClearConfirm.value = false
  try {
    await clearChatHistory()
    messages.value = []
    historyTotal.value = 0
    historyOffset.value = 0
    notify.success('Chatverlauf gelöscht.')
  } catch {
    notify.error('Löschen fehlgeschlagen.')
  }
}

async function scrollToBottom() {
  await nextTick()
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

const MESSAGE_TIME_FORMAT = { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }

onMounted(async () => {
  await Promise.all([loadHistory(), ensureLoaded()])
})
</script>

<template>
  <div class="flex flex-col h-[calc(100vh-8rem)] md:h-[calc(100vh-4rem)] lg:h-[calc(100vh-2rem)]">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white flex-shrink-0">
      <div class="flex items-center gap-3">
        <h1 class="text-lg font-bold text-gray-900">KI-Assistent</h1>
        <select
          v-if="scopes.length > 1"
          v-model="selectedScopeId"
          class="input !py-1 !text-sm !w-auto"
          aria-label="Ablagebereich für die Frage"
        >
          <option :value="null">Alle Bereiche</option>
          <option v-for="scope in scopes" :key="scope.id" :value="scope.id">
            {{ scope.name }}
          </option>
        </select>
      </div>
      <button
        v-if="messages.length > 0"
        @click="showClearConfirm = true"
        class="text-sm text-gray-400 hover:text-red-500 transition-colors"
      >
        Verlauf löschen
      </button>
    </div>

    <!-- Messages -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto px-4 py-4 space-y-4">
      <!-- Loading History -->
      <div v-if="loadingHistory" class="flex justify-center py-8">
        <div class="h-8 w-8 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
      </div>

      <!-- Empty State -->
      <div v-else-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-center">
        <div class="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary-100 text-primary-600 mb-4">
          <svg class="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
        </div>
        <h2 class="text-lg font-semibold text-gray-900 mb-1">Frag deine Dokumente</h2>
        <p class="text-sm text-gray-500 mb-6 max-w-md">
          Stelle natürlichsprachige Fragen zu deinen archivierten Dokumenten. Der KI-Assistent durchsucht relevante Dokumente und gibt dir eine Antwort.
        </p>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
          <button
            v-for="eq in exampleQuestions"
            :key="eq"
            @click="sendQuestion(eq)"
            class="text-left text-sm px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:border-primary-300 hover:text-primary-700 hover:bg-primary-50 transition-colors"
          >
            {{ eq }}
          </button>
        </div>
      </div>

      <!-- Message List -->
      <template v-else>
        <div v-if="hasOlder" class="flex justify-center">
          <button
            type="button"
            @click="loadOlder"
            :disabled="loadingOlder"
            class="text-xs text-primary-600 hover:text-primary-700 underline disabled:opacity-50"
          >
            {{ loadingOlder ? 'Lade …' : 'Ältere Nachrichten laden' }}
          </button>
        </div>
        <div
          v-for="msg in messages"
          :key="msg.id"
          :class="[
            'flex',
            msg.role === 'user' ? 'justify-end' : 'justify-start',
          ]"
        >
          <div
            :class="[
              'max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3',
              msg.role === 'user'
                ? 'bg-primary-600 text-white'
                : 'bg-gray-100 text-gray-900',
            ]"
          >
            <p class="text-sm whitespace-pre-wrap" :role="msg.isError ? 'alert' : undefined">{{ msg.content }}</p>
            <button
              v-if="msg.offerReload"
              type="button"
              @click="loadHistory"
              class="mt-2 text-xs text-primary-700 underline hover:text-primary-800"
            >
              Verlauf neu laden
            </button>
            <!-- Sources -->
            <div v-if="msg.sources?.length" class="mt-2 pt-2 border-t" :class="msg.role === 'user' ? 'border-primary-400' : 'border-gray-200'">
              <p class="text-xs font-medium mb-1" :class="msg.role === 'user' ? 'text-primary-200' : 'text-gray-500'">Quellen:</p>
              <div class="flex flex-wrap gap-1">
                <router-link
                  v-for="(source, idx) in msg.sources"
                  :key="source.document_id"
                  :to="`/dokumente/${source.document_id}`"
                  class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full transition-colors"
                  :class="msg.role === 'user'
                    ? 'bg-primary-500 text-primary-100 hover:bg-primary-400'
                    : 'bg-white text-primary-700 hover:bg-primary-50 border border-gray-200'"
                >
                  <span class="font-medium">{{ idx + 1 }}.</span>
                  <span class="truncate max-w-[150px]">{{ source.title }}</span>
                </router-link>
              </div>
            </div>
            <p class="text-[10px] mt-1 text-right" :class="msg.role === 'user' ? 'text-primary-300' : 'text-gray-400'">
              {{ formatDateTime(msg.created_at, MESSAGE_TIME_FORMAT) }}
            </p>
          </div>
        </div>

        <!-- Typing Indicator -->
        <div v-if="loading" class="flex justify-start">
          <div class="bg-gray-100 rounded-2xl px-4 py-3">
            <div class="flex gap-1">
              <span class="h-2 w-2 rounded-full bg-gray-400 animate-bounce" style="animation-delay: 0ms"></span>
              <span class="h-2 w-2 rounded-full bg-gray-400 animate-bounce" style="animation-delay: 150ms"></span>
              <span class="h-2 w-2 rounded-full bg-gray-400 animate-bounce" style="animation-delay: 300ms"></span>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Input -->
    <div class="border-t border-gray-200 bg-white px-4 py-3 flex-shrink-0">
      <form @submit.prevent="sendQuestion()" class="flex gap-2">
        <input
          v-model="question"
          type="text"
          placeholder="Stelle eine Frage zu deinen Dokumenten..."
          aria-label="Frage an den Assistenten"
          class="input flex-1"
          :disabled="loading"
          maxlength="2000"
        />
        <button
          v-if="!loading"
          type="submit"
          :disabled="!question.trim()"
          class="btn-primary !px-4 flex-shrink-0"
          aria-label="Frage abschicken"
        >
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
        </button>
        <button
          v-else
          type="button"
          @click="cancelQuestion"
          class="btn-secondary !px-4 flex-shrink-0"
          aria-label="Anfrage abbrechen"
          title="Anfrage abbrechen"
        >
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </form>
    </div>

    <ConfirmDialog
      :show="showClearConfirm"
      title="Chatverlauf löschen"
      message="Soll der gesamte Chatverlauf wirklich gelöscht werden? Das kann nicht rückgängig gemacht werden."
      confirm-text="Löschen"
      :danger="true"
      @confirm="doClearHistory"
      @cancel="showClearConfirm = false"
    />
  </div>
</template>
