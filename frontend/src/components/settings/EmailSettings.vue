<script setup>
import { ref } from 'vue'
import EmailAccountList from '../email/EmailAccountList.vue'

const props = defineProps({
  scopes: { type: Array, default: () => [] },
})

const emailList = ref(null)

function loadAccounts() {
  emailList.value?.loadAccounts()
}

defineExpose({ loadAccounts })
</script>

<template>
  <EmailAccountList :scopes="scopes" ref="emailList" />
</template>
