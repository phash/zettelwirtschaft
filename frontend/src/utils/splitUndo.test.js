import { describe, it, expect } from 'vitest'
import { undoMessage, undoTarget } from './splitUndo'

describe('undoTarget', () => {
  it('Stapel wieder als Vorschlag: zurück in den Editor', () => {
    expect(undoTarget({ batch_id: 'b1', status: 'PROPOSED', restored_document_id: null }, null).path)
      .toBe('/aufteilen/b1')
  })

  it('wiederhergestelltes Original öffnen', () => {
    const target = undoTarget({ batch_id: 'b1', status: 'UNDONE', restored_document_id: 'd9' }, null)
    expect(target.path).toBe('/dokumente/d9')
    expect(target.message).toBe('Die Aufteilung wurde rückgängig gemacht.')
  })

  it('Original war noch aktiv: Dokument aus dem Stapel, sonst Liste', () => {
    const result = { batch_id: 'b1', status: 'UNDONE', restored_document_id: null }
    expect(undoTarget(result, { source_document_id: 'd2' }).path).toBe('/dokumente/d2')
    expect(undoTarget(result, null).path).toBe('/dokumente')
  })
})

describe('undoMessage', () => {
  it('nennt je nach Herkunft die Folge', () => {
    expect(undoMessage('MANUAL')).toContain('Originaldokument ist danach wieder aktiv')
    expect(undoMessage('AUTO')).toContain('zurück in den Editor')
    expect(undoMessage(undefined)).toContain('als gelöscht markiert')
  })
})
