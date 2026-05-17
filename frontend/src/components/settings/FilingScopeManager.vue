<script setup>
import { ref, onMounted } from 'vue'
import { createFilingScope, updateFilingScope, deleteFilingScope } from '../../services/api'
import { useNotificationStore } from '../../stores/notifications'
import { useFilingScopes } from '../../composables/useFilingScopes'
import ConfirmDialog from '../common/ConfirmDialog.vue'

const notify = useNotificationStore()

// H-FE-5: scopes aus dem shared Composable. CRUD-Operationen invalidieren
// den Cache, sodass alle anderen Views (Documents, Search, Tax, Chat, Detail)
// die Aenderung beim naechsten Mount oder direkt ueber Reactivity sehen.
const { scopes, ensureLoaded, invalidate } = useFilingScopes()
const scopeForm = ref({ name: '', description: '', keywords: '', is_default: false, color: '#3B82F6' })
const editingScopeId = ref(null)
const showScopeForm = ref(false)
const deletingScopeId = ref(null)

async function loadScopes() {
  await ensureLoaded(true)
}

function startNewScope() {
  editingScopeId.value = null
  scopeForm.value = { name: '', description: '', keywords: '', is_default: false, color: '#3B82F6' }
  showScopeForm.value = true
}

function startEditScope(scope) {
  editingScopeId.value = scope.id
  scopeForm.value = {
    name: scope.name,
    description: scope.description || '',
    keywords: (scope.keywords || []).join(', '),
    is_default: scope.is_default,
    color: scope.color || '#3B82F6',
  }
  showScopeForm.value = true
}

async function saveScope() {
  const keywords = scopeForm.value.keywords
    .split(',')
    .map(k => k.trim())
    .filter(k => k)
  const payload = {
    name: scopeForm.value.name,
    description: scopeForm.value.description || null,
    keywords,
    is_default: scopeForm.value.is_default,
    color: scopeForm.value.color,
  }
  try {
    if (editingScopeId.value) {
      await updateFilingScope(editingScopeId.value, payload)
      notify.success('Ablagebereich aktualisiert.')
    } else {
      await createFilingScope(payload)
      notify.success('Ablagebereich erstellt.')
    }
    showScopeForm.value = false
    invalidate()
    await ensureLoaded(true)
  } catch (e) {
    notify.error(e.response?.data?.detail || 'Fehler beim Speichern.')
  }
}

async function confirmDeleteScope() {
  try {
    await deleteFilingScope(deletingScopeId.value)
    notify.success('Ablagebereich gelöscht.')
    deletingScopeId.value = null
    invalidate()
    await ensureLoaded(true)
  } catch (e) {
    notify.error(e.response?.data?.detail || 'Löschen fehlgeschlagen.')
    deletingScopeId.value = null
  }
}

// H-FE-4: Kind-Komponente laedt selbst, kein defineExpose-Pattern mehr.
onMounted(async () => {
  await ensureLoaded()
})
</script>

<template>
  <div class="card">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-semibold text-gray-900">Ablagebereiche</h2>
      <button @click="startNewScope" class="btn-primary text-sm">+ Neuer Bereich</button>
    </div>

    <!-- Scope-Liste -->
    <div class="space-y-3">
      <div v-for="scope in scopes" :key="scope.id" class="flex items-center justify-between rounded-lg border border-gray-200 p-3">
        <div class="flex items-center gap-3">
          <span class="h-3 w-3 rounded-full flex-shrink-0" :style="{ backgroundColor: scope.color || '#6B7280' }"></span>
          <div>
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-gray-900">{{ scope.name }}</span>
              <span v-if="scope.is_default" class="badge bg-primary-100 text-primary-700 text-xs">Standard</span>
            </div>
            <p v-if="scope.description" class="text-xs text-gray-500">{{ scope.description }}</p>
            <div v-if="scope.keywords?.length" class="mt-1 flex flex-wrap gap-1">
              <span v-for="kw in scope.keywords" :key="kw" class="badge bg-gray-100 text-gray-600 text-xs">{{ kw }}</span>
            </div>
          </div>
        </div>
        <div class="flex gap-2">
          <button @click="startEditScope(scope)" class="text-sm text-primary-600 hover:text-primary-700">Bearbeiten</button>
          <button v-if="!scope.is_default" @click="deletingScopeId = scope.id" class="text-sm text-red-500 hover:text-red-700">Löschen</button>
        </div>
      </div>
      <div v-if="scopes.length === 0" class="text-sm text-gray-400">Keine Ablagebereiche konfiguriert.</div>
    </div>

    <!-- Inline-Formular -->
    <div v-if="showScopeForm" class="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-3">
      <h3 class="text-sm font-semibold text-gray-900">
        {{ editingScopeId ? 'Ablagebereich bearbeiten' : 'Neuer Ablagebereich' }}
      </h3>
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Name</label>
        <input v-model="scopeForm.name" class="input" placeholder="z.B. Praxis Dr. Müller" />
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Beschreibung</label>
        <input v-model="scopeForm.description" class="input" placeholder="Optional" />
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-500 mb-1">Schlüsselwörter (kommagetrennt)</label>
        <input v-model="scopeForm.keywords" class="input" placeholder="z.B. KBV, Kassenärztliche, Praxis" />
      </div>
      <div class="flex items-center gap-4">
        <div class="flex items-center gap-2">
          <label class="block text-xs font-medium text-gray-500">Farbe</label>
          <input v-model="scopeForm.color" type="color" class="h-8 w-8 rounded border border-gray-300 cursor-pointer" />
        </div>
        <label class="flex items-center gap-2">
          <input type="checkbox" v-model="scopeForm.is_default" class="rounded border-gray-300" />
          <span class="text-sm text-gray-600">Standard-Bereich</span>
        </label>
      </div>
      <div class="flex gap-2">
        <button @click="saveScope" class="btn-primary text-sm">Speichern</button>
        <button @click="showScopeForm = false" class="btn-secondary text-sm">Abbrechen</button>
      </div>
    </div>
  </div>

  <ConfirmDialog
    :show="!!deletingScopeId"
    title="Ablagebereich löschen"
    message="Soll dieser Ablagebereich wirklich gelöscht werden? Zugeordnete Dokumente werden dem Standard-Bereich zugewiesen."
    confirm-text="Löschen"
    :danger="true"
    @confirm="confirmDeleteScope"
    @cancel="deletingScopeId = null"
  />
</template>
