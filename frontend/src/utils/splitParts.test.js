import { describe, it, expect } from 'vitest'
import { computeParts, confirmBody, formatPageRanges, partLabel, sourcePagesLabel } from './splitParts'

describe('formatPageRanges', () => {
  it('fasst zusammenhängende Seiten zusammen', () => {
    expect(formatPageRanges([3, 4, 5, 7, 9, 10])).toBe('3–5, 7, 9–10')
    expect(formatPageRanges([2])).toBe('2')
    expect(formatPageRanges([])).toBe('')
  })
})

describe('computeParts', () => {
  it('ohne Schnitt ist alles ein Teil', () => {
    const { parts, error } = computeParts(3, new Set(), new Set())
    expect(parts).toEqual([{ index: 1, pages: [1, 2, 3], range: [1, 2, 3] }])
    expect(error).toBeNull()
  })

  it('Schnitte und weggelassene Seiten', () => {
    const { parts, segmentOf } = computeParts(5, new Set([2]), new Set([3]))
    expect(parts.map((p) => p.pages)).toEqual([[1, 2], [4, 5]])
    expect(segmentOf[3]).toBe(2)
    expect(partLabel(parts[1])).toBe('Teil 2 · Seiten 4–5')
  })

  it('ein ganz weggelassener Abschnitt ergibt keinen Teil', () => {
    const { parts, segmentOf } = computeParts(4, new Set([1, 2]), new Set([2]))
    expect(parts.map((p) => p.pages)).toEqual([[1], [3, 4]])
    expect(segmentOf[2]).toBeNull()
    expect(partLabel(parts[0])).toBe('Teil 1 · Seite 1')
  })

  it('meldet Teile nur aus Leerseiten und leere Auswahl', () => {
    expect(computeParts(3, new Set([1]), new Set(), new Set([2, 3])).error)
      .toBe('Teil 2 besteht nur aus Leerseiten.')
    expect(computeParts(2, new Set(), new Set([1, 2])).error)
      .toBe('Mindestens eine Seite muss übernommen werden.')
  })
})

describe('sourcePagesLabel', () => {
  it('formatiert die Herkunft eines Teils, Einzahl bei einer Seite', () => {
    expect(sourcePagesLabel('3-44')).toBe('Seiten 3–44')
    expect(sourcePagesLabel('3-11,13-44')).toBe('Seiten 3–11, 13–44')
    expect(sourcePagesLabel('5')).toBe('Seite 5')
    expect(sourcePagesLabel(null)).toBe('')
  })
})

describe('confirmBody', () => {
  it('baut den Request-Body', () => {
    const { parts } = computeParts(5, new Set([2]), new Set([3]))
    expect(confirmBody(4, parts, new Set([3]))).toEqual({
      version: 4,
      parts: [{ pages: [1, 2] }, { pages: [4, 5] }],
      excluded: [3],
    })
  })
})
