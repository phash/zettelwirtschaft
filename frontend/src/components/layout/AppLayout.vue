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
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const onboarding = useOnboardingStore()
const auth = useAuthStore()

// Beim ersten App-Start die Willkommens-Tour zeigen — aber NICHT auf der
// PIN-Seite und NICHT solange der PIN-Schutz aktiv und der Nutzer noch nicht
// angemeldet ist (sonst flasht das Modal um/vor dem Login). Auf Auth-State
// gegated statt nur auf den Route-Namen, damit es nicht von Redirect-Timing
// abhaengt. Nur einmal (per localStorage gemerkt).
watch(
  () => route.name,
  (name) => {
    if (name && name !== 'pin-login' && (!auth.pinEnabled || auth.isAuthenticated)) {
      onboarding.maybeAutoStart()
    }
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
