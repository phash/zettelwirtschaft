<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()

const items = [
  { path: '/scan', label: 'Scan', icon: 'camera', primary: true },
  { path: '/dokumente', label: 'Dokumente', icon: 'folder' },
  { path: '/suche', label: 'Suche', icon: 'search' },
  { path: '/', label: 'Mehr', icon: 'dots' },
]

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

const icons = {
  camera: 'M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z',
  folder: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z',
  search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
  dots: 'M4 6h16M4 12h16M4 18h16',
}
</script>

<template>
  <nav class="fixed bottom-0 left-0 right-0 z-40 border-t border-gray-200 bg-white lg:hidden">
    <div class="flex items-center justify-around">
      <router-link
        v-for="item in items"
        :key="item.path"
        :to="item.path"
        :class="[
          'flex flex-col items-center gap-0.5 py-2 px-3 text-[10px] font-medium transition-colors',
          item.primary ? '-mt-3' : '',
          isActive(item.path)
            ? 'text-primary-600'
            : 'text-gray-400',
        ]"
      >
        <div
          v-if="item.primary"
          :class="[
            'flex h-12 w-12 items-center justify-center rounded-full shadow-lg',
            isActive(item.path) ? 'bg-primary-600 text-white' : 'bg-primary-500 text-white',
          ]"
        >
          <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" :d="icons[item.icon]" />
          </svg>
        </div>
        <svg v-else class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" :d="icons[item.icon]" />
        </svg>
        <span>{{ item.label }}</span>
      </router-link>
    </div>
  </nav>
</template>
