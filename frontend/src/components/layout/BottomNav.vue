<script setup>
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { BOTTOM_NAV_ITEMS, MORE_NAV_ITEMS, NAV_ICONS, isNavActive } from '../../constants/navigation'
import { useReviewCount } from '../../composables/useReviewCount'
import { trapTabKey } from '../../utils/focusTrap'

const route = useRoute()
const reviewCount = useReviewCount()

// "Mehr"-Menue (Spec 09): alle Seiten, die nicht in die 4 BottomNav-Plaetze
// passen — ohne es waeren Dashboard, Prüfen, Steuer usw. auf dem Handy nur
// per URL erreichbar.
const moreOpen = ref(false)
const moreButton = ref(null)
const sheet = ref(null)

const moreActive = computed(() => MORE_NAV_ITEMS.some((i) => isNavActive(route.path, i.path)))

async function openMore() {
  moreOpen.value = true
  reviewCount.refresh(true)
  document.addEventListener('keydown', onKeydown)
  await nextTick()
  sheet.value?.querySelector('a')?.focus()
}

function closeMore({ restoreFocus = true } = {}) {
  if (!moreOpen.value) return
  moreOpen.value = false
  document.removeEventListener('keydown', onKeydown)
  if (restoreFocus) moreButton.value?.focus()
}

function toggleMore() {
  if (moreOpen.value) closeMore()
  else openMore()
}

function onKeydown(e) {
  if (e.key === 'Escape') closeMore()
  else trapTabKey(e, sheet.value) // aria-modal: Tab bleibt im Menue
}

// Nach einer Navigation (Klick auf einen Eintrag) Menue schliessen.
watch(() => route.fullPath, () => closeMore({ restoreFocus: false }))

onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <nav class="fixed bottom-0 left-0 right-0 z-40 border-t border-gray-200 bg-white lg:hidden" aria-label="Navigation">
    <div class="flex items-center justify-around">
      <router-link
        v-for="item in BOTTOM_NAV_ITEMS"
        :key="item.path"
        :to="item.path"
        :class="[
          'flex flex-col items-center gap-0.5 py-2 px-3 text-[10px] font-medium transition-colors',
          item.primary ? '-mt-3' : '',
          isNavActive(route.path, item.path)
            ? 'text-primary-600'
            : 'text-gray-400',
        ]"
      >
        <div
          v-if="item.primary"
          :class="[
            'flex h-12 w-12 items-center justify-center rounded-full shadow-lg',
            isNavActive(route.path, item.path) ? 'bg-primary-600 text-white' : 'bg-primary-500 text-white',
          ]"
        >
          <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" :d="NAV_ICONS[item.icon]" />
          </svg>
        </div>
        <svg v-else class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" :d="NAV_ICONS[item.icon]" />
        </svg>
        <span>{{ item.shortLabel || item.label }}</span>
      </router-link>

      <button
        ref="moreButton"
        type="button"
        :class="[
          'relative flex flex-col items-center gap-0.5 py-2 px-3 text-[10px] font-medium transition-colors',
          moreOpen || moreActive ? 'text-primary-600' : 'text-gray-400',
        ]"
        aria-haspopup="dialog"
        :aria-expanded="moreOpen"
        aria-controls="bottom-nav-more"
        @click="toggleMore"
      >
        <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" :d="NAV_ICONS.menu" />
        </svg>
        <span
          v-if="reviewCount.count.value > 0"
          class="absolute right-2 top-1 h-2 w-2 rounded-full bg-amber-500"
          aria-hidden="true"
        ></span>
        <span>Mehr</span>
      </button>
    </div>
  </nav>

  <div v-if="moreOpen" class="fixed inset-0 z-30 lg:hidden">
    <div class="absolute inset-0 bg-black/30" aria-hidden="true" @click="closeMore()"></div>
    <div
      id="bottom-nav-more"
      ref="sheet"
      role="dialog"
      aria-modal="true"
      aria-label="Weitere Seiten"
      class="absolute bottom-14 left-0 right-0 rounded-t-xl border-t border-gray-200 bg-white px-2 pb-2 pt-3 shadow-xl"
    >
      <p class="px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Weitere Seiten</p>
      <div class="grid grid-cols-2 gap-1 sm:grid-cols-3">
        <router-link
          v-for="item in MORE_NAV_ITEMS"
          :key="item.path"
          :to="item.path"
          :class="[
            'flex items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium',
            isNavActive(route.path, item.path) ? 'bg-primary-50 text-primary-700' : 'text-gray-700 hover:bg-gray-50',
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
      </div>
    </div>
  </div>
</template>
