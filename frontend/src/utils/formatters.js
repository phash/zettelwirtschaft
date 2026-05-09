export function formatDate(d) {
  if (d == null || d === '') return '-'
  const dt = new Date(d)
  if (Number.isNaN(dt.getTime())) return '-'
  return dt.toLocaleDateString('de-DE')
}

export function formatDateTime(d) {
  if (d == null || d === '') return '-'
  const dt = new Date(d)
  if (Number.isNaN(dt.getTime())) return '-'
  return dt.toLocaleString('de-DE')
}

export function formatAmount(val, currency = 'EUR') {
  if (val == null || val === '') return '-'
  const n = typeof val === 'number' ? val : parseFloat(val)
  if (!Number.isFinite(n)) return '-'
  return new Intl.NumberFormat('de-DE', { style: 'currency', currency: currency || 'EUR' }).format(n)
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}
