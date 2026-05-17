<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-100 px-4">
    <div class="w-full max-w-sm bg-white rounded-lg shadow-md p-8">
      <div class="text-center mb-6">
        <h1 class="text-2xl font-bold text-gray-800">Zettelwirtschaft</h1>
        <p class="text-gray-500 mt-1">Bitte PIN eingeben</p>
      </div>

      <form @submit.prevent="handleLogin" class="space-y-4">
        <div>
          <label for="pin" class="block text-sm font-medium text-gray-700 mb-1">PIN</label>
          <input
            id="pin"
            ref="pinInput"
            v-model="pin"
            type="password"
            inputmode="numeric"
            autocomplete="current-password"
            class="input w-full text-center text-2xl tracking-widest"
            placeholder="****"
            :disabled="auth.loading"
          />
        </div>

        <div v-if="error" class="text-red-600 text-sm text-center">
          {{ error }}
        </div>

        <button
          type="submit"
          class="btn btn-primary w-full"
          :disabled="!pin || auth.loading"
        >
          {{ auth.loading ? 'Pruefen...' : 'Anmelden' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const pin = ref('')
const error = ref('')
const pinInput = ref(null)

onMounted(() => {
  pinInput.value?.focus()
})

// H-FE-1: redirect-Param muss einem internen Pfad entsprechen. Werte wie
// `https://evil.com`, `//evil.com/x`, oder `javascript:` werden ignoriert und
// fallen auf `/` zurueck. Vue-Router interpretiert "//evil" als Pfad, aber
// das LAN-Risiko reicht fuer die Whitelist-Pruefung.
function safeRedirect(raw) {
  if (typeof raw !== 'string' || raw.length === 0) return '/'
  if (!raw.startsWith('/')) return '/'
  if (raw.startsWith('//')) return '/'
  if (raw.startsWith('/\\')) return '/'
  return raw
}

async function handleLogin() {
  error.value = ''
  const result = await auth.login(pin.value)
  if (result.success) {
    router.replace(safeRedirect(router.currentRoute.value.query.redirect))
  } else {
    error.value = result.detail
    pin.value = ''
    pinInput.value?.focus()
  }
}
</script>
