<script setup>
import { ref, watch, nextTick, onBeforeUnmount, useId } from 'vue'
import { trapTabKey } from '../../utils/focusTrap'

const props = defineProps({
  show: Boolean,
  title: { type: String, default: 'Bestätigung' },
  message: String,
  confirmText: { type: String, default: 'Bestätigen' },
  cancelText: { type: String, default: 'Abbrechen' },
  danger: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])

const uid = useId()
const titleId = `confirm-title-${uid}`
const messageId = `confirm-message-${uid}`
const panel = ref(null)
const cancelButton = ref(null)
let previousFocus = null

// Tastatur: Fokus startet auf "Abbrechen" (sichere Wahl bei destruktiven
// Aktionen), Escape bricht ab, Tab bleibt im Dialog, danach Fokus zurueck.
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
  () => props.show,
  async (visible) => {
    if (visible) {
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
  if (props.show) deactivate()
})
</script>

<template>
  <teleport to="body">
    <div v-if="show" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="fixed inset-0 bg-black/40" aria-hidden="true" @click="emit('cancel')"></div>
      <div
        ref="panel"
        role="alertdialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="message ? messageId : undefined"
        class="relative z-10 w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
      >
        <h3 :id="titleId" class="text-lg font-semibold text-gray-900">{{ title }}</h3>
        <p v-if="message" :id="messageId" class="mt-2 text-sm text-gray-600">{{ message }}</p>
        <div class="mt-6 flex justify-end gap-3">
          <button ref="cancelButton" type="button" class="btn-secondary" @click="emit('cancel')">{{ cancelText }}</button>
          <button type="button" :class="danger ? 'btn-danger' : 'btn-primary'" @click="emit('confirm')">{{ confirmText }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>
