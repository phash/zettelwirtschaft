// Tab-Fokusfalle fuer modale Dialoge (aria-modal="true"): Tab/Shift+Tab
// zirkulieren nur innerhalb von `container`; steht der Fokus ausserhalb, wird
// er auf das erste Element zurueckgeholt. Aus dem keydown-Handler des Dialogs
// aufrufen (ConfirmDialog, "Mehr"-Menue der BottomNav).
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function trapTabKey(event, container) {
  if (event.key !== 'Tab' || !container) return
  const focusable = [...container.querySelectorAll(FOCUSABLE)]
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const active = document.activeElement
  if (!container.contains(active)) {
    event.preventDefault()
    first.focus()
  } else if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}
