# E-Mail-Anbindung (Issue #18)

## Ziel

Automatischer IMAP-Abruf von E-Mails aus mehreren Konten. LLM entscheidet ueber Relevanz. Relevante Anhaenge und E-Mail-Texte werden in die bestehende Verarbeitungs-Pipeline eingespeist. Konfiguration ueber die Web-UI.

## Ansatz

IMAP-Polling-Service als neuer Background-Task im Backend-Container. Kein zusaetzlicher Docker-Service. Neuer `JobSource.EMAIL` speist in die bestehende Queue (ProcessingJob -> OCR -> LLM -> Archivierung).

## Datenmodell

### Neue Tabelle `EmailAccount`

| Feld | Typ | Beschreibung |
|------|-----|-------------|
| id | Integer PK | |
| name | String | Anzeigename (z.B. "Gmail Privat") |
| imap_host | String | IMAP-Server |
| imap_port | Integer | Default 993 |
| use_ssl | Boolean | Default True |
| username | String | Login-Benutzername |
| encrypted_password | String | AES-verschluesselt (Fernet) |
| folder_inbox | String | Zu ueberwachender Ordner (Default "INBOX") |
| folder_processed | String | Zielordner nach Verarbeitung |
| schedule_type | Enum | CRON, MANUAL, IDLE |
| cron_expression | String | Cron-Ausdruck (nur bei CRON) |
| is_active | Boolean | Konto aktiv? |
| last_checked_at | DateTime | Letzter Abruf |
| last_error | String | Letzte Fehlermeldung |
| filing_scope_id | Integer FK | Optional: Dokumente diesem Scope zuordnen |
| created_at | DateTime | |
| updated_at | DateTime | |

### Neue Tabelle `ProcessedEmail`

| Feld | Typ | Beschreibung |
|------|-----|-------------|
| id | Integer PK | |
| email_account_id | Integer FK | Zugehoeriges Konto |
| message_id | String | IMAP Message-ID (UNIQUE pro Account) |
| subject | String | E-Mail-Betreff |
| sender | String | Absender |
| received_at | DateTime | Empfangszeitpunkt |
| status | Enum | RELEVANT, IRRELEVANT, FAILED |
| processing_job_id | Integer FK | Link zum Job (nullable) |
| created_at | DateTime | |

### Erweiterungen

- `JobSource` Enum: neuer Wert `EMAIL`
- `ProcessingJob`: neues Feld `email_account_id` (nullable FK)
- Migration: `009_add_email_accounts`

## E-Mail-Service

### `email_fetch_service.py`

IMAP-Verbindung via Python `imaplib` (Standardbibliothek). Pro Konto:

1. Verbinden + Authentifizieren
2. folder_inbox oeffnen, ungelesene E-Mails abrufen
3. Pro E-Mail:
   - Message-ID Duplikat-Check gegen ProcessedEmail
   - Parsen via `email` Standardbibliothek: Subject, Sender, Body, Anhaenge
   - LLM-Relevanzpruefung (neuer Prompt `email_relevance.txt`): Subject + Sender + Body-Snippet (max 1000 Zeichen) + Anhang-Dateinamen -> `{"relevant": true/false, "reason": "..."}`
   - Irrelevant: ProcessedEmail (IRRELEVANT) speichern, E-Mail in folder_processed verschieben
   - Relevant: Anhaenge als temp-Dateien -> je ein ProcessingJob (source=EMAIL). Body als .txt falls substanziell. ProcessedEmail (RELEVANT) speichern, E-Mail verschieben.

### Passwort-Verschluesselung

- AES-256 via `cryptography.fernet`
- Schluessel: `EMAIL_ENCRYPTION_KEY` in `.env` (auto-generiert beim ersten Konto falls leer)

### Scheduling

- **CRON**: Background-Task mit asyncio + `croniter` Library
- **MANUAL**: API-Endpoint `POST /api/email/accounts/{id}/fetch`
- **IDLE**: IMAP IDLE Command (Push-Notification, langlebige Verbindung)

## API-Endpoints

Neuer Router `api/email.py`:

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| GET | /api/email/accounts | Alle Konten auflisten |
| POST | /api/email/accounts | Neues Konto anlegen |
| PUT | /api/email/accounts/{id} | Konto bearbeiten |
| DELETE | /api/email/accounts/{id} | Konto loeschen |
| POST | /api/email/accounts/{id}/test | Verbindung testen |
| POST | /api/email/accounts/{id}/fetch | Manueller Abruf |
| GET | /api/email/accounts/{id}/history | Verarbeitete E-Mails (paginiert) |
| GET | /api/email/stats | Statistik pro Konto |

## Frontend

### EmailSettingsView.vue (oder Tab in SettingsView)

- Liste konfigurierter Konten (Name, Host, letzter Abruf, Status)
- Formular: Konto hinzufuegen/bearbeiten (IMAP-Host, Port, SSL, User, Passwort, Ordner, Schedule, Cron, Ablagebereich)
- "Verbindung testen"-Button
- "Jetzt abrufen"-Button pro Konto
- Verlauf: letzte verarbeitete E-Mails mit Status

### Dashboard-Erweiterung

- E-Mail-Statistik-Karte (wenn Konten konfiguriert): "X E-Mails geprueft, Y Dokumente importiert"

## Dependencies

- `cryptography` (Fernet-Verschluesselung) - neu
- `croniter` (Cron-Parsing) - neu
- Kein neuer Docker-Service

## Verarbeitungsfluss

```
Scheduler/Manuell/IDLE
  -> IMAP Connect + Authenticate
  -> Ungelesene E-Mails in folder_inbox abrufen
  -> Pro E-Mail:
      -> Message-ID Duplikat-Check (ProcessedEmail)
      -> E-Mail parsen (Subject, Sender, Body, Anhaenge)
      -> LLM-Relevanzpruefung (email_relevance.txt Prompt)
      -> IRRELEVANT: ProcessedEmail speichern, E-Mail verschieben
      -> RELEVANT:
          -> Anhaenge als temp-Dateien -> je ein ProcessingJob (source=EMAIL)
          -> Body als .txt -> ProcessingJob (falls substanziell)
          -> ProcessedEmail speichern, E-Mail verschieben
      -> Bestehende Pipeline uebernimmt (OCR -> LLM-Analyse -> Archivierung)
```
