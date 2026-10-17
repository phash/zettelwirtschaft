<script setup>
import { ref, onMounted } from 'vue'
import EmailAccountList from '../email/EmailAccountList.vue'
import { useFilingScopes } from '../../composables/useFilingScopes'
import { getEmailSettings } from '../../services/api'

// H-FE-5: EmailSettings holt scopes selbst aus dem Composable.
// Re-Review #2: ensureLoaded() selbst aufrufen, sonst wartet die Komponente
// implizit darauf, dass FilingScopeManager als Geschwister das laedt — fragil
// bei Mount-Order-Aenderungen oder wenn EmailSettings allein eingebettet wird.
const { scopes, ensureLoaded } = useFilingScopes()

// Feste Abruf-Regeln des Backends (Zeitzone fuer CRON, nur ungelesene Mails,
// Limit pro Abruf) — fuer die Hinweise in Liste und Formular. null = unbekannt
// (aelteres Backend), die Hinweise fallen dann knapper aus.
const emailInfo = ref(null)

onMounted(async () => {
  ensureLoaded()
  try {
    emailInfo.value = await getEmailSettings()
  } catch {
    emailInfo.value = null
  }
})
</script>

<template>
  <EmailAccountList :scopes="scopes" :info="emailInfo" />
</template>
