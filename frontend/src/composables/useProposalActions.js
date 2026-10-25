// Übernehmen/Verwerfen eines Änderungsvorschlags (Issue #39) — gemeinsam für
// Detailansicht, "Zu prüfen" und den Tab "Änderungsvorschläge": Sperre gegen
// doppelte Aufrufe, Erfolgs-/Fehlermeldung, danach immer onSettled. Nach
// einem Fehler (z.B. 409: Vorschlag inzwischen ersetzt) muss der Aufrufer den
// Vorschlagsstand neu laden, statt ihn auszublenden.
import { computed, ref } from 'vue'
import { applyProposal, discardProposal } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { apiErrorMessage } from '../utils/apiErrors'

// onSettled({ documentId, kind: 'apply' | 'discard', succeeded })
// blocked(): true sperrt beide Aktionen (z.B. ungespeicherte Eingaben).
export function useProposalActions({ onSettled, blocked = () => false }) {
  const notify = useNotificationStore()
  const busyId = ref(null)
  const busy = computed(() => busyId.value !== null)

  async function run(documentId, kind, action, successText, errorText) {
    if (busy.value || blocked()) return false
    busyId.value = documentId
    let succeeded = false
    try {
      await action()
      succeeded = true
      notify.success(successText)
    } catch (e) {
      notify.error(apiErrorMessage(e, errorText))
    } finally {
      busyId.value = null
    }
    await onSettled({ documentId, kind, succeeded })
    return succeeded
  }

  // fields: string[] (Auswahl) oder null (alles übernehmen)
  function apply(documentId, fields) {
    return run(
      documentId, 'apply', () => applyProposal(documentId, fields),
      'Änderungen übernommen.', 'Änderungen konnten nicht übernommen werden.',
    )
  }

  function discard(documentId) {
    return run(
      documentId, 'discard', () => discardProposal(documentId),
      'Vorschlag verworfen.', 'Vorschlag konnte nicht verworfen werden.',
    )
  }

  return { busy, busyId, apply, discard }
}
