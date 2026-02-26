<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { askQuestion, getChatHistory, clearChatHistory, getFilingScopes } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

const notify = useNotificationStore()
const messages = ref([])
const question = ref('')
const loading = ref(false)
const loadingHistory = ref(true)
const scopes = ref([])
const selectedScopeId = ref(null)
const messagesContainer = ref(null)

const exampleQuestions = [
  'Wie viel haben wir dieses Jahr fuer Versicherungen ausgegeben?',
  'Wann laeuft die Garantie fuer die Waschmaschine ab?',
  'Welche Handwerkerrechnungen hatten wir letztes Jahr?',
  'Was steht in meinem letzten Steuerbescheid?',
]

async function loadHistory() {
  loadingHistory.value = true
  try {
    const data = await getChatHistory()
    messages.value = data.messages
  } catch {
    // ignore
  } finally {
    loadingHistory.value = false
    await scrollToBottom()
  }
}

async function loadScopes() {
  try {
    scopes.value = await getFilingScopes()
  } catch {
    // ignore
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
  try {
    const result = await askQuestion(q, selectedScopeId.value)
    messages.value.push({
      id: 'temp-assistant-' + Date.now(),
      role: 'assistant',
      content: result.answer,
      sources: result.sources || [],
      created_at: new Date().toISOString(),
    })
  } catch (e) {
    const errorMsg = e.response?.status === 503
      ? 'Der KI-Assistent ist derzeit nicht verfuegbar. Pruefe ob Ollama und ChromaDB laufen.'
      : 'Fehler bei der Verarbeitung. Bitte versuche es erneut.'
    messages.value.push({
      id: 'temp-error-' + Date.now(),
      role: 'assistant',
      content: errorMsg,
      sources: [],
      created_at: new Date().toISOString(),
    })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

async function doClearHistory() {
  try {
    await clearChatHistory()
    messages.value = []
    notify.success('Chatverlauf geloescht.')
  } catch {
    notify.error('Loeschen fehlgeschlagen.')
  }
}

async function scrollToBottom() {
  await nextTick()
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

function formatDate(d) {
  if (!d) return ''
  return new Date(d).toLocaleString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

onMounted(async () => {
  await Promise.all([loadHistory(), loadScopes()])
})
</script>

<template>
  <div class="flex flex-col h-[calc(100vh-4rem)] lg:h-[calc(100vh-2rem)]">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white flex-shrink-0">
      <div class="flex items-center gap-3">
        <h1 class="text-lg font-bold text-gray-900">KI-Assistent</h1>
        <select
          v-if="scopes.length > 1"
          v-model="selectedScopeId"
          class="input !py-1 !text-sm !w-auto"
        >
          <option :value="null">Alle Bereiche</option>
          <option v-for="scope in scopes" :key="scope.id" :value="scope.id">
            {{ scope.name }}
          </option>
        </select>
      </div>
      <button
        v-if="messages.length > 0"
        @click="doClearHistory"
        class="text-sm text-gray-400 hover:text-red-500 transition-colors"
      >
        Verlauf loeschen
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
          Stelle natuerlichsprachige Fragen zu deinen archivierten Dokumenten. Der KI-Assistent durchsucht relevante Dokumente und gibt dir eine Antwort.
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
            <p class="text-sm whitespace-pre-wrap">{{ msg.content }}</p>
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
              {{ formatDate(msg.created_at) }}
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
          class="input flex-1"
          :disabled="loading"
          maxlength="2000"
        />
        <button
          type="submit"
          :disabled="!question.trim() || loading"
          class="btn-primary !px-4 flex-shrink-0"
        >
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
        </button>
      </form>
    </div>
  </div>
</template>
