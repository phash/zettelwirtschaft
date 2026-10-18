"""Reine Funktionen der Aufteilungs-Erkennung (Spezifikation 12, "Erkennung").

Ohne DB, ohne Dateizugriff testbar: Merkmale einer Seite aus ihrem Text
(``extract_features``), Boilerplate, Seitenzaehler, Referenzen, Anschrift,
Duplex und das Scoring der Seitenuebergaenge. ``split_detection_service``
ruft sie auf und re-exportiert sie.

Datenschutz: Referenz-, Vertrags-, Kundennummern, IBANs, USt-IDs und
Kopfzeilen werden nur als gekuerzter Hash (SHA-256 des normalisierten
Werts, 12 Hex-Zeichen) gespeichert — fuer "gleich/verschieden" reicht das,
im Klartext stehen sie nur im OCR-Text des Dokuments.
"""

import hashlib
import math
import re
from datetime import date

from app.services import split_weights as W

HASH_LEN = 12
_HEADER_SHARE = 0.25  # Kopfzeilen: obere 25 % der Zeilen ...
_HEADER_MAX_LINES = 12  # ... hoechstens 12
_TOP_SHARE = 0.35  # Anschrift, Hauptdatum: obere 35 % der Zeilen
# Anrede: obere 65 % — sie folgt auf Anschrift, Info-Block und Betreff
# (Beispiel-Stapel: Zeile 18 von 30).
_SALUTATION_SHARE = 0.65
_BOTTOM_SHARE = 0.15  # "x / y" ohne "Seite": nur untere 15 % der Zeilen
_MAX_DATES = 3

_COUNTER_RE = re.compile(r"\bSeite\s+(\d{1,3})(?:\s*(?:von|/)\s*(\d{1,3}))?", re.IGNORECASE)
_COUNTER_SLASH_RE = re.compile(r"(?<![\d./])(\d{1,3})\s*/\s*(\d{1,3})(?![\d./])")
_REF_LABEL_RE = re.compile(
    r"(?:(?:Kunden|Vertrags|Rechnungs|Versicherungs(?:schein)?|Policen|Mitglieds)[- ]?(?:Nr|Nummer)\b\.?"
    r"|Aktenzeichen|Steuernummer)\s*[:.#]?\s*",
    re.IGNORECASE,
)
_USTID_LABEL_RE = re.compile(r"USt\.?[- ]?Id(?:ent)?\.?[- ]?(?:Nr|Nummer)?\.?\s*[:.]?\s*", re.IGNORECASE)
_USTID_VALUE_RE = re.compile(r"^([A-Z]{2}\s?[0-9A-Z](?:\s?[0-9A-Z]){7,11})")
_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?)\b")
_VALUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9./-]+$")
_ZIP_LINE_RE = re.compile(r"^(?:D\s?-\s?)?\d{5}\s+[A-ZÄÖÜ][a-zäöüß]")
_SALUTATION_RE = re.compile(r"\b(?:Sehr\s+geehrte[rns]?|Guten\s+Tag|Hallo)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)")
_MARGIN_RE = re.compile(r"/([\d .]{1,6})/([\d .]{1,6})/")


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:HASH_LEN]


def sigmoid(score: float) -> float:
    if score >= 0:
        return 1.0 / (1.0 + math.exp(-score))
    z = math.exp(score)
    return z / (1.0 + z)


def text_lines(text: str | None) -> list[str]:
    """Nicht-leere Zeilen; Zeilen ohne zwei Buchstaben/Ziffern (OCR-Muell) fallen weg."""
    out = []
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if sum(ch.isalnum() for ch in line) >= 2:
            out.append(line)
    return out


def _top(lines: list[str], share: float) -> list[str]:
    return lines[: max(1, math.ceil(len(lines) * share))] if lines else []


def _bottom(lines: list[str], share: float) -> list[str]:
    return lines[min(len(lines) - 1, math.floor(len(lines) * (1 - share))):] if lines else []


# ---------------------------------------------------------------------------
# Merkmale einer Seite
# ---------------------------------------------------------------------------


