<script setup>
// Änderungsvorschlag einer Neu-Analyse: je Feld "Bisher" und "Neu" mit
// Häkchen. Der Aufrufer führt apply/discard aus (Backend: /api/reanalysis).
import { computed, ref, watch } from 'vue'
import { typeLabels } from '../../constants/documentTypes'
import { formatAmount, formatDate } from '../../utils/formatters'

const props = defineProps({
  proposal: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  // Gesetzt: Übernehmen/Verwerfen gesperrt, der Text nennt den Grund
  // (z.B. ungespeicherte Eingaben im Formular der Detailseite).
  blockedReason: { type: String, default: null },
})

// apply: fields = string[] (Auswahl) oder null (alles übernehmen)
const emit = defineEmits(['apply', 'discard'])

const EMPTY = '—'

const changes = computed(() => props.proposal.changes ?? [])
const selected = ref(new Set())
const openPreviews = ref(new Set())

// Standard: alle Änderungen angehakt (auch bei einem neuen Vorschlag).
watch(
  () => props.proposal,
  () => {
    selected.value = new Set(changes.value.map((c) => c.field))
    openPreviews.value = new Set()
  },
  { immediate: true },
)

const hasSelection = computed(() => selected.value.size > 0)
const locked = computed(() => props.busy || !!props.blockedReason)
const discardedWorse = computed(() => props.proposal.ocr_note === 'discarded_worse')

function toggle(field, checked) {
  const next = new Set(selected.value)
  if (checked) next.add(field)
  else next.delete(field)
  selected.value = next
}

function togglePreview(field) {
  const next = new Set(openPreviews.value)
  if (next.has(field)) next.delete(field)
  else next.add(field)
  openPreviews.value = next
}

function isEmpty(value) {
  return value == null || value === '' || (Array.isArray(value) && value.length === 0)
}

function formatWarranty(w) {
  const parts = [w.product_name, w.warranty_end_date ? `bis ${formatDate(w.warranty_end_date)}` : null]
  return parts.filter(Boolean).join(' ') || 'Garantie'
}

function display(field, value) {
  if (isEmpty(value)) return EMPTY
  switch (field) {
    case 'document_type': return typeLabels[value] || String(value)
    case 'document_date': return formatDate(value)
    case 'amount': return formatAmount(value)
    case 'tax_relevant': return value ? 'Ja' : 'Nein'
    case 'filing_scope_id': return value.name || EMPTY
    case 'tags': return value.join(', ')
    case 'warranty': return formatWarranty(value)
    default: return String(value)
  }
}

function percent(confidence) {
  return confidence == null ? null : `${Math.round(confidence * 100)} %`
}

function ocrLabel(change) {
  const before = percent(change.old?.confidence)
  const after = percent(change.new?.confidence)
  const confidence = before && after ? ` (Konfidenz ${before} → ${after})` : after ? ` (Konfidenz ${after})` : ''
  return `${change.label}${confidence}`
}

function applySelection() {
  // Reihenfolge des Vorschlags beibehalten.
  emit('apply', changes.value.map((c) => c.field).filter((f) => selected.value.has(f)))
}
</script>

<template>
  <section class="card space-y-3" data-testid="proposal-diff" aria-labelledby="proposal-diff-title">
    <div>
      <h2 id="proposal-diff-title" class="text-sm font-semibold text-gray-900 uppercase tracking-wider">Änderungsvorschlag</h2>
      <p class="mt-1 text-xs text-gray-500">Neu analysiert mit {{ proposal.model || 'unbekannt' }}. Das Dokument ändert sich erst, wenn du Änderungen übernimmst.</p>
    </div>

    <p
      v-if="discardedWorse"
      class="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800"
      role="status"
      data-testid="proposal-ocr-note"
    >Neu erkannter Text war schlechter und wurde verworfen.</p>

    <p v-if="!changes.length" class="text-sm text-gray-500">Keine Änderungen.</p>

    <div v-else class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs font-medium text-gray-500">
            <th scope="col" class="py-1 pr-3">Feld</th>
            <th scope="col" class="py-1 pr-3">Bisher</th>
            <th scope="col" class="py-1 pr-3">Neu</th>
            <th scope="col" class="py-1 text-center">Übernehmen</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="change in changes" :key="change.field">
            <tr class="border-t border-gray-100 align-top" :data-testid="`proposal-row-${change.field}`">
              <template v-if="change.field === 'ocr_text'">
                <th scope="row" class="py-2 pr-3 text-left font-medium text-gray-700">{{ ocrLabel(change) }}</th>
                <td colspan="2" class="py-2 pr-3">
                  <button
                    type="button"
                    class="text-xs text-primary-600 hover:text-primary-700"
                    :aria-expanded="openPreviews.has(change.field)"
                    @click="togglePreview(change.field)"
                  >{{ openPreviews.has(change.field) ? 'Vorschau ausblenden' : 'Vorschau anzeigen' }}</button>
                </td>
              </template>
              <template v-else>
                <th scope="row" class="py-2 pr-3 text-left font-medium text-gray-700">{{ change.label }}</th>
                <td class="py-2 pr-3 text-gray-500 break-words">{{ display(change.field, change.old) }}</td>
                <td class="py-2 pr-3 text-gray-900 break-words">{{ display(change.field, change.new) }}</td>
              </template>
              <td class="py-2 text-center">
                <input
                  type="checkbox"
                  class="rounded border-gray-300"
                  :checked="selected.has(change.field)"
                  :aria-label="`${change.label} übernehmen`"
                  @change="toggle(change.field, $event.target.checked)"
                />
              </td>
            </tr>
            <tr v-if="change.field === 'ocr_text' && openPreviews.has(change.field)">
              <td colspan="4" class="pb-3">
                <div class="grid gap-2 sm:grid-cols-2">
                  <div>
                    <p class="text-xs font-medium text-gray-500 mb-1">Bisher</p>
                    <pre class="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs text-gray-600" data-testid="proposal-ocr-old">{{ change.old?.text || EMPTY }}</pre>
                  </div>
                  <div>
                    <p class="text-xs font-medium text-gray-500 mb-1">Neu</p>
                    <pre class="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs text-gray-900" data-testid="proposal-ocr-new">{{ change.new?.text || EMPTY }}</pre>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <p
      v-if="blockedReason"
      class="text-right text-xs text-amber-700"
      role="status"
      data-testid="proposal-blocked"
    >{{ blockedReason }}</p>

    <div class="flex flex-wrap justify-end gap-2 pt-1">
      <button type="button" class="btn-secondary" :disabled="locked" @click="emit('discard')">Verwerfen</button>
      <button
        v-if="changes.length"
        type="button"
        class="btn-secondary"
        :disabled="locked || !hasSelection"
        @click="applySelection"
      >Auswahl übernehmen</button>
      <button
        v-if="changes.length"
        type="button"
        class="btn-primary"
        :disabled="locked"
        @click="emit('apply', null)"
      >Alles übernehmen</button>
    </div>
  </section>
</template>
