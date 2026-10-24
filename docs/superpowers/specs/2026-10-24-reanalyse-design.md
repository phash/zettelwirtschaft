# Dokumente neu analysieren — mit Änderungsvorschlag (Issue #39)

Stand: 2026-10-24, abgestimmt mit dem Owner im Chat.

## Warum

Alte Dokumente sollen sich neu einlesen lassen. Das ist vor allem dann wichtig, wenn das Modell
gewechselt wurde, also z.B. von `qwen2.5:7b-instruct` auf `gemma4:*`. Es gibt zwei Anlässe:

1. **Einzelnes Dokument** mit falsch erkannten Feldern → am Dokument neu analysieren.
2. **Altbestand nach Modellwechsel** → in der Übersicht viele oder alle Dokumente neu analysieren.

Leitgedanke des Owners: **Man sieht erst einen Diff und entscheidet dann.** Eine Re-Analyse
überschreibt nie ungefragt. Ein schlechteres neues Modell darf gute Werte nicht unbemerkt
ersetzen, auch nicht in einem Lauf über 500 Dokumente.

## Ist-Zustand

- `reanalysis_service.run_reanalysis` (Hintergrund-Vorgang `reanalyze`, `POST /api/review/documents/{id}/reanalyze`)
  wertet den **gespeicherten** OCR-Text neu aus (`analyze_document(..., pre_ocr_text=...)`) und
  übernimmt das Ergebnis **direkt**. Vom Benutzer bearbeitete Felder (AuditLog UPDATED), manuelle
  Tag-Entscheidungen, ein vom Benutzer gesetzter Ablagebereich und eine bearbeitete Garantie bleiben erhalten.
- Im Frontend gibt es den Aufruf nur in `ReviewView.vue`, und nur wenn die erste KI-Analyse
  ausgefallen ist (`needsReanalysis`). In `DocumentDetailView.vue` und `DocumentsView.vue` gibt es
  ihn nicht. Die Dokumentliste kennt keine Mehrfachauswahl.
- Am Dokument wird **nicht** gespeichert, mit welchem Modell es analysiert wurde.
- Analysen teilen sich einen prozessweiten Slot (`analysis_service.analysis_slot`,
  `ANALYSIS_CONCURRENCY=1`). Re-Analysen sind auf 3 aktive begrenzt (`start_task(max_active=...)`).
- CPU-Richtwert: ~40 s pro Dokument mit `gemma4:e2b-it-qat`. 500 Dokumente brauchen also mehrere Stunden.

## Ziele

- **G1 Einzeldokument:** In der Detailansicht ist „Neu analysieren“ immer verfügbar. Optional mit
  „Text neu erkennen (OCR)“, standardmäßig aus.
- **G2 Vorschlag statt Überschreiben:** Jede Re-Analyse (einzeln und im Massenlauf) erzeugt einen
  **Änderungsvorschlag**. Übernommen wird erst nach Bestätigung, pro Feld oder für alles.
- **G3 Massenlauf:** In der Dokumentliste werden Dokumente per Häkchen ausgewählt und neu
  analysiert. Zusätzlich gibt es die Aktion „Alle Dokumente neu analysieren, die nicht mit dem
  aktuellen Modell ausgewertet wurden“. Ebenfalls mit OCR-Option.
- **G4 Modell merken:** Am Dokument steht, mit welchem Modell (`OLLAMA_MODEL`-Tag) es zuletzt
  analysiert bzw. geprüft wurde. Die Detailansicht zeigt das an.
- **G5 Prüfen großer Mengen:** Vorschläge lassen sich ohne 300 Einzelklicks abarbeiten.

## Nicht-Ziele

- Keine neue Aufteilung: Teildokumente aus Stapeln werden wie normale Dokumente neu analysiert,
  ohne Split-Erkennung.
- Kein Vergleich mehrerer Modelle nebeneinander und keine Modellwahl pro Lauf. Es zählt immer das
  konfigurierte `OLLAMA_MODEL`.
