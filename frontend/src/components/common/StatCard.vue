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

// FE-M2: Die icon-Prop wurde frueher ignoriert (es gab nur einen #icon-Slot),
// sodass TaxView/WarrantyView mit icon="document" usw. den Platzhalter (erstes
// Zeichen des Werts) zeigten. Kleine Glyph-Map als Default-Icon; ohne icon-Prop
// bleibt der erste-Zeichen-Fallback (Dashboard nutzt nur color, unveraendert).
const iconMap = {
  document: '📄',
  currency: '€',
  folder: '📁',
  check: '✓',
  warning: '⚠️',
  error: '⛔',
}
</script>

<template>
  <div class="card flex items-center gap-4">
    <div :class="['flex h-12 w-12 items-center justify-center rounded-lg', colorMap[color]]">
      <slot name="icon">
        <span class="text-xl font-bold">{{ iconMap[icon] || String(value).charAt(0) }}</span>
      </slot>
    </div>
    <div>
      <p class="text-2xl font-bold text-gray-900">{{ value }}</p>
      <p class="text-sm text-gray-500">{{ label }}</p>
    </div>
  </div>
</template>
