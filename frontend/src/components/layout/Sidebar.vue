<script setup>
import { watch } from 'vue'
import { useRoute } from 'vue-router'
import { NAV_ITEMS, NAV_ICONS, isNavActive } from '../../constants/navigation'
import { useReviewCount } from '../../composables/useReviewCount'

const route = useRoute()
const appVersion = __APP_VERSION__
const reviewCount = useReviewCount()

// Badge bei Navigation aktualisieren (gedrosselt im Composable).
watch(() => route.path, () => reviewCount.refresh(), { immediate: true })
</script>

<template>
  <aside class="hidden w-60 flex-shrink-0 border-r border-gray-200 bg-white lg:flex lg:flex-col">
    <router-link to="/" class="flex h-16 items-center gap-2 border-b border-gray-200 px-6" aria-label="Zettelwirtschaft – zum Dashboard">
      <div class="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-600 text-white font-bold text-sm">
        Z
      </div>
      <span class="text-lg font-semibold text-gray-900">Zettelwirtschaft</span>
    </router-link>
    <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto" aria-label="Hauptnavigation">
      <router-link
        v-for="item in NAV_ITEMS"
        :key="item.path"
        :to="item.path"
        :class="[
          'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
          isNavActive(route.path, item.path)
            ? 'bg-primary-50 text-primary-700'
            : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
        ]"
      >
        <svg class="h-5 w-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" :d="NAV_ICONS[item.icon]" />
        </svg>
        <span class="flex-1">{{ item.label }}</span>
        <span
          v-if="item.badge === 'review' && reviewCount.count.value > 0"
          class="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800"
          data-testid="review-badge"
        >{{ reviewCount.count.value > 99 ? '99+' : reviewCount.count.value }}<span class="sr-only"> offen</span></span>
      </router-link>
    </nav>
    <div class="border-t border-gray-200 px-6 py-3">
      <p class="text-xs text-gray-400">v{{ appVersion }}</p>
    </div>
  </aside>
</template>
