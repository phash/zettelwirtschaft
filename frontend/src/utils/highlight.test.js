import { describe, it, expect } from 'vitest'
import { highlightSegments, HIGHLIGHT_START as S, HIGHLIGHT_END as E } from './highlight'

describe('highlightSegments', () => {
  it('zerlegt Treffer-Markierungen in Segmente', () => {
    expect(highlightSegments(`Monatliche ${S}Strom${E}rechnung`)).toEqual([
      { text: 'Monatliche ', hit: false },
      { text: 'Strom', hit: true },
      { text: 'rechnung', hit: false },
    ])
  })

  it('behandelt mehrere Treffer und Treffer am Rand', () => {
    expect(highlightSegments(`${S}a${E} b ${S}c${E}`)).toEqual([
      { text: 'a', hit: true },
      { text: ' b ', hit: false },
      { text: 'c', hit: true },
    ])
  })

  it('laesst HTML im Text unangetastet (kein Markup)', () => {
    expect(highlightSegments('<mark>x</mark> & <b>')).toEqual([
      { text: '<mark>x</mark> & <b>', hit: false },
    ])
  })

  it('liefert fuer leere Werte eine leere Liste', () => {
    expect(highlightSegments('')).toEqual([])
    expect(highlightSegments(null)).toEqual([])
  })
})
