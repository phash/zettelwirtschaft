<script setup>
import { computed } from 'vue'
import { documentTypeMeta, DEFAULT_TYPE_COLOR } from '../../constants/documentTypes'

const props = defineProps({
  type: String,
})

const meta = computed(() => documentTypeMeta[props.type])
</script>

<template>
  <span :class="['badge', meta?.color || DEFAULT_TYPE_COLOR]">
    {{ meta?.label || type }}
  </span>
</template>
