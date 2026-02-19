<script setup>
import { computed } from 'vue'

const props = defineProps({
  total: Number,
  page: Number,
  pageSize: Number,
})

const emit = defineEmits(['update:page'])

const totalPages = computed(() => Math.ceil(props.total / props.pageSize))

const pages = computed(() => {
  const items = []
  const tp = totalPages.value
  const cp = props.page

  if (tp <= 7) {
    for (let i = 1; i <= tp; i++) items.push(i)
  } else {
    items.push(1)
    if (cp > 3) items.push('...')
    for (let i = Math.max(2, cp - 1); i <= Math.min(tp - 1, cp + 1); i++) {
      items.push(i)
    }
    if (cp < tp - 2) items.push('...')
    items.push(tp)
  }
  return items
})
</script>

<template>
  <div v-if="totalPages > 1" class="flex items-center justify-between">
    <p class="text-sm text-gray-500">
      {{ (page - 1) * pageSize + 1 }}-{{ Math.min(page * pageSize, total) }} von {{ total }}
    </p>
    <nav class="flex gap-1">
      <button
        :disabled="page <= 1"
        class="btn-secondary !px-3 !py-1.5 disabled:opacity-40"
        @click="emit('update:page', page - 1)"
      >&laquo;</button>
      <template v-for="p in pages" :key="p">
        <button
          v-if="p === '...'"
          class="px-3 py-1.5 text-sm text-gray-400"
          disabled
        >...</button>
        <button
          v-else
          :class="[
            'rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
            p === page
              ? 'bg-primary-600 text-white'
              : 'text-gray-600 hover:bg-gray-100',
          ]"
          @click="emit('update:page', p)"
        >{{ p }}</button>
      </template>
      <button
        :disabled="page >= totalPages"
        class="btn-secondary !px-3 !py-1.5 disabled:opacity-40"
        @click="emit('update:page', page + 1)"
      >&raquo;</button>
    </nav>
  </div>
</template>
