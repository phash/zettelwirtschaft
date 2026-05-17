<script setup>
import EmailAccountList from '../email/EmailAccountList.vue'
import { useFilingScopes } from '../../composables/useFilingScopes'

// H-FE-5: EmailSettings holt scopes selbst aus dem Composable statt sie
// als Prop von SettingsView durchgereicht zu bekommen (Race bei Mount-Order).
const { scopes } = useFilingScopes()
</script>

<template>
  <EmailAccountList :scopes="scopes" />
</template>
