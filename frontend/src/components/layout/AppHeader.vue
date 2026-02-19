<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { checkHealth, searchSuggest, getNotificationCount, getNotifications, markAllNotificationsRead } from '../../services/api'

const router = useRouter()
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

async function checkBackend() {
  const health = await checkHealth()
  backendOnline.value = !!health
}

async function loadNotifCount() {
  try {
    unreadCount.value = await getNotificationCount()
  } catch {
    // ignore
  }
}

async function toggleNotifications() {
  showNotifications.value = !showNotifications.value
  if (showNotifications.value) {
    try {
      notifications.value = await getNotifications()
    } catch {
      notifications.value = []
    }
  }
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

function onSearch() {
  if (searchQuery.value.trim()) {
    showSuggestions.value = false
    router.push({ name: 'search', query: { q: searchQuery.value.trim() } })
  }
}

async function onInput() {
  const q = searchQuery.value.trim()
  if (q.length < 2) {
    suggestions.value = []
    showSuggestions.value = false
    return
  }

  clearTimeout(suggestTimeout)
  suggestTimeout = setTimeout(async () => {
    try {
      suggestions.value = await searchSuggest(q)
      showSuggestions.value = suggestions.value.length > 0
    } catch {
      suggestions.value = []
    }
  }, 200)
}

function selectSuggestion(s) {
  searchQuery.value = s
  showSuggestions.value = false
  onSearch()
}

function hideSuggestions() {
  setTimeout(() => { showSuggestions.value = false }, 200)
}

onMounted(() => {
  checkBackend()
  healthInterval = setInterval(checkBackend, 30000)
  loadNotifCount()
  notifInterval = setInterval(loadNotifCount, 60000)
})

onUnmounted(() => {
  clearInterval(healthInterval)
  clearInterval(notifInterval)
})
</script>

<template>
  <header class="flex h-16 items-center justify-between border-b border-gray-200 bg-white px-6">
    <!-- Mobile title -->
    <div class="flex items-center gap-2 lg:hidden">
      <div class="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-600 text-white font-bold text-sm">Z</div>
      <span class="text-lg font-semibold">Zettelwirtschaft</span>
    </div>

    <!-- Search bar -->
    <div class="relative mx-4 flex-1 max-w-xl">
      <div class="relative">
        <svg class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <input
          v-model="searchQuery"
          type="text"
          placeholder="Dokumente durchsuchen..."
          class="input pl-10"
          @keydown.enter="onSearch"
          @input="onInput"
          @blur="hideSuggestions"
          @focus="onInput"
        />
      </div>
      <!-- Autocomplete dropdown -->
      <div v-if="showSuggestions" class="absolute z-50 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg">
        <button
          v-for="s in suggestions"
          :key="s"
          class="block w-full px-4 py-2 text-left text-sm hover:bg-gray-50"
          @mousedown.prevent="selectSuggestion(s)"
        >
          {{ s }}
        </button>
      </div>
    </div>

    <!-- Notifications + Status -->
    <div class="flex items-center gap-3">
      <!-- Notification bell -->
      <div class="relative">
        <button @click="toggleNotifications" class="relative p-1 text-gray-400 hover:text-gray-600">
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
          </svg>
          <span v-if="unreadCount > 0" class="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white">
            {{ unreadCount > 9 ? '9+' : unreadCount }}
          </span>
        </button>
        <!-- Dropdown -->
        <div v-if="showNotifications" class="absolute right-0 z-50 mt-2 w-80 rounded-lg border border-gray-200 bg-white shadow-lg">
          <div class="flex items-center justify-between border-b border-gray-100 px-4 py-2">
            <span class="text-sm font-semibold text-gray-900">Benachrichtigungen</span>
            <button v-if="unreadCount > 0" @click="markAllRead" class="text-xs text-primary-600 hover:text-primary-700">Alle gelesen</button>
          </div>
          <div class="max-h-64 overflow-y-auto">
            <div v-if="notifications.length === 0" class="px-4 py-6 text-center text-sm text-gray-400">
              Keine Benachrichtigungen.
            </div>
            <div
              v-for="n in notifications"
              :key="n.id"
              :class="['px-4 py-3 border-b border-gray-50 last:border-0', n.is_read ? '' : 'bg-primary-50/50']"
            >
              <p class="text-sm font-medium text-gray-900">{{ n.title }}</p>
              <p class="text-xs text-gray-500 mt-0.5">{{ n.message }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Status indicator -->
      <div class="flex items-center gap-2 text-sm">
        <span
          :class="[
            'inline-block h-2.5 w-2.5 rounded-full',
            backendOnline ? 'bg-green-500' : 'bg-red-500',
          ]"
        ></span>
        <span class="hidden text-gray-500 sm:inline">
          {{ backendOnline ? 'Verbunden' : 'Offline' }}
        </span>
      </div>
    </div>
  </header>
</template>
