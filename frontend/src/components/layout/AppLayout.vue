<script setup>
import { watch } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from './Sidebar.vue'
import AppHeader from './AppHeader.vue'
import BottomNav from './BottomNav.vue'
import ToastContainer from '../common/ToastContainer.vue'
import PinWarningBanner from '../common/PinWarningBanner.vue'
import OnboardingModal from '../onboarding/OnboardingModal.vue'
import { useOnboardingStore } from '../../stores/onboarding'

const route = useRoute()
const onboarding = useOnboardingStore()

// Beim ersten App-Start (nach evtl. PIN-Login) die Willkommens-Tour zeigen.
// Nicht auf der PIN-Seite, und nur einmal (per localStorage gemerkt).
watch(
  () => route.name,
  (name) => {
    if (name && name !== 'pin-login') onboarding.maybeAutoStart()
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex h-screen overflow-hidden">
    <Sidebar />
    <div class="flex flex-1 flex-col overflow-hidden">
      <AppHeader />
      <PinWarningBanner />
      <main class="flex-1 overflow-y-auto bg-gray-50 p-6 pb-20 lg:pb-6">
        <router-view />
      </main>
    </div>
    <BottomNav />
    <ToastContainer />
    <OnboardingModal />
  </div>
</template>
