<script setup>
import { useNotificationStore } from '../../stores/notifications'

const store = useNotificationStore()

const typeClasses = {
  success: 'bg-green-50 text-green-800 border-green-200',
  error: 'bg-red-50 text-red-800 border-red-200',
  info: 'bg-blue-50 text-blue-800 border-blue-200',
}
</script>

<template>
  <div class="fixed bottom-4 right-4 z-50 space-y-2">
    <transition-group name="toast">
      <div
        v-for="n in store.notifications"
        :key="n.id"
        :class="['flex items-center gap-3 rounded-lg border px-4 py-3 shadow-lg text-sm max-w-sm', typeClasses[n.type]]"
      >
        <span class="flex-1">{{ n.message }}</span>
        <button @click="store.remove(n.id)" class="opacity-60 hover:opacity-100">&times;</button>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-enter-active { transition: all 0.3s ease; }
.toast-leave-active { transition: all 0.2s ease; }
.toast-enter-from { opacity: 0; transform: translateX(30px); }
.toast-leave-to { opacity: 0; transform: translateX(30px); }
</style>
