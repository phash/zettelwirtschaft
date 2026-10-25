<script setup>
// Bestätigungsdialog für "Neu analysieren" (einzeln oder als Massenlauf).
// Mit `preview` zeigt er zusätzlich, was ein Lauf erfassen würde; bei Änderung
// der OCR-Option emittiert er `ocr-change`, damit der Aufrufer die Vorschau
// neu lädt.
import { computed, ref, watch, nextTick, onBeforeUnmount, useId } from 'vue'
import { trapTabKey } from '../../utils/focusTrap'
import { pluralize } from '../../utils/pluralize'

const props = defineProps({
  open: Boolean,
  title: { type: String, default: 'Neu analysieren' },
  preview: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel', 'ocr-change'])

const uid = useId()
const titleId = `reanalyze-title-${uid}`
const panel = ref(null)
const cancelButton = ref(null)
const withOcr = ref(false)
let previousFocus = null

const canConfirm = computed(() => !props.busy && (props.preview == null || props.preview.eligible > 0))

function duration(seconds) {
  if (seconds < 60) return `ca. ${Math.max(1, Math.round(seconds))} Sek.`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `ca. ${minutes} Min.`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `ca. ${hours} Std. ${rest} Min.` : `ca. ${hours} Std.`
}

function onOcrChange(event) {
  withOcr.value = event.target.checked
  emit('ocr-change', withOcr.value)
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('cancel')
    return
  }
  trapTabKey(e, panel.value)
}

function deactivate() {
  document.removeEventListener('keydown', onKeydown)
  if (previousFocus && typeof previousFocus.focus === 'function' && document.contains(previousFocus)) {
    previousFocus.focus()
  }
  previousFocus = null
}

watch(
  () => props.open,
  async (visible) => {
    if (visible) {
      withOcr.value = false  // Standard bei jedem Öffnen: aus
      previousFocus = document.activeElement
      document.addEventListener('keydown', onKeydown)
      await nextTick()
      cancelButton.value?.focus()
    } else {
      deactivate()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (props.open) deactivate()
})
</script>

<template>
  <teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div class="fixed inset-0 bg-black/40" aria-hidden="true" @click="emit('cancel')"></div>
      <div
        ref="panel"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        class="relative z-10 w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
      >
        <h3 :id="titleId" class="text-lg font-semibold text-gray-900">{{ title }}</h3>
        <p class="mt-2 text-sm text-gray-600">
          Die KI analysiert den Text erneut. Das Ergebnis erscheint als Vorschlag — nichts wird ohne deine Bestätigung geändert.
        </p>

        <label class="mt-4 flex items-start gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            class="mt-0.5 rounded border-gray-300"
            :checked="withOcr"
            @change="onOcrChange"
          />
          <span>Text neu erkennen (OCR) — dauert länger</span>
        </label>

        <ul v-if="preview" class="mt-4 space-y-1 text-sm text-gray-700" data-testid="reanalyze-preview">
          <li>{{ pluralize(preview.eligible, 'Dokument', 'Dokumente') }}, {{ duration(preview.estimate_seconds) }}</li>
          <li v-if="preview.reviewed_excluded > 0" class="text-gray-500">
            {{ pluralize(preview.reviewed_excluded, 'freigegebenes bleibt', 'freigegebene bleiben') }} unverändert
          </li>
          <li v-if="preview.no_text_excluded > 0" class="text-gray-500">
            {{ preview.no_text_excluded }} ohne erkannten Text — nur mit Neu-Erkennung
          </li>
        </ul>

        <div class="mt-6 flex justify-end gap-3">
          <button ref="cancelButton" type="button" class="btn-secondary" @click="emit('cancel')">Abbrechen</button>
          <button type="button" class="btn-primary" :disabled="!canConfirm" @click="emit('confirm', { withOcr })">Neu analysieren</button>
        </div>
      </div>
    </div>
  </teleport>
</template>
