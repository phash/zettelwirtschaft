<script setup>
defineProps({
  label: String,
  value: [Number, String],
  color: { type: String, default: 'blue' },
  icon: { type: String, default: '' },
})

const colorMap = {
  blue: 'bg-blue-50 text-blue-700',
  green: 'bg-green-50 text-green-700',
  orange: 'bg-orange-50 text-orange-700',
  red: 'bg-red-50 text-red-700',
  purple: 'bg-purple-50 text-purple-700',
  indigo: 'bg-indigo-50 text-indigo-700',
}

// FE-M2: Kleine Glyph-Map fuer die icon-Prop (TaxView/WarrantyView/Dashboard).
// Kein Fallback auf das erste Zeichen des Werts mehr — das ergab "4 | 4" auf
// dem Dashboard. Ohne icon-Prop und ohne #icon-Slot entfaellt die Icon-Box.
const iconMap = {
  document: '📄',
  currency: '€',
  folder: '📁',
  check: '✓',
  warning: '⚠️',
  error: '⛔',
  calendar: '📅',
  question: '❓',
  shield: '🛡️',
  mail: '✉️',
}
</script>

<template>
  <div class="card flex items-center gap-4">
    <div
      v-if="$slots.icon || iconMap[icon]"
      :class="['flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg', colorMap[color]]"
      aria-hidden="true"
    >
      <slot name="icon">
        <span class="text-xl font-bold">{{ iconMap[icon] }}</span>
      </slot>
    </div>
    <div>
      <p class="text-2xl font-bold text-gray-900">{{ value }}</p>
      <p class="text-sm text-gray-500">{{ label }}</p>
    </div>
  </div>
</template>
