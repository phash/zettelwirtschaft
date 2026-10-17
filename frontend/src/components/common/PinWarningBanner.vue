<script setup>
import { ref } from 'vue'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()

// "Nur fuer diese Sitzung" = Browser-Sitzung (Tab): sessionStorage ueberlebt
// Reload und Deep-Links, nicht aber das Schliessen des Tabs. Ohne Storage
// (privater Modus, blockiert) bleibt es beim Ausblenden bis zum Reload.
const STORAGE_KEY = 'zw_pin_warning_dismissed'

function readDismissed() {
  try {
    return sessionStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

const dismissed = ref(readDismissed())

function dismiss() {
  dismissed.value = true
  try {
    sessionStorage.setItem(STORAGE_KEY, '1')
  } catch {
    // ignore
  }
}
</script>

<template>
  <div
    v-if="auth.pinWarning && !dismissed"
    role="alert"
    class="border-b border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
  >
    <div class="mx-auto flex max-w-7xl items-start gap-3">
      <svg
        class="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-600"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
        />
      </svg>
      <div class="flex-1">
        <p class="font-semibold">PIN-Schutz ist deaktiviert</p>
        <!-- Smartphone: Kurzfassung mit Link zur Hilfe statt Konfig-Details -->
        <p class="mt-1 sm:hidden">
          Jedes Ger&auml;t im Heimnetz kann Dokumente sehen und l&ouml;schen.
          <router-link to="/hilfe" class="font-medium underline">Mehr erfahren</router-link>
        </p>
        <p class="mt-1 hidden sm:block">
          Das System l&auml;uft ohne Passwort &mdash; jedes Ger&auml;t im Heimnetz kann Dokumente
          sehen, l&ouml;schen oder exportieren.
          Aktivierung: in der Konfiguration
          (<code class="rounded bg-amber-100 px-1 font-mono text-xs">.env</code> bzw. bei der
          Native-Installation <code class="rounded bg-amber-100 px-1 font-mono text-xs">config.toml</code>)
          <code class="rounded bg-amber-100 px-1 font-mono text-xs">PIN_ENABLED=true</code> und
          <code class="rounded bg-amber-100 px-1 font-mono text-xs">PIN_CODE=&lt;4-6 Stellen&gt;</code>
          setzen, danach Backend neu starten.
          <router-link to="/hilfe" class="font-medium underline">Hilfe</router-link>
        </p>
      </div>
      <button
        type="button"
        class="rounded text-amber-700 hover:bg-amber-100 hover:text-amber-900 focus:outline-none focus:ring-2 focus:ring-amber-500"
        aria-label="Hinweis ausblenden (nur f&uuml;r diese Sitzung)"
        @click="dismiss"
      >
        <svg class="h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  </div>
</template>
