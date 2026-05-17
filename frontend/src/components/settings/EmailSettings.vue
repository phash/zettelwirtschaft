<script setup>
import { onMounted } from 'vue'
import EmailAccountList from '../email/EmailAccountList.vue'
import { useFilingScopes } from '../../composables/useFilingScopes'

// H-FE-5: EmailSettings holt scopes selbst aus dem Composable.
// Re-Review #2: ensureLoaded() selbst aufrufen, sonst wartet die Komponente
// implizit darauf, dass FilingScopeManager als Geschwister das laedt — fragil
// bei Mount-Order-Aenderungen oder wenn EmailSettings allein eingebettet wird.
const { scopes, ensureLoaded } = useFilingScopes()
onMounted(() => ensureLoaded())
</script>

<template>
  <EmailAccountList :scopes="scopes" />
</template>