- Keine automatische Re-Analyse beim Modellwechsel. Der Lauf startet nur auf Klick.
- Das Embedding-Modell ist nicht Teil davon. Dafür gibt es schon den Auto-Rebuild bzw. die Wartung.

## Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Werte ändern sich, ohne dass der Benutzer zugestimmt hat. Das gilt auch für Tags, Ablagebereich,
  Garantie und den OCR-Text.
- Vom Benutzer korrigierte Felder werden überschrieben oder auch nur als Vorschlag angeboten, als
  wären sie KI-Werte.
- Der Massenlauf blockiert neue Uploads, Scans oder E-Mails. Er hält die DB gesperrt oder geht nach
  einem Neustart verloren.
- Eine missglückte Neu-Erkennung verschlechtert guten Text.

## Verhalten

### Neu analysieren (einzeln)

- Knopf „Neu analysieren“ in `DocumentDetailView`. Ein kurzer Dialog bietet die Checkbox
  „Text neu erkennen (OCR) — dauert länger“ (Standard: aus) an.
- Läuft im Hintergrund über `start_task` / `useBackgroundTask` (202 + Polling), wie heute.
- Ergebnis: ein Vorschlag am Dokument (siehe unten). Die Detailansicht zeigt danach die Diff-Ansicht.
  Gibt es keine Änderung, kommt nur der Hinweis „Keine Änderungen — mit <Modell> geprüft“, und das
  Modell wird am Dokument vermerkt.
- **Einheitlich auch in `ReviewView`:** Die bestehende Re-Analyse nach einem KI-Ausfall läuft
  ebenfalls über den Vorschlag (Diff zeigt „leer → neu“). *Entscheidung von Claude:* Es soll keinen
  zweiten, direkt überschreibenden Pfad geben.

### Text neu erkennen (OCR-Option)

- Die Archivdatei wird neu per OCR gelesen (bestehende OCR-Pipeline, gleiche Vorverarbeitung wie
  beim Import, auch digitale PDFs über pdfplumber). Der neue Text dient als Grundlage der Analyse.
- **Schutz:** Ist die neue OCR-Konfidenz klar schlechter als die gespeicherte, analysiert die KI
  mit dem **alten** Text. Der Vorschlag enthält dann keinen neuen Text, aber den Hinweis „Neu
  erkannter Text war schlechter und wurde verworfen“. Die Schwelle legt der Builder fest; sie gehört
  als benannte Konstante neben die übrigen Analyse-Schwellen.
- Ein neuer Text ist Teil des Vorschlags (Eintrag „Text neu erkannt, Konfidenz 62 % → 88 %“ mit
  aufklappbarer Vorschau alt/neu). Er wird nur mit der Übernahme gespeichert. Bei Übernahme werden
  FTS-Index und Vektoren aktualisiert.

### Änderungsvorschlag

- Pro Dokument höchstens **ein offener Vorschlag**. Ein neuer Lauf ersetzt einen offenen Vorschlag.
- Inhalt: die Felder aus `REANALYZED_FIELDS` + Ablagebereich + Tags (hinzu/weg) + Garantie + ggf.
  OCR-Text/-Konfidenz + `ai_confidence` + neue Rückfragen. Dazu Modell, Zeitpunkt, „mit OCR ja/nein“.
- **Umformulierte Texte zählen nicht** (Owner): Ändern sich nur `title` und/oder `summary`, gilt das
  Dokument als „unverändert“. Es bekommt keinen offenen Vorschlag und wird nur als geprüft markiert.
  Ändert sich zusätzlich ein anderes Feld, erscheinen Titel/Zusammenfassung im Diff mit und sind
  wie jedes Feld einzeln annehm- oder ablehnbar. Der Filter „nach geändertem Feld“ ignoriert reine
  Textänderungen.
- **Nur echte Änderungen** landen im Vorschlag. Geschützte Felder (Logik aus `user_edited_fields`,
  `_user_tag_decisions`, Garantie-Prefix, Scope) tauchen gar nicht auf.
- **Keine Änderung → kein offener Vorschlag:** Das Dokument wird nur als „mit <Modell> geprüft“
  markiert (G4).
