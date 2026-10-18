"""Optionaler KI-Pass der Aufteilungs-Erkennung (Spezifikation 12, Etappe 3).

Laeuft im Queue-Worker nach dem heuristischen Scoring, **hoechstens ein
LLM-Aufruf pro Stapel** und nur, wenn es etwas zu klaeren gibt:

- unsichere Uebergaenge (``decision == "uncertain"``),
- "Dokument aufteilen"/Upload-Option (FORCE) ohne einen einzigen Schnitt:
  dann die knappsten Uebergaenge,
- Uebergaenge innerhalb eines fortlaufenden Seitenzaehlers, deren uebrige
  Merkmale *ohne* den Zaehler-Term einen Wechsel zeigen
  (``score_without_counter >= 0``, Owner-Entscheidung 4).

Hoechstens ``MAX_CANDIDATES`` Uebergaenge (sortiert nach ``|p - 0,5|``), je
Uebergang das Ende von Seite i und der Anfang von Seite i+1 ohne Boilerplate,
zusammen hoechstens ``MAX_INPUT_CHARS`` Zeichen. Die Ausschnitte werden
zwischen ``<page_excerpts>`` gewrappt und vorher mit dem gemeinsamen
Delimiter-Sanitizer entschaerft (Prompt-Injection-Schutz wie RAG).

Verrechnung: ``score += LLM_WEIGHT * confidence * (+1 | -1)``, danach neu
klassifiziert. Zaehler-interne Kandidaten werden nie "Schnitt": sagt das LLM
``new_document=true`` mit ``confidence >= INSIDE_COUNTER_MIN_CONF``, wird der
Uebergang nur auf "unsicher" gehoben — der Benutzer entscheidet im Editor.

Graceful Degradation: Ollama nicht erreichbar, Timeout oder ungueltiges JSON
-> das heuristische Ergebnis bleibt, Hinweis "KI-Pruefung nicht verfuegbar".
Der Job schlaegt deswegen nie fehl.
"""

import json
import logging
from dataclasses import dataclass

from app.config import Settings
from app.core.prompt_safety import neutralize_prompt_tags
from app.services.analysis_service import analysis_slot
from app.services.llm_service import call_llm, fill_prompt, load_prompt_template
from app.services.split_heuristics import (
    boilerplate_lines,
    decision_for,
    normalize_header_line,
    resolve_blank_gaps,
    short_hash,
    sigmoid,
    text_lines,
)

logger = logging.getLogger(__name__)

# Wird im detector_version-Feld angehaengt ("h1+llm1"), sobald eine KI-Antwort
# verrechnet wurde. Beim Aendern von Prompt, Gewicht oder Auswahl hochzaehlen.
LLM_VERSION = "llm1"
PROMPT_FILE = "split_boundaries.txt"
MAX_CANDIDATES = 12
TAIL_CHARS = 250  # Ende von Seite i
HEAD_CHARS = 350  # Anfang von Seite i+1
MAX_INPUT_CHARS = 6000
LLM_WEIGHT = 2.5
INSIDE_COUNTER_MIN_CONF = 0.6
_REASON_MAX = 120
_PROMPT_TAG = "page_excerpts"
UNAVAILABLE = "KI-Prüfung nicht verfügbar"

RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["boundaries"],
    "properties": {
        "boundaries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["after_page", "new_document", "confidence", "reason"],
                "properties": {
                    "after_page": {"type": "integer"},
                    "new_document": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}


@dataclass(frozen=True)
class Candidate:
    after_page: int
    inside_counter: bool
    before: str
    after: str


def _without_boilerplate(text: str | None, boiler: set[str]) -> str:
    """Zeilen des Seitentexts ohne wiederholten Briefkopf/Fuss (Boilerplate)."""
    out = []
    for line in text_lines(text):
        norm = normalize_header_line(line)
        if norm and short_hash(norm) in boiler:
            continue
        out.append(line)
    return "\n".join(out)


def _neighbor_text(pages: list[dict], texts: dict[int, str], start: int, step: int) -> str:
    """Text der naechsten Nicht-Leerseite ab ``start`` (step -1: rueckwaerts)."""
    by_n = {p["n"]: p for p in pages}
    n = start
    while n in by_n:
        if not by_n[n].get("blank") and texts.get(n, "").strip():
            return texts[n]
        n += step
    return ""


def _priority(boundary: dict, inside: bool) -> float:
    """Je naeher an p = 0,5, desto wichtiger. Zaehler-interne Kandidaten werden
    nach dem Teil-Score ohne Zaehler eingeordnet (ihr p ist durch den Zaehler
    sehr klein)."""
    score = boundary.get("score_without_counter") if inside else boundary.get("score")
    return abs(sigmoid(score or 0.0) - 0.5)


def select_candidates(boundaries: list[dict], *, forced: bool) -> list[tuple[dict, bool]]:
    """Uebergaenge fuer den KI-Pass: ``[(boundary, zaehler_intern)]``, hoechstens
    MAX_CANDIDATES. Leer -> kein Aufruf. Uebergaenge ohne Texterkennung
    (``score is None``, Zeitbudget) kommen nie in Frage."""
    scored = [b for b in boundaries if b.get("score") is not None]
    regular = [b for b in scored if b.get("decision") == "uncertain"]
    if forced and not any(b.get("decision") == "cut" for b in scored):
        regular = [b for b in scored if b.get("decision") != "cut"]
    regular_pages = {b["after_page"] for b in regular}
    inside = [
        b for b in scored
        if b.get("decision") == "none"
        and b.get("inside_counter")
        and (b.get("score_without_counter") is not None and b["score_without_counter"] >= 0)
        and b["after_page"] not in regular_pages
    ]
    ranked = [(b, False) for b in regular] + [(b, True) for b in inside]
    ranked.sort(key=lambda item: (_priority(item[0], item[1]), item[0]["after_page"]))
    return ranked[:MAX_CANDIDATES]


def build_candidates(
    selected: list[tuple[dict, bool]], pages: list[dict], texts: dict[int, str],
) -> list[Candidate]:
    """Textausschnitte je Uebergang; zusammen hoechstens MAX_INPUT_CHARS Zeichen.
    Uebergaenge, zu denen auf beiden Seiten kein Text vorliegt, entfallen."""
    if not selected:
        return []
    boiler = boilerplate_lines(pages)
    budget = min(TAIL_CHARS + HEAD_CHARS, MAX_INPUT_CHARS // len(selected))
    tail = max(1, round(budget * TAIL_CHARS / (TAIL_CHARS + HEAD_CHARS)))
    head = max(1, budget - tail)
    out = []
    for boundary, inside in selected:
        i = boundary["after_page"]
        before = _without_boilerplate(_neighbor_text(pages, texts, i, -1), boiler)[-tail:]
        after = _without_boilerplate(_neighbor_text(pages, texts, i + 1, 1), boiler)[:head]
        if not before.strip() and not after.strip():
            continue
        out.append(Candidate(after_page=i, inside_counter=inside, before=before.strip(), after=after.strip()))
    return out


def render_prompt(candidates: list[Candidate], page_count: int, known_cuts: list[int]) -> str:
    blocks = []
    for c in sorted(candidates, key=lambda c: c.after_page):
        blocks.append(
            f"### Uebergang after_page={c.after_page} (Seite {c.after_page} -> Seite {c.after_page + 1})\n"
            f"[Ende von Seite {c.after_page}]\n{neutralize_prompt_tags(c.before, _PROMPT_TAG) or '(kein Text)'}\n"
            f"[Anfang von Seite {c.after_page + 1}]\n{neutralize_prompt_tags(c.after, _PROMPT_TAG) or '(kein Text)'}"
        )
    return fill_prompt(load_prompt_template(PROMPT_FILE), {
        "page_count": str(page_count),
        "known_cuts": ", ".join(f"nach Seite {n}" for n in known_cuts) or "keine",
        "excerpts": "\n\n".join(blocks),
    })


def _clean_reason(value) -> str:
    """KI-Begruendung fuer den Tooltip: eine Zeile, gekuerzt (untrusted Text,
    die Oberflaeche zeigt ihn nur als Text an)."""
    text = " ".join(str(value or "").split())
    return text[: _REASON_MAX - 1] + "…" if len(text) > _REASON_MAX else text


def _counter_label(counter) -> str:
    x, y = (list(counter) + [None, None])[:2]
    return f"Seite {x}/{y}" if y else f"Seite {x}"


def parse_answer(raw: str | None) -> list[dict] | None:
    """Antwort -> Liste der Bewertungen; None bei leerer/ungueltiger Antwort."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    items = data.get("boundaries") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        page = item.get("after_page")
        new_doc = item.get("new_document")
        try:
            conf = float(item.get("confidence"))
        except (TypeError, ValueError):
            continue
        if isinstance(page, bool) or not isinstance(page, int) or not isinstance(new_doc, bool):
            continue
        if conf != conf:  # NaN
            continue
        out.append({
            "after_page": page,
            "new_document": new_doc,
            "confidence": min(1.0, max(0.0, conf)),
            "reason": _clean_reason(item.get("reason")),
        })
    return out


def apply_answer(
    boundaries: list[dict], candidates: list[Candidate], answer: list[dict], pages: list[dict],
) -> int:
    """Verrechnet die KI-Bewertungen mit den Uebergaengen (in place).
    Bewertungen fuer nicht angefragte Uebergaenge und Doppelte werden
    ignoriert. Returns: Anzahl verrechneter Bewertungen."""
    by_page = {b["after_page"]: b for b in boundaries}
    wanted = {c.after_page: c for c in candidates}
    seen: set[int] = set()
    applied = 0
    for item in answer:
        page = item["after_page"]
        candidate = wanted.get(page)
        if candidate is None or page in seen or page not in by_page:
            continue
        seen.add(page)
        boundary = by_page[page]
        conf, new_doc, reason = item["confidence"], item["new_document"], item["reason"]
        percent = round(conf * 100)
        verdict = f"KI: {'neues Dokument' if new_doc else 'gleiches Dokument'} ({percent} %)"
        notes = [f"{verdict} – {reason}" if reason else verdict]
        boundary["llm"] = True
        applied += 1
        if candidate.inside_counter:
            # Owner-Entscheidung 4: innerhalb eines fortlaufenden Zaehlers nie
            # "Schnitt", hoechstens "unsicher".
            if new_doc and conf >= INSIDE_COUNTER_MIN_CONF and boundary["decision"] == "none":
                boundary["decision"] = "uncertain"
                label = _counter_label(boundary.get("inside_counter") or [page + 1, None])
                notes.insert(0, f"Neues Dokument innerhalb von {label}? – bitte prüfen")
            boundary["reasons"] = [*boundary.get("reasons", []), *notes]
            continue
        score = (boundary.get("score") or 0.0) + LLM_WEIGHT * conf * (1 if new_doc else -1)
        p = sigmoid(score)
        boundary["score"] = round(score, 2)
        boundary["p"] = round(p, 4)
        decision = decision_for(p)
        if decision == "cut" and boundary.get("inside_counter"):
            # Owner-Entscheidung 4 gilt auch fuer regulaere (heuristisch schon
            # unsichere bzw. FORCE-) Kandidaten innerhalb eines Zaehlers.
            decision = "uncertain"
        boundary["decision"] = decision
        boundary["reasons"] = [*boundary.get("reasons", []), *notes]
    resolve_blank_gaps(boundaries, pages)
    return applied


async def llm_refine(
    proposal: dict,
    pages: list[dict],
    page_ocr: list[dict],
    settings: Settings,
    *,
    forced: bool,
) -> bool:
    """KI-Pass ueber ``proposal["boundaries"]`` (in place).

    Returns: True, wenn eine KI-Antwort verrechnet wurde. Ohne Kandidaten
    kein Aufruf (False). Fehler/ungueltige Antwort: ``proposal["llm_unavailable"]
    = True`` und Hinweis an den Kandidaten — nie eine Exception.
    """
    boundaries = proposal.get("boundaries") or []
    selected = select_candidates(boundaries, forced=forced)
    texts = {
        int(e.get("page")): str(e.get("text") or "")
        for e in page_ocr if isinstance(e, dict) and e.get("page") is not None
    }
    candidates = build_candidates(selected, pages, texts)
    if not candidates:
        return False
    known_cuts = sorted(b["after_page"] for b in boundaries if b.get("decision") == "cut")
    raw = None
    try:
        prompt = render_prompt(candidates, len(pages), known_cuts)
        # Analyse-Slot (ANALYSIS_CONCURRENCY=1): nie parallel zu einer Analyse
        # oder Re-Analyse — ein Modell, eine Anfrage gleichzeitig.
        async with analysis_slot():
            raw = await call_llm(prompt, settings, schema=RESPONSE_SCHEMA)
        answer = parse_answer(raw)
    except Exception:
        logger.warning("KI-Pass der Aufteilung fehlgeschlagen", exc_info=True)
        answer = None
    if answer is None:
        logger.warning("KI-Pass der Aufteilung: keine verwertbare Antwort — heuristisches Ergebnis bleibt")
        proposal["llm_unavailable"] = True
        by_page = {b["after_page"]: b for b in boundaries}
        for candidate in candidates:
            boundary = by_page.get(candidate.after_page)
            if boundary is not None and UNAVAILABLE not in boundary.get("reasons", []):
                boundary["reasons"] = [*boundary.get("reasons", []), UNAVAILABLE]
        return False
    applied = apply_answer(boundaries, candidates, answer, pages)
    proposal["llm_unavailable"] = False
    logger.info(
        "KI-Pass der Aufteilung: %d Uebergaenge angefragt, %d bewertet", len(candidates), applied,
    )
    return applied > 0

