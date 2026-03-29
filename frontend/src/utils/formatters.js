export function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('de-DE')
}

export function formatDateTime(d) {
  if (!d) return '-'
  return new Date(d).toLocaleString('de-DE')
}

export function formatAmount(val, currency = 'EUR') {
  if (val == null) return '-'
  return new Intl.NumberFormat('de-DE', { style: 'currency', currency }).format(val)
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}
