/**
 * Single Source of Truth pro Dokumenttyp (Enum-Wert -> Anzeige).
 * label: deutsche Bezeichnung (Badge, Filter, Bearbeiten-Dropdown — ueberall gleich)
 * color: Tailwind-Klassen fuer DocTypeBadge
 * Neuer Enum-Wert (backend models/document.py DocumentType) -> nur hier ergaenzen.
 */
export const documentTypeMeta = {
  RECHNUNG: { label: 'Rechnung', color: 'bg-blue-100 text-blue-700' },
  QUITTUNG: { label: 'Quittung', color: 'bg-green-100 text-green-700' },
  KAUFVERTRAG: { label: 'Kaufvertrag', color: 'bg-purple-100 text-purple-700' },
  GARANTIESCHEIN: { label: 'Garantieschein', color: 'bg-yellow-100 text-yellow-700' },
  VERSICHERUNGSPOLICE: { label: 'Versicherungspolice', color: 'bg-indigo-100 text-indigo-700' },
  KONTOAUSZUG: { label: 'Kontoauszug', color: 'bg-cyan-100 text-cyan-700' },
  LOHNABRECHNUNG: { label: 'Lohnabrechnung', color: 'bg-emerald-100 text-emerald-700' },
  STEUERBESCHEID: { label: 'Steuerbescheid', color: 'bg-orange-100 text-orange-700' },
  MIETVERTRAG: { label: 'Mietvertrag', color: 'bg-rose-100 text-rose-700' },
  HANDWERKER_RECHNUNG: { label: 'Handwerkerrechnung', color: 'bg-amber-100 text-amber-700' },
  ARZTRECHNUNG: { label: 'Arztrechnung', color: 'bg-red-100 text-red-700' },
  REZEPT: { label: 'Rezept', color: 'bg-pink-100 text-pink-700' },
  AMTLICHES_SCHREIBEN: { label: 'Amtliches Schreiben', color: 'bg-slate-100 text-slate-700' },
  BEDIENUNGSANLEITUNG: { label: 'Bedienungsanleitung', color: 'bg-teal-100 text-teal-700' },
  SONSTIGES: { label: 'Sonstiges', color: 'bg-gray-100 text-gray-700' },
}

export const DEFAULT_TYPE_COLOR = 'bg-gray-100 text-gray-700'

/**
 * Human-readable German labels for document type enum values.
 */
export const typeLabels = Object.fromEntries(
  Object.entries(documentTypeMeta).map(([type, meta]) => [type, meta.label]),
)

/**
 * Ordered list of all document type enum values.
 */
export const documentTypes = Object.keys(documentTypeMeta)
