// "Aufteilung rückgängig" (Spezifikation 12): gemeinsam fuer Teildokument-
// Detail und Aufteilungs-Editor. Das Backend prueft die Bearbeitungssperre und
// antwortet sonst mit 409 + Begruendung.
import { getSplitBatch, undoSplit } from '../services/api'

export const UNDO_TITLE = 'Aufteilung rückgängig'

export function undoMessage(origin) {
  if (origin === 'MANUAL') {
    return 'Alle Teile werden als gelöscht markiert, das Originaldokument ist danach wieder aktiv.'
  }
  if (origin === 'AUTO') {
    return 'Alle Teile werden als gelöscht markiert. Der Stapel kommt mit der letzten Aufteilung '
      + 'als Vorschlag zurück in den Editor.'
  }
  return 'Alle Teile dieses Stapels werden als gelöscht markiert. Wurde ein archiviertes Dokument '
    + 'aufgeteilt, ist es danach wieder aktiv; sonst kommt der Stapel zurück in den Editor.'
}

// Wohin nach dem Rueckgaengigmachen: Editor (Stapel wieder Vorschlag),
// wiederhergestelltes Original oder die Dokumentliste.
export function undoTarget(result, batch) {
  if (result.status === 'PROPOSED') {
    return { path: `/aufteilen/${result.batch_id}`, message: 'Die Aufteilung wurde rückgängig gemacht – bitte neu aufteilen.' }
  }
  const documentId = result.restored_document_id || batch?.source_document_id
  if (documentId) {
    return { path: `/dokumente/${documentId}`, message: 'Die Aufteilung wurde rückgängig gemacht.' }
  }
  return { path: '/dokumente', message: 'Die Aufteilung wurde rückgängig gemacht.' }
}

// Laedt den Stapel (aktuelle Version) und macht die Aufteilung rueckgaengig.
export async function undoSplitBatch(batchId, batch = null) {
  const current = batch || (await getSplitBatch(batchId))
  const result = await undoSplit(batchId, current.version)
  return undoTarget(result, current)
}
