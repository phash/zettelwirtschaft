import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useNotificationStore = defineStore('notifications', () => {
  const notifications = ref([])
  let nextId = 0

  function add(message, type = 'info', duration = 4000) {
    const id = nextId++
    notifications.value.push({ id, message, type })
    if (duration > 0) {
      setTimeout(() => remove(id), duration)
    }
  }

  function remove(id) {
    notifications.value = notifications.value.filter((n) => n.id !== id)
  }

  function success(message) {
    add(message, 'success')
  }

  function error(message) {
    add(message, 'error', 6000)
  }

  function info(message) {
    add(message, 'info')
  }

  return { notifications, add, remove, success, error, info }
})