- Übernahme: pro Feld annehmen/ablehnen oder „Alles übernehmen“ / „Verwerfen“. Die Übernahme nutzt
  dieselbe Anwendungslogik wie heute (Tags, Garantie inkl. `rearm_reminders`, Scope-Wechsel mit
  rollback-sicherer Dateiverschiebung, Rückfragen ersetzen, `review_status`, FTS, Revektorisierung,
  AuditLog `REANALYZED`). Übernommene Felder gelten **nicht** als vom Benutzer bearbeitet.
  Abgelehnte Felder werden nicht geschützt.
- **Abgelehnt / verworfen** ist erledigt. Das Modell wird trotzdem am Dokument vermerkt, damit
  „nicht mit aktuellem Modell“ das Dokument nicht erneut erfasst. Nach dem nächsten Modellwechsel
  kommt es wieder in Frage.
- **Veralteter Vorschlag** (*Entscheidung von Claude*): Bearbeitet der Benutzer ein Feld, während ein
  Vorschlag offen ist, gewinnt die Bearbeitung. Das Feld fällt aus dem Vorschlag bzw. wird beim
  Übernehmen übersprungen. Wird das Dokument gelöscht, verfällt der Vorschlag.
- Vorschläge laufen nicht ab (*Entscheidung von Claude*).
- **Modell bei offenem Vorschlag** (*Entscheidung von Claude*): Der Vorschlag trägt sein Modell. Ein
  Dokument mit offenem Vorschlag des aktuellen Modells zählt für „nicht mit aktuellem Modell“ als
  erledigt und wird dort übersprungen. Wer es per Häkchen ausdrücklich auswählt, ersetzt den
  offenen Vorschlag.
- **Teilweise Übernahme** (*Entscheidung von Claude*): Neue Rückfragen werden nur für übernommene
  Felder angelegt (bzw. für Freitext-Fragen ohne Feld, wenn mindestens ein Feld übernommen wird).
  Offene alte Fragen werden nur für übernommene Felder ersetzt. `ai_confidence` wird übernommen,
  sobald mindestens ein Feld übernommen wird. `review_status` wird danach wie heute aus den offenen
  Fragen neu bestimmt.

### Massenlauf

- **Auswahl:** In `DocumentsView` gibt es Häkchen pro Zeile/Karte (Desktop + Mobile) und „Alle auf
  dieser Seite“. Aktion „Neu analysieren“ (mit OCR-Option). Zweite Aktion „Alle, die nicht mit
  <aktuelles Modell> analysiert wurden (N Dokumente)“. Die Zahl N zeigt der Dialog vor dem Start
  zusammen mit einer groben Dauer-Schätzung an.
- Bei Bestandsdokumenten ohne gespeichertes Modell zählen beim ersten Mal **alle** als „nicht mit
  aktuellem Modell“.
- **Ablauf:** Der Lauf ist eine persistente Warteschlange (überlebt Neustarts und macht danach
  weiter). Fortschritt „124 von 500“, Pausieren, Fortsetzen, Abbrechen. Es läuft höchstens ein
  Massenlauf gleichzeitig (409 sonst).
- **Vorrang:** Neue Eingänge (PENDING-ProcessingJobs) gehen immer vor. Der Massenlauf nimmt das
  nächste Dokument erst, wenn keine PENDING-Jobs warten und der Analyse-Slot frei ist. Er hält keine
  Schreib-Transaktion über OCR/LLM.
- **Freigegebene Dokumente bleiben, wie sie sind** (Owner): Dokumente mit `review_status = REVIEWED`
  („Freigeben“ / „So übernehmen“) nimmt der Massenlauf nicht auf, weder bei „nicht mit aktuellem
  Modell“ noch per Häkchen. Der Dialog nennt sie („X freigegebene Dokumente bleiben unverändert“).
  Nur der Einzelknopf am Dokument kann sie ausdrücklich neu analysieren. Auch das ergibt nur einen
  Vorschlag (*Entscheidung von Claude*).
