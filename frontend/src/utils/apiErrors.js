// Einheitliche, deutsche Fehlermeldung aus einem axios-Fehler.
// Vorher zeigten Views `e.response?.data?.detail || fallback` — bei einem 422
// ist detail aber ein Array aus pydantic-Objekten, das als JSON-Dump im Toast
// landete. Hier wird es auf "Bitte Eingaben prüfen: Name ist erforderlich." gemappt.
import { MAX_UPLOAD_SIZE_MB } from '../constants/fileTypes'

export const TIMEOUT_MESSAGE =
  'Zeitüberschreitung – der Server hat nicht rechtzeitig geantwortet. Der Vorgang läuft eventuell im Hintergrund weiter.'
export const NETWORK_MESSAGE = 'Keine Verbindung zum Server.'
export const TOO_LARGE_MESSAGE = `Datei zu groß (max. ${MAX_UPLOAD_SIZE_MB} MB).`

// Feldnamen der Backend-Schemas -> Beschriftung in der UI.
const FIELD_LABELS = {
  name: 'Name',
  title: 'Titel',
  description: 'Beschreibung',
  keywords: 'Schlüsselwörter',
  color: 'Farbe',
  imap_host: 'IMAP-Server',
  imap_port: 'Port',
  username: 'Benutzername',
  password: 'Passwort',
  folder_inbox: 'Posteingangs-Ordner',
  folder_processed: 'Ordner für verarbeitete Mails',
  cron_expression: 'Zeitplan (Cron)',
  schedule_type: 'Abruf-Art',
  amount: 'Betrag',
  currency: 'Währung',
  document_date: 'Datum',
  document_type: 'Dokumenttyp',
  issuer: 'Aussteller',
  recipient: 'Empfänger',
  reference_number: 'Referenznummer',
  tax_year: 'Steuerjahr',
  tax_category: 'Steuerkategorie',
  year: 'Jahr',
  answer: 'Antwort',
  question: 'Frage',
  pin: 'PIN',
  filing_scope_id: 'Ablagebereich',
}

function fieldLabel(loc) {
  if (!Array.isArray(loc)) return ''
  // loc = ["body", "name"] bzw. ["query", "year"] — der erste Teil ist die Quelle.
  const parts = loc.filter((p) => !['body', 'query', 'path', 'header'].includes(p) && typeof p !== 'number')
  const field = parts[parts.length - 1]
  if (!field) return ''
  return FIELD_LABELS[field] || String(field)
}

function describe(item) {
  const ctx = item?.ctx || {}
  switch (item?.type) {
    case 'missing':
      return 'ist erforderlich'
    case 'string_too_short':
      return ctx.min_length > 1 ? `muss mindestens ${ctx.min_length} Zeichen haben` : 'darf nicht leer sein'
    case 'string_too_long':
      return `darf höchstens ${ctx.max_length} Zeichen haben`
    case 'too_short':
      return ctx.min_length > 1 ? `muss mindestens ${ctx.min_length} Einträge haben` : 'darf nicht leer sein'
    case 'too_long':
      return `darf höchstens ${ctx.max_length} Einträge haben`
    case 'int_parsing':
    case 'int_type':
    case 'int_from_float':
      return 'muss eine ganze Zahl sein'
    case 'float_parsing':
    case 'float_type':
    case 'decimal_parsing':
    case 'decimal_type':
      return 'muss eine Zahl sein'
    case 'greater_than':
      return `muss größer als ${ctx.gt} sein`
    case 'greater_than_equal':
      return `muss mindestens ${ctx.ge} sein`
    case 'less_than':
      return `muss kleiner als ${ctx.lt} sein`
    case 'less_than_equal':
      return `darf höchstens ${ctx.le} sein`
    case 'date_parsing':
    case 'date_from_datetime_parsing':
    case 'date_type':
    case 'datetime_parsing':
    case 'datetime_from_date_parsing':
      return 'muss ein gültiges Datum sein'
    case 'enum':
    case 'literal_error':
      return 'hat einen ungültigen Wert'
    case 'string_pattern_mismatch':
      return 'hat ein ungültiges Format'
    case 'bool_parsing':
    case 'bool_type':
      return 'muss ja oder nein sein'
    case 'value_error':
    case 'assertion_error': {
      // Eigene Validatoren liefern bereits deutsche Texte ("Value error, ...").
      const msg = String(item?.msg || '').replace(/^(Value|Assertion) error,\s*/i, '')
      return msg || 'ist ungültig'
    }
    default:
      return 'ist ungültig'
  }
}

export function validationMessage(detail) {
  const parts = detail
    .map((item) => {
      const label = fieldLabel(item?.loc)
      const text = describe(item)
      return label ? `${label} ${text}` : text.charAt(0).toUpperCase() + text.slice(1)
    })
    .filter(Boolean)
  // Doppelte Meldungen (z.B. Union-Typen) nur einmal zeigen.
  const unique = [...new Set(parts)]
  return `Bitte Eingaben prüfen: ${unique.join('; ')}.`
}

export function apiErrorMessage(error, fallback = 'Ein Fehler ist aufgetreten.') {
  if (!error) return fallback
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') return TIMEOUT_MESSAGE
  const response = error.response
  if (!response) {
    // Abbruch durch den Nutzer (AbortController) ist kein Fehler fuer die Anzeige.
    if (error.code === 'ERR_CANCELED') return fallback
    if (error.code === 'ERR_NETWORK') return NETWORK_MESSAGE
    return fallback
  }
  // nginx antwortet bei zu grossem Body mit einer HTML-Seite ohne detail.
  if (response.status === 413) return TOO_LARGE_MESSAGE
  const detail = response.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length) return validationMessage(detail)
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  return fallback
}
