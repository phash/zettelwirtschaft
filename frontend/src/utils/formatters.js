// Zentrale Anzeige-Formatierung (de-DE). Views sollen keine eigenen
// toLocaleString()-Varianten bauen — nur so greifen Invalid-Date-Guard und
// Zeitzonen-Regel ueberall gleich.

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/
// ISO-Zeitstempel ohne Offset ("2026-10-17T10:00:00" bzw. mit Leerzeichen).
const NAIVE_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

// Backend-Konvention: Zeitstempel ohne Offset sind UTC (SQLite CURRENT_TIMESTAMP,
// Altdaten vor UTCDateTime). new Date() wuerde sie als Lokalzeit lesen und die
// Uhrzeit um 1-2 h verschieben. Reine Datumswerte ("2026-03-01") dagegen sind
// Kalendertage und werden lokal interpretiert — sonst rutscht der Tag in
// Zeitzonen westlich von UTC auf den Vortag.
export function parseDate(d) {
  if (d == null || d === '') return null
  if (d instanceof Date) return Number.isNaN(d.getTime()) ? null : d
  let dt
  if (typeof d === 'string' && DATE_ONLY.test(d)) {
    const [y, m, day] = d.split('-').map(Number)
    dt = new Date(y, m - 1, day)
  } else if (typeof d === 'string' && NAIVE_DATETIME.test(d)) {
    dt = new Date(d.replace(' ', 'T') + 'Z')
  } else {
    dt = new Date(d)
  }
  return Number.isNaN(dt.getTime()) ? null : dt
}

export function formatDate(d) {
  const dt = parseDate(d)
  return dt ? dt.toLocaleDateString('de-DE') : '-'
}

// options: Intl.DateTimeFormat-Optionen fuer Kurzformate, z.B.
// { dateStyle: 'short', timeStyle: 'short' } im Chat-Verlauf.
export function formatDateTime(d, options = undefined) {
  const dt = parseDate(d)
  return dt ? dt.toLocaleString('de-DE', options) : '-'
}

export function formatAmount(val, currency = 'EUR') {
  if (val == null || val === '') return '-'
  const n = typeof val === 'number' ? val : parseFloat(val)
  if (!Number.isFinite(n)) return '-'
  const code = currency || 'EUR'
  try {
    return new Intl.NumberFormat('de-DE', { style: 'currency', currency: code }).format(n)
  } catch {
    // Kein ISO-4217-Code (z.B. LLM-Wert "€" oder "Euro"): Intl wirft einen
    // RangeError, der im Template die ganze Liste leer rendern wuerde.
    const num = n.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    return `${num} ${code}`
  }
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  const digits = i > 0 ? 1 : 0
  const num = size.toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return `${num} ${units[i]}`
}
