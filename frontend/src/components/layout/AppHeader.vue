<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  checkHealth,
  searchSuggest,
  getNotificationCount,
  getNotifications,
  markNotificationRead,
  markAllNotificationsRead,
} from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { NAV_ICONS } from '../../constants/navigation'
import { formatDateTime } from '../../utils/formatters'

const router = useRouter()
const auth = useAuthStore()

const backendOnline = ref(false)
const searchQuery = ref('')
const suggestions = ref([])
const showSuggestions = ref(false)
const unreadCount = ref(0)
const showNotifications = ref(false)
const notifications = ref([])
let healthInterval = null
let suggestTimeout = null
let notifInterval = null
// Jede Eingabe/Suche/Blur erhoeht die Sequenz — aeltere Timer und Antworten
// verfallen (sonst oeffnet ein spaeter Request das Dropdown fuer Text, der
// gar nicht mehr im Feld steht, oder ueber der schon geladenen Suchseite).
let suggestSeq = 0

// Geschuetzte Endpunkte nur abfragen, wenn sie nicht ohnehin 401 liefern.
const canUseApi = computed(() => !auth.pinEnabled || auth.isAuthenticated)
const showLogout = computed(() => auth.pinEnabled && auth.isAuthenticated)

async function checkBackend() {
  const health = await checkHealth()
  backendOnline.value = !!health
}

async function loadNotifCount() {
  if (!canUseApi.value) return
  try {
    unreadCount.value = await getNotificationCount()
  } catch {
    // ignore
  }
}

function startNotifPolling() {
  stopNotifPolling()
  loadNotifCount()
  notifInterval = setInterval(loadNotifCount, 60000)
}

function stopNotifPolling() {
  clearInterval(notifInterval)
  notifInterval = null
}

watch(canUseApi, (allowed) => {
  if (allowed) startNotifPolling()
  else {
    stopNotifPolling()
    showNotifications.value = false
  }
})

async function toggleNotifications() {
  if (showNotifications.value) {
    closeNotifications()
    return
  }
  showNotifications.value = true
  // Badge sofort aktualisieren, nicht erst beim naechsten 60-s-Poll.
  loadNotifCount()
  try {
    notifications.value = await getNotifications()
  } catch {
    notifications.value = []
  }
}

function closeNotifications() {
  showNotifications.value = false
}

async function markAllRead() {
  try {
    await markAllNotificationsRead()
    unreadCount.value = 0
    notifications.value.forEach(n => n.is_read = true)
  } catch {
    // ignore
  }
}

async function openNotification(n) {
  closeNotifications()
  if (!n.is_read) {
    n.is_read = true
    unreadCount.value = Math.max(0, unreadCount.value - 1)
    markNotificationRead(n.id).catch(() => {
      // Zaehler beim naechsten Poll korrigieren
    })
  }
  if (n.document_id) router.push(`/dokumente/${n.document_id}`)
}

function cancelSuggestions() {
  suggestSeq++
  clearTimeout(suggestTimeout)
  suggestTimeout = null
  showSuggestions.value = false
}

function onSearch() {
  const q = searchQuery.value.trim()
  cancelSuggestions()
  if (q) {
    router.push({ name: 'search', query: { q } })
  }
}

function onInput() {
  const q = searchQuery.value.trim()
  cancelSuggestions()
  if (q.length < 2) {
    suggestions.value = []
    return
  }

  const seq = suggestSeq
  suggestTimeout = setTimeout(async () => {
    try {
      const result = await searchSuggest(q)
      if (seq !== suggestSeq || searchQuery.value.trim() !== q) return
      suggestions.value = result
      showSuggestions.value = result.length > 0
    } catch {
      if (seq === suggestSeq) suggestions.value = []
    }
  }, 200)
}

function selectSuggestion(s) {
  searchQuery.value = s
  onSearch()
}

function onSearchBlur() {
  // mousedown.prevent auf den Vorschlaegen haelt den Fokus — ein echter Blur
  // verwirft laufende Vorschlags-Requests.
  cancelSuggestions()
}

function onSearchKeydown(e) {
  if (e.key === 'Enter') onSearch()
  else if (e.key === 'Escape') cancelSuggestions()
}

async function logout() {
  await auth.logout()
  router.replace({ name: 'pin-login' })
}

function onDocumentClick(e) {
  // Notification-Dropdown schliessen bei Klick ausserhalb
  if (showNotifications.value) {
    const dropdown = document.querySelector('[data-notif-dropdown]')
    if (dropdown && !dropdown.contains(e.target)) {
      closeNotifications()
    }
  }
}

function onDocumentKeydown(e) {
  if (e.key === 'Escape' && showNotifications.value) {
    closeNotifications()
    document.querySelector('[data-notif-dropdown] button')?.focus()
  }
}

onMounted(() => {
  checkBackend()
  healthInterval = setInterval(checkBackend, 30000)
  if (canUseApi.value) startNotifPolling()
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onDocumentKeydown)
})

