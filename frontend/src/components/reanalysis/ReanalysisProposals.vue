<script setup>
// Offene Änderungsvorschläge aller Dokumente (Tab in "Zu prüfen"): Feldfilter,
// je Dokument aufklappbarer Diff, Sammel-Aktionen. Backend: /api/reanalysis.
import { computed, onMounted, ref } from 'vue'
import ConfirmDialog from '../common/ConfirmDialog.vue'
import Pagination from '../common/Pagination.vue'
import ProposalDiff from './ProposalDiff.vue'
import { getProposals, applyAllProposals, discardAllProposals } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { useBackgroundTask } from '../../composables/useBackgroundTask'
import { useProposalActions } from '../../composables/useProposalActions'
import { pluralize } from '../../utils/pluralize'
import { apiErrorMessage } from '../../utils/apiErrors'

// Nach jeder Änderung am Bestand (Tab-Zähler im Elternteil aktualisieren).
const emit = defineEmits(['changed'])

const notify = useNotificationStore()

const PAGE_SIZE = 25
// Beschriftungen der Filter-Optionen, identisch zu FIELD_LABELS im Backend
// (schemas/reanalysis.py). Die Labels der geladenen Vorschläge haben Vorrang;
// die Tabelle deckt Felder ab, die auf der aktuellen Seite nicht vorkommen.
const FALLBACK_LABELS = {
  document_type: 'Dokumenttyp',
  issuer: 'Aussteller',
  document_date: 'Datum',
  amount: 'Betrag',
  currency: 'Währung',
  tax_relevant: 'Steuerrelevant',
  tax_category: 'Steuerkategorie',
  tax_year: 'Steuerjahr',
  recipient: 'Empfänger',
  reference_number: 'Referenznummer',
  filing_scope_id: 'Ablagebereich',
  tags: 'Tags',
  warranty: 'Garantie',
}

const items = ref([])
const total = ref(0)
const fieldCounts = ref({})
const labels = ref({})
const field = ref('')
const page = ref(1)
const loading = ref(true)
const loadError = ref(false)
const openId = ref(null)
const confirmAction = ref(null) // 'apply' | 'discard' | null
const bulkBusy = ref(false)

const applyAll = useBackgroundTask({
  onSuccess: (result) => {
    const { applied = 0, conflicts = 0, errors = 0 } = result ?? {}
    const failed = errors > 0 ? `, ${errors} fehlgeschlagen` : ''
    notify.success(`${applied} übernommen, ${conflicts} mit Konflikt${failed}`)
    refresh()
  },
  onError: (message) => {
    notify.error(message)
    refresh()
  },
})

// Auch nach einem Fehler neu laden (z. B. 409: Vorschlag veraltet).
const proposalActions = useProposalActions({ onSettled: () => refresh() })

const anyBusy = computed(() => bulkBusy.value || applyAll.running.value || proposalActions.busy.value)

const fieldOptions = computed(() =>
  Object.entries(fieldCounts.value).map(([name, count]) => ({
    name,
    text: `${labels.value[name] ?? FALLBACK_LABELS[name] ?? name} (${count})`,
  })),
)

const fieldLabel = computed(() => labels.value[field.value] ?? FALLBACK_LABELS[field.value] ?? field.value)

const confirmMessage = computed(() => {
  const count = pluralize(total.value, 'Vorschlag', 'Vorschläge')
  const scope = field.value ? ` (Dokumente mit Änderung an „${fieldLabel.value}“)` : ''
  return confirmAction.value === 'apply'
    ? `${count}${scope}: Alle Änderungen dieser Vorschläge werden in die Dokumente übernommen, nicht nur die des gewählten Feldes.`
    : `${count}${scope} werden verworfen. Die Dokumente bleiben unverändert.`
})

