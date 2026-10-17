<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from './components/layout/AppLayout.vue'
import ToastContainer from './components/common/ToastContainer.vue'

const route = useRoute()
const router = useRouter()

// Erst nach der ersten aufgeloesten Navigation rendern: davor steht die Route
// auf START_LOCATION (meta leer) — die App-Shell wuerde kurz montieren und
// ihre Poller (Benachrichtigungen, Badges) noch vor dem PIN-Redirect starten.
const ready = ref(false)
const markReady = () => { ready.value = true }
router.isReady().then(markReady, markReady)
</script>

<template>
  <template v-if="ready">
    <!-- Oeffentliche Seiten (PIN-Login) ohne App-Shell: keine Navigation, keine
         Suche, keine Glocke — und damit keine Poller, die vor dem Login 401
         liefern und den Redirect verschachteln. -->
    <template v-if="route.meta.public">
      <router-view />
      <ToastContainer />
    </template>
    <AppLayout v-else />
  </template>
</template>