onUnmounted(() => {
  clearInterval(healthInterval)
  stopNotifPolling()
  clearTimeout(suggestTimeout)
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <header class="flex h-16 items-center justify-between border-b border-gray-200 bg-white px-4 sm:px-6">
    <!-- Mobile title: Link zum Dashboard (die Sidebar ist unterhalb lg ausgeblendet) -->
    <router-link to="/" class="flex items-center gap-2 lg:hidden" aria-label="Zettelwirtschaft – zum Dashboard">
      <div class="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-600 text-white font-bold text-sm">Z</div>
      <span class="hidden text-lg font-semibold sm:inline">Zettelwirtschaft</span>
    </router-link>

    <!-- Search bar -->
    <div class="relative mx-3 flex-1 max-w-xl sm:mx-4" role="search">
      <div class="relative">
        <svg class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <input
          v-model="searchQuery"
          type="search"
          placeholder="Dokumente durchsuchen…"
          aria-label="Dokumente durchsuchen"
          aria-autocomplete="list"
          :aria-expanded="showSuggestions"
          aria-controls="header-search-suggestions"
          class="input pl-10"
          @keydown="onSearchKeydown"
          @input="onInput"
          @blur="onSearchBlur"
          @focus="onInput"
        />
      </div>
      <!-- Autocomplete dropdown -->
      <div
        v-if="showSuggestions"
        id="header-search-suggestions"
        role="listbox"
        aria-label="Suchvorschläge"
        class="absolute z-50 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg"
      >
        <button
          v-for="(s, i) in suggestions"
          :key="`${i}-${s}`"
          type="button"
          role="option"
          class="block w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
          @mousedown.prevent="selectSuggestion(s)"
        >
          {{ s }}
        </button>
      </div>
    </div>

    <!-- Notifications + Status -->
    <div class="flex items-center gap-3">
      <!-- Help -->
      <router-link to="/hilfe" class="p-1 text-gray-400 hover:text-gray-600" aria-label="Hilfe" title="Hilfe">
        <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" :d="NAV_ICONS.help" />
        </svg>
      </router-link>

      <!-- Notification bell -->
      <div class="relative" data-notif-dropdown>
        <button
          type="button"
          class="relative p-1 text-gray-400 hover:text-gray-600"
          aria-label="Benachrichtigungen"
          aria-haspopup="true"
          :aria-expanded="showNotifications"
          aria-controls="header-notifications"
          @click="toggleNotifications"
        >
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
          </svg>
          <span v-if="unreadCount > 0" class="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white">
            {{ unreadCount > 9 ? '9+' : unreadCount }}<span class="sr-only"> ungelesen</span>
          </span>
        </button>
        <!-- Dropdown -->
        <div
          v-if="showNotifications"
          id="header-notifications"
          class="absolute right-0 z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 bg-white shadow-lg"
        >
          <div class="flex items-center justify-between border-b border-gray-100 px-4 py-2">
            <span class="text-sm font-semibold text-gray-900">Benachrichtigungen</span>
            <button v-if="unreadCount > 0" type="button" @click="markAllRead" class="text-xs text-primary-600 hover:text-primary-700">Alle gelesen</button>
          </div>
          <ul class="max-h-64 overflow-y-auto">
            <li v-if="notifications.length === 0" class="px-4 py-6 text-center text-sm text-gray-400">
              Keine Benachrichtigungen.
            </li>
            <li v-for="n in notifications" :key="n.id" class="border-b border-gray-50 last:border-0">
              <button
                type="button"
                :class="['block w-full px-4 py-3 text-left hover:bg-gray-50', n.is_read ? '' : 'bg-primary-50/50']"
                :aria-label="n.document_id ? `${n.title} – Dokument öffnen` : `${n.title} – als gelesen markieren`"
                @click="openNotification(n)"
              >
                <span class="block text-sm font-medium text-gray-900">{{ n.title }}</span>
                <span class="mt-0.5 block text-xs text-gray-500">{{ n.message }}</span>
                <span class="mt-1 block text-[11px] text-gray-400">{{ formatDateTime(n.created_at) }}</span>
              </button>
            </li>
          </ul>
        </div>
      </div>

      <!-- Abmelden (nur mit PIN-Schutz sinnvoll) -->
      <button
        v-if="showLogout"
        type="button"
        class="p-1 text-gray-400 hover:text-gray-600"
        aria-label="Abmelden"
        title="Abmelden"
        @click="logout"
      >
        <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" :d="NAV_ICONS.logout" />
        </svg>
      </button>

      <!-- Status indicator -->
      <div class="flex items-center gap-2 text-sm" role="status">
        <span
          :class="[
            'inline-block h-2.5 w-2.5 rounded-full',
            backendOnline ? 'bg-green-500' : 'bg-red-500',
          ]"
          aria-hidden="true"
        ></span>
        <span class="sr-only sm:not-sr-only text-gray-500">
          {{ backendOnline ? 'Verbunden' : 'Offline' }}
        </span>
      </div>
    </div>
  </header>
</template>
