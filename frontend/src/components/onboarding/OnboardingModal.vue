<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useOnboardingStore } from '../../stores/onboarding'

const onboarding = useOnboardingStore()
const router = useRouter()
const step = ref(0)
const panel = ref(null)
let lastFocused = null

const steps = [
  {
    icon: 'sparkles',
    title: 'Willkommen bei Zettelwirtschaft',
    text: 'Dein persönliches Dokumentenarchiv mit KI. Rechnungen, Belege und Verträge werden automatisch erkannt, einsortiert und durchsuchbar — alles bleibt lokal bei dir, ganz ohne Cloud.',
  },
  {
    icon: 'upload',
    title: 'Dokumente erfassen',
    text: 'Lade Dateien per Drag & Drop hoch, scanne sie mit dem Smartphone, lege sie in den Watch-Ordner oder lass sie automatisch aus deinem E-Mail-Postfach abholen.',
  },
  {
    icon: 'badge',
    title: 'Die KI ordnet automatisch',
    text: 'Eine lokale KI liest den Text (OCR) und erkennt Typ, Datum, Betrag und Aussteller. Ist etwas unklar, fragt sie unter „Zu prüfen“ gezielt nach — du behältst die Kontrolle.',
  },
  {
    icon: 'search',
    title: 'Finden & nutzen',
    text: 'Durchsuche alles per Volltext, stell dem KI-Assistenten Fragen zu deinen Dokumenten, exportiere Steuerbelege als ZIP und behalte Garantien im Blick.',
  },
  {
    icon: 'send',
    title: 'Bereit loszulegen?',
    text: 'Am besten startest du mit deinem ersten Dokument. Die Hilfe erreichst du jederzeit über das Fragezeichen oben rechts oder „Hilfe“ in der Seitenleiste.',
  },
]

const current = computed(() => steps[step.value])
const isLast = computed(() => step.value === steps.length - 1)

function next() {
  if (isLast.value) finish()
  else step.value++
}
function back() {
  if (step.value > 0) step.value--
}
function finish() {
  onboarding.finish()
  step.value = 0
}
function goUpload() {
  finish()
  router.push('/upload')
}

function onKey(e) {
  if (e.key === 'Escape') finish()
  else if (e.key === 'ArrowRight') next()
  else if (e.key === 'ArrowLeft') back()
}

// Keydown-Listener nur waehrend die Tour offen ist (nicht app-weit), plus
// Fokus-Management: beim Oeffnen Fokus in den Dialog, beim Schliessen zurueck.
watch(
  () => onboarding.open,
  (isOpen) => {
    if (isOpen) {
      lastFocused = document.activeElement
      document.addEventListener('keydown', onKey)
      nextTick(() => panel.value?.focus())
    } else {
      document.removeEventListener('keydown', onKey)
      if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus()
      lastFocused = null
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))

const icons = {
  sparkles: 'M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456z',
  upload: 'M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5',
  badge: 'M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.746 3.746 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z',
  search: 'M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z',
  send: 'M6 12L3.269 3.125A59.769 59.769 0 0121.485 12 59.768 59.768 0 013.27 20.875L5.999 12zm0 0h7.5',
}
</script>

<template>
  <teleport to="body">
    <div v-if="onboarding.open" class="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <!-- Backdrop bewusst OHNE Klick-zum-Schliessen: ein versehentlicher Klick
           daneben soll die Erst-Tour nicht dauerhaft verwerfen (Review M3).
           Schliessen via X / Überspringen / Später / Esc. -->
      <div class="fixed inset-0 bg-black/50" aria-hidden="true"></div>

      <div
        ref="panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="onb-title"
        tabindex="-1"
        class="relative z-10 w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl focus:outline-none"
      >
        <button
          @click="finish"
          class="absolute right-4 top-4 rounded-md p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
          aria-label="Tour schließen"
        >
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div class="flex flex-col items-center px-8 pt-12 pb-6 text-center">
          <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary-50 text-primary-600">
            <svg class="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" :d="icons[current.icon]" />
            </svg>
          </div>
          <p class="text-xs font-semibold uppercase tracking-wide text-primary-600">
            Schritt {{ step + 1 }} von {{ steps.length }}
          </p>
          <h2 id="onb-title" class="mt-2 text-2xl font-bold text-gray-900">{{ current.title }}</h2>
          <p class="mt-3 leading-relaxed text-gray-600">{{ current.text }}</p>
        </div>

        <div class="flex justify-center gap-1.5 pb-6">
          <button
            v-for="(s, i) in steps"
            :key="i"
            @click="step = i"
            :class="['h-2 rounded-full transition-all', i === step ? 'w-6 bg-primary-600' : 'w-2 bg-gray-300 hover:bg-gray-400']"
            :aria-label="`Zu Schritt ${i + 1}`"
          ></button>
        </div>

        <div class="flex items-center justify-between gap-3 border-t border-gray-100 bg-gray-50 px-6 py-4">
          <button v-if="step > 0" @click="back" class="btn-secondary">Zurück</button>
          <button v-else @click="finish" class="text-sm text-gray-500 hover:text-gray-700">Überspringen</button>

          <div class="flex gap-2">
            <template v-if="isLast">
              <button @click="finish" class="btn-secondary">Später</button>
              <button @click="goUpload" class="btn-primary">Erstes Dokument hochladen</button>
            </template>
            <button v-else @click="next" class="btn-primary">Weiter</button>
          </div>
        </div>
      </div>
    </div>
  </teleport>
</template>
