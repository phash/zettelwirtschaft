// In die Zwischenablage kopieren — auch ueber plain-HTTP im LAN.
// navigator.clipboard existiert nur in Secure Contexts (HTTPS/localhost); der
// Standardzugriff http://<LAN-IP>:8080 ist keiner. Dort hilft nur der (veraltete,
// aber ueberall unterstuetzte) execCommand('copy') ueber ein verstecktes Textfeld.
// Liefert true bei Erfolg, false sonst — die Meldung (Toast) macht der Aufrufer.
export async function copyToClipboard(text) {
  const value = String(text ?? '')
  if (typeof window !== 'undefined' && window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // z.B. Berechtigung verweigert -> Fallback versuchen
    }
  }
  return legacyCopy(value)
}

function legacyCopy(value) {
  if (typeof document === 'undefined' || !document.body) return false
  const previousFocus = document.activeElement
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.setAttribute('aria-hidden', 'true')
  textarea.style.position = 'fixed'
  textarea.style.top = '-1000px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  try {
    textarea.select()
    textarea.setSelectionRange(0, value.length)
    return document.execCommand('copy') === true
  } catch {
    return false
  } finally {
    textarea.remove()
    if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus()
  }
}
