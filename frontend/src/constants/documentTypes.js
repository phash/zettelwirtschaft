/**
 * Human-readable German labels for document type enum values.
 */
export const typeLabels = {
  RECHNUNG: 'Rechnung',
  QUITTUNG: 'Quittung',
  KAUFVERTRAG: 'Kaufvertrag',
  GARANTIESCHEIN: 'Garantieschein',
  VERSICHERUNGSPOLICE: 'Versicherungspolice',
  KONTOAUSZUG: 'Kontoauszug',
  LOHNABRECHNUNG: 'Lohnabrechnung',
  STEUERBESCHEID: 'Steuerbescheid',
  MIETVERTRAG: 'Mietvertrag',
  HANDWERKER_RECHNUNG: 'Handwerkerrechnung',
  ARZTRECHNUNG: 'Arztrechnung',
  REZEPT: 'Rezept',
  AMTLICHES_SCHREIBEN: 'Amtliches Schreiben',
  BEDIENUNGSANLEITUNG: 'Bedienungsanleitung',
  SONSTIGES: 'Sonstiges',
}

/**
 * Ordered list of all document type enum values.
 */
export const documentTypes = Object.keys(typeLabels)
