// Aufteilungs-Editor (Spezifikation 12): aus Schnitten und weggelassenen Seiten
// die Teile berechnen — dieselben Regeln wie die Backend-Validierung
// (split_service.validate_parts), damit der Benutzer Fehler vor dem Absenden sieht.

// Seitenliste -> "1–2", "4", "3–11, 13–44"
export function formatPageRanges(pages) {
  const sorted = [...pages].sort((a, b) => a - b)
  const ranges = []
  let start = null
  let prev = null
  for (const page of sorted) {
    if (start === null) {
      start = prev = page
    } else if (page === prev + 1) {
      prev = page
    } else {
      ranges.push(start === prev ? `${start}` : `${start}–${prev}`)
      start = prev = page
    }
  }
  if (start !== null) ranges.push(start === prev ? `${start}` : `${start}–${prev}`)
  return ranges.join(', ')
}

// "1–2" (bzw. "1–5" mit Luecken) als Beschriftung eines Teils
export function partLabel(part) {
  const range = formatPageRanges(part.pages)
  const noun = part.pages.length === 1 ? 'Seite' : 'Seiten'
  return `Teil ${part.index} · ${noun} ${range}`
}

// Document.source_pages des Backends ("3-44", "3-11,13-44", "5") als Text:
// "Seiten 3–44", "Seiten 3–11, 13–44", "Seite 5"
export function sourcePagesLabel(sourcePages) {
  if (!sourcePages) return ''
  const text = sourcePages.replaceAll('-', '–').replaceAll(',', ', ')
  return /[-,]/.test(sourcePages) ? `Seiten ${text}` : `Seite ${text}`
}

/**
 * @param {number} pageCount
 * @param {Set<number>} cuts       Schnitt NACH Seite n
 * @param {Set<number>} excluded   weggelassene Seiten
 * @param {Set<number>} blankPages Leerseiten
 * @returns {{ parts: {index:number, pages:number[], range:number[]}[], segmentOf: Record<number, number>, error: string|null }}
 *   ``segmentOf`` ordnet jeder Seite den (Anzeige-)Abschnitt zu, auch weggelassenen.
 */
export function computeParts(pageCount, cuts, excluded, blankPages = new Set()) {
  const segments = []
  let current = []
  for (let n = 1; n <= pageCount; n += 1) {
    current.push(n)
    if (cuts.has(n) || n === pageCount) {
      segments.push(current)
      current = []
    }
  }
  const segmentOf = {}
  const parts = []
  segments.forEach((segment) => {
    const pages = segment.filter((n) => !excluded.has(n))
    // Ein Abschnitt nur aus weggelassenen Seiten ergibt keinen Teil.
    if (pages.length) parts.push({ index: parts.length + 1, pages, range: segment })
    for (const n of segment) segmentOf[n] = pages.length ? parts.length : null
  })

  let error = null
  if (!parts.length) {
    error = 'Mindestens eine Seite muss übernommen werden.'
  } else {
    const onlyBlank = parts.find((part) => part.pages.every((n) => blankPages.has(n)))
    if (onlyBlank) error = `Teil ${onlyBlank.index} besteht nur aus Leerseiten.`
  }
  return { parts, segmentOf, error }
}

// Request-Body fuer POST /api/split/{id}/confirm
export function confirmBody(version, parts, excluded) {
  return {
    version,
    parts: parts.map((part) => ({ pages: part.pages })),
    excluded: [...excluded].sort((a, b) => a - b),
  }
}
