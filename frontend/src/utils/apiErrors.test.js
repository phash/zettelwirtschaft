import { describe, it, expect } from 'vitest'
import { apiErrorMessage, NETWORK_MESSAGE, TIMEOUT_MESSAGE, TOO_LARGE_MESSAGE } from './apiErrors'

const httpError = (status, data) => ({ response: { status, data } })

describe('apiErrorMessage', () => {
  it('uebernimmt eine Text-Detail-Meldung des Backends', () => {
    expect(apiErrorMessage(httpError(409, { detail: 'Läuft bereits' }), 'x')).toBe('Läuft bereits')
  })

  it('macht aus einem pydantic-422-Array eine deutsche Meldung statt JSON', () => {
    const err = httpError(422, {
      detail: [
        { type: 'string_too_short', loc: ['body', 'name'], msg: 'String should have at least 1 character', ctx: { min_length: 1 } },
        { type: 'missing', loc: ['body', 'imap_host'], msg: 'Field required' },
        { type: 'string_too_short', loc: ['body', 'password'], msg: '...', ctx: { min_length: 8 } },
      ],
    })
    expect(apiErrorMessage(err, 'Fehler')).toBe(
      'Bitte Eingaben prüfen: Name darf nicht leer sein; IMAP-Server ist erforderlich; Passwort muss mindestens 8 Zeichen haben.',
    )
  })

  it('reicht Texte eigener Validatoren ohne "Value error"-Praefix durch', () => {
    const err = httpError(422, {
      detail: [{ type: 'value_error', loc: ['body', 'cron_expression'], msg: 'Value error, Ungültiger Cron-Ausdruck' }],
    })
    expect(apiErrorMessage(err)).toBe('Bitte Eingaben prüfen: Zeitplan (Cron) Ungültiger Cron-Ausdruck.')
  })

  it('erkennt Timeout, Netzwerkfehler und 413 (nginx-HTML ohne detail)', () => {
    expect(apiErrorMessage({ code: 'ECONNABORTED' }, 'x')).toBe(TIMEOUT_MESSAGE)
    expect(apiErrorMessage({ code: 'ERR_NETWORK' }, 'x')).toBe(NETWORK_MESSAGE)
    expect(apiErrorMessage(httpError(413, '<html>413</html>'), 'x')).toBe(TOO_LARGE_MESSAGE)
  })

  it('faellt sonst auf den Fallback zurueck', () => {
    expect(apiErrorMessage(httpError(500, {}), 'Speichern fehlgeschlagen.')).toBe('Speichern fehlgeschlagen.')
    expect(apiErrorMessage(null, 'F')).toBe('F')
    expect(apiErrorMessage({ code: 'ERR_CANCELED' }, 'F')).toBe('F')
  })
})
