// Treffer-Markierungen im Such-Snippet. Das Backend umschliesst Treffer mit
// STX (\u0002) ... ETX (\u0003) statt mit HTML (search_service.HIGHLIGHT_START/
// HIGHLIGHT_END). Hier wird der Text in Segmente zerlegt, die das Template als
// <mark>- bzw. Text-Knoten rendert — bewusst ohne v-html, damit OCR-Text nie
// als Markup interpretiert wird.
export const HIGHLIGHT_START = '\u0002'
export const HIGHLIGHT_END = '\u0003'

/**
 * @param {string} text
 * @returns {{ text: string, hit: boolean }[]}
 */
export function highlightSegments(text) {
  if (!text) return []
  const segments = []
  let hit = false
  let buffer = ''
  for (const ch of text) {
    if (ch === HIGHLIGHT_START || ch === HIGHLIGHT_END) {
      if (buffer) segments.push({ text: buffer, hit })
      buffer = ''
      hit = ch === HIGHLIGHT_START
    } else {
      buffer += ch
    }
  }
  if (buffer) segments.push({ text: buffer, hit })
  return segments
}
