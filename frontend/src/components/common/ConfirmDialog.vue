<script setup>
defineProps({
  show: Boolean,
  title: { type: String, default: 'Bestaetigung' },
  message: String,
  confirmText: { type: String, default: 'Bestaetigen' },
  cancelText: { type: String, default: 'Abbrechen' },
  danger: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <teleport to="body">
    <div v-if="show" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="fixed inset-0 bg-black/40" @click="emit('cancel')"></div>
      <div class="relative z-10 w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h3 class="text-lg font-semibold text-gray-900">{{ title }}</h3>
        <p class="mt-2 text-sm text-gray-600">{{ message }}</p>
        <div class="mt-6 flex justify-end gap-3">
          <button class="btn-secondary" @click="emit('cancel')">{{ cancelText }}</button>
          <button :class="danger ? 'btn-danger' : 'btn-primary'" @click="emit('confirm')">{{ confirmText }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>