async function load() {
  try {
    const data = await getProposals({ field: field.value || undefined, page: page.value, page_size: PAGE_SIZE })
    // Letzte Seite wurde durch Übernehmen/Verwerfen leer: eine Seite zurück.
    if (!data.items.length && data.total > 0 && page.value > 1) {
      page.value = Math.ceil(data.total / PAGE_SIZE)
      return load()
    }
    for (const item of data.items) {
      for (const change of item.changes) labels.value[change.field] = change.label
    }
    items.value = data.items
    total.value = data.total
    fieldCounts.value = data.field_counts ?? {}
    // Gewählter Filter ohne Vorschläge (alle erledigt): zurück zu "Alle Felder".
    if (field.value && !fieldCounts.value[field.value]) {
      field.value = ''
      page.value = 1
      return load()
    }
    if (openId.value && !items.value.some((i) => i.document_id === openId.value)) openId.value = null
    loadError.value = false
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function refresh() {
  await load()
  emit('changed')
}

function onFilterChange() {
  page.value = 1
  openId.value = null
  loading.value = true
  load()
}

function onPage(next) {
  page.value = next
  openId.value = null
  load()
}

function toggleOpen(id) {
  openId.value = openId.value === id ? null : id
}

async function confirmBulk() {
  const action = confirmAction.value
  confirmAction.value = null
  const selected = field.value || null
  if (action === 'apply') {
    await applyAll.start(() => applyAllProposals(selected))
    return
  }
  bulkBusy.value = true
  try {
    const { discarded } = await discardAllProposals(selected)
    notify.success(`${pluralize(discarded, 'Vorschlag', 'Vorschläge')} verworfen.`)
  } catch (e) {
    notify.error(apiErrorMessage(e, 'Vorschläge konnten nicht verworfen werden.'))
  } finally {
    bulkBusy.value = false
    await refresh()
  }
}

onMounted(() => {
  load()
  // Läuft noch eine Sammel-Übernahme (z. B. nach Seitenwechsel), weiter verfolgen.
  applyAll.resume({ kind: 'reanalysis_apply_all', key: 'global' })
})
</script>

<template>
  <div class="space-y-4" data-testid="reanalysis-proposals">
    <div v-if="loading" class="flex items-center justify-center py-20">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-primary-200 border-t-primary-600"></div>
    </div>

    <div v-else-if="loadError" class="card text-center py-12" role="alert">
      <p class="text-gray-600">Änderungsvorschläge konnten nicht geladen werden.</p>
      <button type="button" class="btn-primary mt-4" @click="onFilterChange">Erneut versuchen</button>
    </div>

    <div v-else-if="total === 0 && !field && !applyAll.running.value" class="card text-center py-16">
      <p class="text-lg font-medium text-gray-600">Keine offenen Änderungsvorschläge.</p>
      <p class="mt-1 text-sm text-gray-400">Vorschläge entstehen, wenn du ein Dokument neu analysieren lässt.</p>
    </div>

    <template v-else>
      <div class="card flex flex-wrap items-end justify-between gap-3">
        <div>
          <label for="proposal-field" class="block text-xs font-medium text-gray-500 mb-1">Feld</label>
          <select id="proposal-field" v-model="field" class="input" :disabled="anyBusy" @change="onFilterChange">
            <option value="">Alle Felder</option>
            <option v-for="option in fieldOptions" :key="option.name" :value="option.name">{{ option.text }}</option>
          </select>
        </div>
        <div class="flex flex-wrap gap-2">
          <button type="button" class="btn-secondary" :disabled="anyBusy || total === 0" @click="confirmAction = 'discard'">
            Alle verwerfen ({{ total }})
          </button>
          <button type="button" class="btn-primary" :disabled="anyBusy || total === 0" @click="confirmAction = 'apply'">
            Alle übernehmen ({{ total }})
          </button>
        </div>
      </div>

      <p v-if="applyAll.running.value" class="text-sm text-gray-600" role="status">
        Änderungen werden übernommen …<template v-if="applyAll.progressText.value"> {{ applyAll.progressText.value }}</template>
      </p>

      <ul class="space-y-3">
        <li
          v-for="item in items"
          :key="item.document_id"
          class="card space-y-3"
          :data-testid="`proposal-entry-${item.document_id}`"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="min-w-0">
              <router-link :to="`/dokumente/${item.document_id}`" class="font-medium text-primary-700 hover:underline break-words">{{ item.document_title }}</router-link>
              <p class="text-xs text-gray-500">{{ pluralize(item.changes.length, 'Änderung', 'Änderungen') }}</p>
            </div>
            <button
              type="button"
              class="btn-secondary"
              :aria-expanded="openId === item.document_id"
              @click="toggleOpen(item.document_id)"
            >{{ openId === item.document_id ? 'Änderungen ausblenden' : 'Änderungen anzeigen' }}</button>
          </div>
          <ProposalDiff
            v-if="openId === item.document_id"
            :proposal="item"
            :busy="anyBusy"
            @apply="(fields) => proposalActions.apply(item.document_id, fields)"
            @discard="proposalActions.discard(item.document_id)"
          />
        </li>
      </ul>

      <Pagination :total="total" :page="page" :page-size="PAGE_SIZE" @update:page="onPage" />
    </template>

    <ConfirmDialog
      :show="confirmAction !== null"
      :title="confirmAction === 'apply' ? 'Alle Vorschläge übernehmen?' : 'Alle Vorschläge verwerfen?'"
      :message="confirmMessage"
      :confirm-text="confirmAction === 'apply' ? 'Alle übernehmen' : 'Alle verwerfen'"
      :danger="confirmAction === 'discard'"
      @confirm="confirmBulk"
      @cancel="confirmAction = null"
    />
  </div>
</template>
