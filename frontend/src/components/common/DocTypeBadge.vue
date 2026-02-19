<script setup>
defineProps({
  type: String,
})

const typeLabels = {
  RECHNUNG: 'Rechnung',
  QUITTUNG: 'Quittung',
  KAUFVERTRAG: 'Kaufvertrag',
  GARANTIESCHEIN: 'Garantieschein',
  VERSICHERUNGSPOLICE: 'Versicherung',
  KONTOAUSZUG: 'Kontoauszug',
  LOHNABRECHNUNG: 'Lohnabrechnung',
  STEUERBESCHEID: 'Steuerbescheid',
  MIETVERTRAG: 'Mietvertrag',
  HANDWERKER_RECHNUNG: 'Handwerker',
  ARZTRECHNUNG: 'Arztrechnung',
  REZEPT: 'Rezept',
  AMTLICHES_SCHREIBEN: 'Amtlich',
  BEDIENUNGSANLEITUNG: 'Anleitung',
  SONSTIGES: 'Sonstiges',
}

const typeColors = {
  RECHNUNG: 'bg-blue-100 text-blue-700',
  QUITTUNG: 'bg-green-100 text-green-700',
  KAUFVERTRAG: 'bg-purple-100 text-purple-700',
  GARANTIESCHEIN: 'bg-yellow-100 text-yellow-700',
  VERSICHERUNGSPOLICE: 'bg-indigo-100 text-indigo-700',
  KONTOAUSZUG: 'bg-cyan-100 text-cyan-700',
  LOHNABRECHNUNG: 'bg-emerald-100 text-emerald-700',
  STEUERBESCHEID: 'bg-orange-100 text-orange-700',
  MIETVERTRAG: 'bg-rose-100 text-rose-700',
  HANDWERKER_RECHNUNG: 'bg-amber-100 text-amber-700',
  ARZTRECHNUNG: 'bg-red-100 text-red-700',
  REZEPT: 'bg-pink-100 text-pink-700',
  AMTLICHES_SCHREIBEN: 'bg-slate-100 text-slate-700',
  BEDIENUNGSANLEITUNG: 'bg-teal-100 text-teal-700',
  SONSTIGES: 'bg-gray-100 text-gray-700',
}
</script>

<template>
  <span :class="['badge', typeColors[type] || 'bg-gray-100 text-gray-700']">
    {{ typeLabels[type] || type }}
  </span>
</template>
