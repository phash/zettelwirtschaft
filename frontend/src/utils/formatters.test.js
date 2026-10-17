import { describe, it, expect } from 'vitest'
import { formatAmount, formatBytes, formatDate, formatDateTime, parseDate } from './formatters'

// TZ ist in vite.config.js (test.env) auf Europe/Berlin fixiert.

describe('formatAmount', () => {
  it('formatiert ISO-Waehrungen', () => {
    expect(formatAmount(1234.5, 'EUR')).toBe('1.234,50 €')
    expect(formatAmount('99.9', 'USD')).toBe('99,90 $')
  })

  it('wirft bei Nicht-ISO-Waehrung (LLM-Wert) keinen RangeError', () => {
    expect(() => formatAmount(10, '€')).not.toThrow()
    expect(formatAmount(10, '€')).toBe('10,00 €')
    expect(formatAmount(1234.5, 'Euro')).toBe('1.234,50 Euro')
  })

  it('faellt ohne Waehrung auf EUR zurueck und behandelt leere Werte', () => {
    expect(formatAmount(5, null)).toBe('5,00 €')
    expect(formatAmount(null)).toBe('-')
    expect(formatAmount('')).toBe('-')
    expect(formatAmount('abc')).toBe('-')
  })
})

describe('formatDate / formatDateTime', () => {
  it('liest Zeitstempel ohne Offset als UTC (Backend-Konvention)', () => {
    // 10:00 UTC = 12:00 Sommerzeit Berlin
    expect(formatDateTime('2026-06-01T10:00:00')).toBe('1.6.2026, 12:00:00')
    expect(formatDateTime('2026-06-01 10:00:00')).toBe('1.6.2026, 12:00:00')
  })

  it('respektiert einen expliziten Offset', () => {
    expect(formatDateTime('2026-01-15T10:00:00Z')).toBe('15.1.2026, 11:00:00')
    expect(formatDateTime('2026-01-15T10:00:00+01:00')).toBe('15.1.2026, 10:00:00')
  })

  it('liest reine Datumswerte als Kalendertag (kein Tagesversatz)', () => {
    expect(formatDate('2026-03-01')).toBe('1.3.2026')
    const d = parseDate('2026-03-01')
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours()]).toEqual([2026, 2, 1, 0])
  })

  it('nimmt Intl-Optionen fuer Kurzformate', () => {
    expect(formatDateTime('2026-06-01T10:00:00Z', { dateStyle: 'short', timeStyle: 'short' })).toBe('01.06.26, 12:00')
  })

  it('liefert "-" fuer leere und ungueltige Werte', () => {
    expect(formatDate(null)).toBe('-')
    expect(formatDate('')).toBe('-')
    expect(formatDate('kein datum')).toBe('-')
    expect(formatDateTime(undefined)).toBe('-')
    expect(formatDateTime('2026-13-45T99:00:00')).toBe('-')
  })
})

describe('formatBytes', () => {
  it('nutzt deutsches Dezimaltrennzeichen', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1,5 KB')
    expect(formatBytes(5 * 1024 * 1024 * 1024)).toBe('5,0 GB')
  })
})