- **Umfang** (*Entscheidung von Claude*): alle übrigen nicht gelöschten Dokumente, auch
  `NEEDS_REVIEW` und Teildokumente. Dokumente ohne gespeicherten Text werden ohne OCR-Option übersprungen („nur mit
  ‚Text neu erkennen‘ möglich“) und in N gesondert ausgewiesen.
- **Fehler** (KI-Antwort unbrauchbar, Datei fehlt, kein Text): Das Dokument wird übersprungen, mit
  Grund vermerkt und am Ende aufgelistet. Ist Ollama nicht erreichbar oder fehlt das Modell (direkt
  nach einem Modellwechsel wahrscheinlich, weil der Auto-Pull noch läuft), wartet der Lauf mit
  Backoff und macht von selbst weiter, statt alle restlichen Dokumente als Fehler zu verbuchen. Die
  Fortschrittsanzeige zeigt „wartet auf KI“ (*Entscheidung von Claude*, Läufe sollen über Nacht
  durchlaufen).
- **Globale Pause** (*Entscheidung von Claude*): `pause_queue` hält auch den Massenlauf an.
- **Ende:** SYSTEM-Benachrichtigung „Neu-Analyse fertig: X Vorschläge, Y unverändert, Z Fehler“ mit
  Link zur Vorschlagsliste.

### Vorschläge prüfen

- **Ort** (*Entscheidung von Claude*): Bereich „Änderungsvorschläge“ auf der bestehenden Seite
  `/pruefen`, eigener Tab neben den Rückfragen, mit Zähler. Einzeldokument-Vorschläge erscheinen
  dort ebenfalls und zusätzlich direkt in der Detailansicht.
- Liste der Dokumente mit offenem Vorschlag. Pro Dokument eine Feldtabelle *alt → neu* mit Annehmen/
  Ablehnen pro Zeile, „Alles übernehmen“, „Verwerfen“. Ein Link öffnet das Dokument (Vorschau).
- **Für große Mengen:** „Alle übernehmen“ und „Alle verwerfen“ über die gesamte Liste (mit
  Bestätigungsdialog und Anzahl), dazu ein Filter nach geändertem Feld (z.B. nur Dokumente, bei denen
  sich Betrag oder Typ ändert). Die Massen-Übernahme läuft als Hintergrund-Vorgang, falls sie lange
  dauert (Scope-Verschiebungen, Revektorisierung).

## Randbedingungen

- Konventionen aus `CLAUDE.md`: Alembic-Migration auf den aktuellen Head (019, `detect_stamp`
  ergänzen), `UTCDateTime`, Pydantic-Schemas, UI-Texte deutsch mit Umlauten,
  `api-types.ts` + E2E-Mocks nachziehen, Body-Limits beachten.
- Lange Vorgänge: 202 + `TaskResponse` / Polling, eigene kurze Sessions in `work()`.
- Analyse ausschließlich über `analyze_document` (Slot, Sanitizer, Verifier, Few-Shot) — keine
  zweite Pipeline. Auto-Korrekturen (`apply_auto_corrections`) fließen in den Vorschlag ein.
- Die bestehende Logik „was ist geschützt“ und „wie wird angewendet“ aus `reanalysis_service` wird
  wiederverwendet bzw. in „Vorschlag berechnen“ + „Vorschlag anwenden“ zerlegt, nicht kopiert.
- Heim-Hardware: ein Dokument gleichzeitig, SQLite ohne WAL.

## Dem Builder überlassen

- Datenmodell des Vorschlags (eigene Tabelle vs. JSON-Spalte) und des Massenlaufs (eigene Tabelle
  vs. Wiederverwendung von `ProcessingJob` / `BackgroundTask`), solange Neustart-Fortsetzung,
  Vorrang neuer Eingänge und „ein offener Vorschlag pro Dokument“ gelten.
- API-Pfade und -Schnitte (Vorschlag lesen/anwenden/verwerfen, Massenlauf starten/pausieren/abbrechen/Status).
- Schwelle „OCR klar schlechter“ und die Formel der Dauer-Schätzung.
- Konkrete Gestaltung von Dialogen und Diff-Tabelle im bestehenden Tailwind-Stil.