def parse_counter(lines: list[str]) -> list[int | None] | None:
    """Seitenzaehler ``[x, y|None]``: "Seite x von y", "Seite x/y", "Seite x";
    "x / y" ohne "Seite" nur in den unteren 15 % der Zeilen. Angaben mit
    Gesamtzahl haben Vorrang."""
    found: list[tuple[int, int | None]] = []
    for line in lines:
        for m in _COUNTER_RE.finditer(line):
            found.append((int(m.group(1)), int(m.group(2)) if m.group(2) else None))
    for line in _bottom(lines, _BOTTOM_SHARE):
        if "seite" in line.lower():
            continue  # schon oben erfasst
        for m in _COUNTER_SLASH_RE.finditer(line):
            found.append((int(m.group(1)), int(m.group(2))))
    valid = [(x, y) for x, y in found if x >= 1 and (y is None or x <= y)]
    if not valid:
        return None
    with_total = [c for c in valid if c[1] is not None]
    x, y = (with_total or valid)[0]
    return [x, y]


def _normalize_ref(value: str) -> str:
    return re.sub(r"[\s./-]", "", value).upper()


def _ref_value(rest: str) -> str | None:
    """Wert hinter einem Referenz-Label: Tokens aus Ziffern/Grossbuchstaben,
    bis zum ersten normalen Wort."""
    tokens = []
    for token in rest.split():
        token = token.strip(",;:()")
        if not token or not _VALUE_TOKEN_RE.match(token):
            break
        if re.search(r"[a-z]{2}", token):
            break  # normales Wort ("vom", "bitte")
        tokens.append(token)
        if len(tokens) >= 5:
            break
    value = _normalize_ref("".join(tokens))
    if len(value) < 4 or sum(ch.isdigit() for ch in value) < 2:
        return None
    return value


def _iban_ok(iban: str) -> bool:
    if not 15 <= len(iban) <= 34:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


def parse_refs(lines: list[str]) -> tuple[list[str], list[str]]:
    """(Referenzen, Absender-IDs) als Hashes.

    Referenzen: Kunden-/Vertrags-/Rechnungs-/Versicherungs-/Policen-/
    Mitgliedsnummer, Aktenzeichen, Steuernummer — kennzeichnen den Vorgang.
    Absender-IDs: IBAN (Pruefziffer gueltig) und USt-ID — kennzeichnen den
    Absender, nicht den Vorgang, und bilden deshalb ein eigenes Signal.
    """
    refs: set[str] = set()
    sender: set[str] = set()
    for line in lines:
        for m in _REF_LABEL_RE.finditer(line):
            value = _ref_value(line[m.end():])
            if value:
                refs.add(short_hash(value))
        for m in _USTID_LABEL_RE.finditer(line):
            v = _USTID_VALUE_RE.match(line[m.end():].upper())
            if v:
                sender.add(short_hash("UST" + _normalize_ref(v.group(1))))
        for m in _IBAN_RE.finditer(line.upper()):
            iban = _normalize_ref(m.group(1))
            if _iban_ok(iban):
                sender.add(short_hash("IBAN" + iban))
    return sorted(refs), sorted(sender)


def detect_address_block(lines: list[str]) -> tuple[bool, bool]:
    """(PLZ+Ort-Zeile in den oberen 35 %, Anrede in den oberen 65 % der Zeilen)."""
    addr = any(_ZIP_LINE_RE.match(line) for line in _top(lines, _TOP_SHARE))
    salut = any(_SALUTATION_RE.search(line) for line in _top(lines, _SALUTATION_SHARE))
    return addr, salut


