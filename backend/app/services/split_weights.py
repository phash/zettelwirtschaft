"""Startgewichte der Aufteilungs-Heuristik (Spezifikation 12, "Scoring eines Uebergangs").

Log-Odds-Beitraege je Signal fuer den Uebergang Seite i | i+1; die Summe geht
ueber ``sigmoid`` in eine Wahrscheinlichkeit "neues Dokument beginnt". Kalibriert
mit ``backend/scripts/eval_split.py`` an lokalen Beispiel-Stapeln (bleiben
ausserhalb des Repos). Bei einer Aenderung ``DETECTOR_VERSION`` in
``split_detection_service`` hochzaehlen, damit gespeicherte Vorschlaege
auswertbar bleiben.
"""

BASE = -2.0  # die meisten Uebergaenge sind keine Grenze

COUNTER_RESTART = 4.0  # Zaehler auf i+1 ist "Seite 1"
COUNTER_LAST = 2.0  # Zaehler auf i ist die letzte Seite (x == y)
COUNTER_CONTINUES = -4.0  # Zaehler laeuft weiter (x+1, gleiches y)

ADDRESS_SALUTATION = 3.0  # Anschrift + Anrede auf i+1
# Kalibrierung (Beispiel-Stapel): Formulare/Vertraege beginnen mit Anschrift,
# aber ohne Anrede. Allein ist die Anschrift schwaecher (sie steht auch mitten
# in Dokumenten, z.B. Liste versicherter Orte).
ADDRESS_ONLY = 1.5
SAME_REFERENCE = -3.0  # gleiche Referenznummer (Ueberlappung)
DIFFERENT_REFERENCES = 3.0  # Referenzen auf beiden Seiten, disjunkt
SENDER_ID_CHANGES = 2.5  # IBAN / USt-ID wechselt (beide vorhanden)

HEADER_SIMILAR = -1.5  # Kopfzeilen-Jaccard >= HEADER_SIMILAR_MIN
HEADER_SIMILAR_MIN = 0.4
HEADER_DISJOINT_NO_COUNTER = 1.0  # Jaccard = 0 und auf keiner Seite ein Zaehler

DATE_GAP = 1.0  # Hauptdatum mehr als DATE_GAP_DAYS auseinander
DATE_GAP_DAYS = 31

FORMAT_CHANGE = 2.0  # Breite (> FORMAT_TOLERANCE_PT) oder Ausrichtung wechselt
FORMAT_TOLERANCE_PT = 3.0

MARGIN_CODE_CHANGES = 3.0  # Randcode: Gesamtzahl n wechselt (andere Sendung)

BLANK_BACK = -2.0  # Leerseite i+1, Zaehler laeuft ueber sie weiter
BLANK_SEPARATOR = 2.5  # Leerseite i+1, danach Anschrift oder Zaehler-Neustart

DUPLEX_SPLITS_SHEET = -3.0  # Duplex (conf >= DUPLEX_MIN_CONF), Schnitt nach ungerader Seite
DUPLEX_MIN_CONF = 0.8
DUPLEX_PAIR_TOLERANCE_PT = 1.0  # Hoehendifferenz innerhalb eines Blatts
# Anteil der Blattwechsel (2|3, 4|5 ...) mit Hoehendifferenz > Toleranz, ab dem
# die Hoehenmessung des Einzugs als Duplex-Beleg zaehlt ("zwischen Paaren
# haeufig > 1,0 pt"). Ohne diese Bedingung waere jedes PDF mit gleich hohen
# Seiten (digital, Flachbett) "duplex".
DUPLEX_MIN_SHEET_CHANGES = 0.3

# Entscheidung ueber p = sigmoid(score)
P_CUT = 0.80
P_NO_CUT = 0.25

# Boilerplate: Kopfzeilen, die auf >= diesem Anteil der Textseiten (und
# mindestens BOILERPLATE_MIN_PAGES Seiten) vorkommen, zaehlen nicht. Ab 3
# Seiten: bei zwei Seiten waere jede gemeinsame Zeile "Boilerplate" und der
# Kopfzeilen-Vergleich wirkungslos.
BOILERPLATE_SHARE = 0.30
BOILERPLATE_MIN_PAGES = 3