def _parse_dates(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        for m in _DATE_RE.finditer(line):
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if year < 100:
                year += 2000 if year < 70 else 1900
            try:
                iso = date(year, month, day).isoformat()
            except ValueError:
                continue
            if iso not in out:
                out.append(iso)
            if len(out) >= _MAX_DATES:
                return out
    return out


def normalize_header_line(line: str) -> str | None:
    """Kleinbuchstaben, Ziffern -> '#', nur Buchstaben/'#'; None bei < 4 Buchstaben."""
    norm = re.sub(r"\d", "#", line.lower())
    norm = re.sub(r"[^a-zäöüß# ]+", " ", norm)
    norm = " ".join(norm.split())
    if sum(ch.isalpha() for ch in norm) < 4:
        return None
    return norm


def header_hashes(lines: list[str]) -> list[str]:
    region = _top(lines, _HEADER_SHARE)[:_HEADER_MAX_LINES]
    out: list[str] = []
    for line in region:
        norm = normalize_header_line(line)
        if norm:
            h = short_hash(norm)
            if h not in out:
                out.append(h)
    return out


def parse_margin_code(text: str | None) -> list[int] | None:
    """Senkrechter Druckerei-Code ``<job-id>/<i>/<n>/`` aus dem Randstreifen:
    ``[i, n]`` (Seite i der Sendung mit n Seiten)."""
    for m in _MARGIN_RE.finditer(text or ""):
        a = re.sub(r"\D", "", m.group(1))
        b = re.sub(r"\D", "", m.group(2))
        if not a or not b or len(a) > 3 or len(b) > 3:
            continue
        i, n = int(a), int(b)
        if 1 <= i <= n:
            return [i, n]
    return None


def extract_features(text: str | None, margin_text: str | None = None) -> dict:
    """Merkmale (``sig``) einer Seite aus ihrem Inhaltstext — kein Volltext."""
    lines = text_lines(text)
    refs, sender = parse_refs(lines)
    addr, salut = detect_address_block(lines)
    return {
        "counter": parse_counter(lines),
        "refs": refs,
        "ibans": sender,
        "addr": addr,
        "salut": salut,
        "dates": _parse_dates(_top(lines, _TOP_SHARE)),
        "header": header_hashes(lines),
        "margin": parse_margin_code(margin_text),
    }


# ---------------------------------------------------------------------------
# Ueber alle Seiten
# ---------------------------------------------------------------------------


def _sig(page: dict) -> dict:
    return page.get("sig") or {}


def boilerplate_lines(pages: list[dict]) -> set[str]:
    """Kopfzeilen (Hashes), die auf >= 30 % der Textseiten (mindestens 2)
    vorkommen: wiederholter Briefkopf — zaehlt beim Vergleich nicht."""
    text_pages = [set(_sig(p).get("header") or []) for p in pages if not p.get("blank")]
    text_pages = [h for h in text_pages if h]
    if not text_pages:
        return set()
    counts: dict[str, int] = {}
    for headers in text_pages:
        for h in headers:
            counts[h] = counts.get(h, 0) + 1
    needed = max(W.BOILERPLATE_MIN_PAGES, math.ceil(len(text_pages) * W.BOILERPLATE_SHARE))
    return {h for h, c in counts.items() if c >= needed}


def detect_duplex(pages: list[dict]) -> tuple[bool, float]:
    """Duplex-Scan am Einzug erkennen: Vorder- und Rueckseite eines Blatts
    sind gleich hoch (<= 1,0 pt), zwischen Blaettern schwankt die Hoehe.

    Returns: (duplex, conf) — conf = Anteil passender Paare (1/2, 3/4 ...),
    0, wenn die Blattwechsel die Hoehe nicht erkennbar aendern.
    """
    ordered = sorted(pages, key=lambda p: p["n"])
    pairs = [(ordered[i], ordered[i + 1]) for i in range(0, len(ordered) - 1, 2)]
    if len(pairs) < 2:
        return False, 0.0
    tol = W.DUPLEX_PAIR_TOLERANCE_PT
    matching = sum(
        1 for a, b in pairs
        if abs(a.get("h", 0) - b.get("h", 0)) <= tol and abs(a.get("w", 0) - b.get("w", 0)) <= tol
    )
    changes = [
        abs(ordered[i].get("h", 0) - ordered[i + 1].get("h", 0)) > tol
        for i in range(1, len(ordered) - 1, 2)
    ]
    if not changes or sum(changes) / len(changes) < W.DUPLEX_MIN_SHEET_CHANGES:
        return False, 0.0
    conf = round(matching / len(pairs), 3)
    return conf >= W.DUPLEX_MIN_CONF, conf


def _landscape(page: dict) -> bool:
    w, h = page.get("w", 0), page.get("h", 0)
    if int(page.get("rot") or 0) % 180 == 90:
        w, h = h, w
    return w > h


def decision_for(p: float) -> str:
    """"cut" ab P_CUT, "none" bis P_NO_CUT, dazwischen "uncertain"."""
    if p >= W.P_CUT:
        return "cut"
    if p <= W.P_NO_CUT:
        return "none"
    return "uncertain"


def _counter_continues(ca, cb, blanks: int) -> bool:
    if not ca or not cb:
        return False
    if cb[0] - ca[0] not in (1, 1 + blanks):
        return False
    return ca[1] is None or cb[1] is None or ca[1] == cb[1]


def _content_terms(a: dict, b: dict, blanks: int, boiler: set[str]) -> list[tuple[float, str, bool]]:
    """Signale zwischen zwei Nicht-Leerseiten a und b (Leerseiten dazwischen
    uebersprungen). Returns: [(Beitrag, Grund, ist_Zaehler_Term)]."""
    sa, sb = _sig(a), _sig(b)
    ca, cb = sa.get("counter"), sb.get("counter")
    terms: list[tuple[float, str, bool]] = []

    if cb and cb[0] == 1:
        terms.append((W.COUNTER_RESTART, "Seitenzähler beginnt neu: Seite 1", True))
    if ca and ca[1] and ca[0] == ca[1]:
        terms.append((W.COUNTER_LAST, f"Vorherige Seite ist die letzte (Seite {ca[0]} von {ca[1]})", True))
    if _counter_continues(ca, cb, blanks):
        terms.append((W.COUNTER_CONTINUES, f"Seitenzähler läuft weiter (Seite {ca[0]} → {cb[0]})", True))

    if sb.get("addr") and sb.get("salut"):
        terms.append((W.ADDRESS_SALUTATION, f"Anschrift und Anrede auf Seite {b['n']}", False))
    elif sb.get("addr"):
        terms.append((W.ADDRESS_ONLY, f"Anschrift auf Seite {b['n']}", False))

    ra, rb = set(sa.get("refs") or []), set(sb.get("refs") or [])
    if ra and rb:
        if ra & rb:
            terms.append((W.SAME_REFERENCE, "Gleiche Referenznummer wie auf der vorherigen Seite", False))
        else:
            terms.append((W.DIFFERENT_REFERENCES, "Andere Referenznummern als auf der vorherigen Seite", False))

    ia, ib = set(sa.get("ibans") or []), set(sb.get("ibans") or [])
    if ia and ib and not ia & ib:
        terms.append((W.SENDER_ID_CHANGES, "Bankverbindung oder USt-ID wechselt", False))

    ha = set(sa.get("header") or []) - boiler
    hb = set(sb.get("header") or []) - boiler
    if ha and hb:
        jaccard = len(ha & hb) / len(ha | hb)
        if jaccard >= W.HEADER_SIMILAR_MIN:
            terms.append((W.HEADER_SIMILAR, "Ähnliche Kopfzeilen", False))
        elif jaccard == 0 and not ca and not cb:
            terms.append((W.HEADER_DISJOINT_NO_COUNTER, "Keine gemeinsamen Kopfzeilen", False))

    da, db = (sa.get("dates") or [None])[0], (sb.get("dates") or [None])[0]
    if da and db:
        try:
            gap = abs((date.fromisoformat(da) - date.fromisoformat(db)).days)
        except ValueError:
            gap = 0
        if gap > W.DATE_GAP_DAYS:
            terms.append((W.DATE_GAP, "Datum liegt mehr als einen Monat auseinander", False))

    if abs(a.get("w", 0) - b.get("w", 0)) > W.FORMAT_TOLERANCE_PT or _landscape(a) != _landscape(b):
        terms.append((W.FORMAT_CHANGE, "Seitenformat wechselt", False))

    ma, mb = sa.get("margin"), sb.get("margin")
    if ma and mb and ma[1] != mb[1]:
        terms.append((W.MARGIN_CODE_CHANGES, "Anderer Sendungscode im Rand", False))
    return terms


def score_boundaries(
    pages: list[dict],
    *,
    duplex_conf: float = 0.0,
    partial_from: int | None = None,
) -> tuple[list[dict], dict[int, str]]:
    """Bewertet jeden Uebergang i | i+1.

    Leerseiten werden uebersprungen: verglichen wird die letzte Nicht-Leerseite
    davor mit der ersten danach. Alle Uebergaenge einer solchen Luecke tragen
    dieselben Inhaltssignale; hoechstens EINER bekommt "Schnitt"/"unsicher"
    (der mit dem hoechsten Score) — sonst entstuende ein Teil nur aus
    Leerseiten. Ab ``partial_from`` (ohne Texterkennung) wird nie geschnitten.

    ``inside_counter`` ist ``[x, y]`` (Zaehler der Folgeseite), wenn der
    Seitenzaehler ueber den Uebergang weiterlaeuft — Kandidat fuer den KI-Pass
    (Owner-Entscheidung 4), sonst None.

    Returns: (Uebergaenge ``{after_page, score, score_without_counter, p,
    decision, reasons, llm, inside_counter}``, Art der Leerseiten
    ``{n: "back"|"separator"}``).
    """
    ordered = sorted(pages, key=lambda p: p["n"])
    boiler = boilerplate_lines(ordered)
    duplex = duplex_conf >= W.DUPLEX_MIN_CONF
    blank_kinds: dict[int, str] = {}
    out: list[dict] = []

    for idx in range(len(ordered) - 1):
        cur, nxt = ordered[idx], ordered[idx + 1]
        i = cur["n"]
        if partial_from is not None and nxt["n"] >= partial_from:
            out.append({
                "after_page": i, "score": None, "score_without_counter": None, "p": None,
                "decision": "none", "reasons": [f"Ab Seite {partial_from} ohne Texterkennung"], "llm": False,
                "inside_counter": None,
            })
            continue
        a = next((p for p in reversed(ordered[: idx + 1]) if not p.get("blank")), None)
        b = next((p for p in ordered[idx + 1:] if not p.get("blank")), None)
        blanks = (b["n"] if b else ordered[-1]["n"] + 1) - (a["n"] if a else 0) - 1

        terms: list[tuple[float, str, bool]] = []
        inside_counter = None
        if a is not None and b is not None:
            terms.extend(_content_terms(a, b, blanks, boiler))
            ca, cb = _sig(a).get("counter"), _sig(b).get("counter")
            if _counter_continues(ca, cb, blanks):
                inside_counter = list(cb)
        if nxt.get("blank") and a is not None and b is not None:
            sa, sb = _sig(a), _sig(b)
            cb = sb.get("counter")
            if _counter_continues(sa.get("counter"), cb, blanks):
                terms.append((W.BLANK_BACK, "Leere Rückseite – der Seitenzähler läuft über sie weiter", False))
                kind = "back"
            elif sb.get("addr") or (cb and cb[0] == 1):
                terms.append((W.BLANK_SEPARATOR, "Leerseite als Trennblatt?", False))
                kind = "separator"
            else:
                kind = None
            if kind:
                for p in ordered[idx + 1:]:
                    if not p.get("blank"):
                        break
                    blank_kinds[p["n"]] = kind
        if duplex and i % 2 == 1:
            terms.append((W.DUPLEX_SPLITS_SHEET, "Würde Vorder- und Rückseite eines Blatts trennen", False))

        score = W.BASE + sum(t[0] for t in terms)
        without_counter = W.BASE + sum(t[0] for t in terms if not t[2])
        p = sigmoid(score)
        boundary = {
            "after_page": i,
            "score": round(score, 2),
            "score_without_counter": round(without_counter, 2),
            "p": round(p, 4),
            "decision": decision_for(p),
            "reasons": [t[1] for t in terms],
            "llm": False,
            "inside_counter": inside_counter,
        }
        out.append(boundary)

    resolve_blank_gaps(out, ordered)
    return out, blank_kinds


def blank_gaps(pages: list[dict]) -> list[list[int]]:
    """Gruppen von Uebergaengen (``after_page``) rund um Leerseiten: alle
    Uebergaenge zwischen derselben Nicht-Leerseite davor und danach."""
    ordered = sorted(pages, key=lambda p: p["n"])
    groups: dict[tuple[int, int], list[int]] = {}
    for idx in range(len(ordered) - 1):
        a = next((p for p in reversed(ordered[: idx + 1]) if not p.get("blank")), None)
        b = next((p for p in ordered[idx + 1:] if not p.get("blank")), None)
        if a is None or b is None or b["n"] - a["n"] <= 1:
            continue
        groups.setdefault((a["n"], b["n"]), []).append(ordered[idx]["n"])
    return [group for group in groups.values() if len(group) > 1]


def resolve_blank_gaps(boundaries: list[dict], pages: list[dict]) -> None:
    """Hoechstens EIN Uebergang je Leerseiten-Luecke bleibt "Schnitt"/"unsicher"
    (der mit dem hoechsten Score) — sonst entstuende ein Teil nur aus
    Leerseiten. Aendert ``boundaries`` in place (auch nach dem KI-Pass)."""
    by_page = {bd["after_page"]: bd for bd in boundaries}
    for group in blank_gaps(pages):
        flagged = [by_page[n] for n in group if n in by_page and by_page[n]["decision"] != "none"]
        if len(flagged) <= 1:
            continue
        keep = max(flagged, key=lambda bd: (bd["decision"] == "cut", bd["score"] or 0.0))
        for bd in flagged:
            if bd is not keep:
                bd["decision"] = "none"
                bd["reasons"] = [*bd["reasons"], f"Schnitt nach Seite {keep['after_page']} vorgeschlagen"]
