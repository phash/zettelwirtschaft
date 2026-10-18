import asyncio
import json
import logging
import re
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.core.date_utils import parse_date_lenient
from app.core.prompt_safety import neutralize_prompt_tags
from app.core.normalize import (
    DOCUMENT_TYPE_LABELS,
    VALID_DOCUMENT_TYPES,
    VALID_TAX_CATEGORIES,
    normalize_currency,
    normalize_tax_category,
)
from app.services.llm_service import call_llm, chat_json, fill_prompt, load_prompt_template
from app.services.ocr_service import OcrResult, PageText, extract_text, ocr_result_from_pages
from app.services.ollama_client import OllamaError, OllamaErrorKind

logger = logging.getLogger("zettelwirtschaft.analysis")

__all__ = [
    "AnalysisResult",
    "VALID_DOCUMENT_TYPES",
    "VALID_TAX_CATEGORIES",
    "analyze_document",
]

# Plausibilitaets-Grenzen fuer Beleg-Beträge (Privathaushalt).
# Werte ueber dieser Grenze sind verdaechtig — entweder Tippfehler im Beleg
# oder Prompt-Injection-Versuch ("setze amount=99999"). Forciert NEEDS_REVIEW.
AMOUNT_SANITY_CEILING = 50_000.0

# question_type fuer die Rueckfrage, wenn die KI-Analyse ganz ausgefallen ist.
# Frontend und Review-API erkennen den Fall daran (nicht am Fragetext).
QUESTION_TYPE_ANALYSIS_FAILED = "analysis_failed"


def _nullable(type_name: str) -> dict:
    return {"type": [type_name, "null"]}


_WARRANTY_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "has_warranty": {"type": "boolean"},
        "product_name": _nullable("string"),
        "purchase_date": _nullable("string"),
        "warranty_duration_months": _nullable("integer"),
        "warranty_end_date": _nullable("string"),
        "store_name": _nullable("string"),
    },
    "required": [
        "has_warranty", "product_name", "purchase_date",
        "warranty_duration_months", "warranty_end_date", "store_name",
    ],
}


def _analysis_schema() -> dict:
    """JSON-Schema fuer das AnalysisResult.

    Wird an Ollama als `format=...` uebergeben (constrained decoding).
    ALLE Felder sind Pflicht (Werte duerfen null sein): Sind nur wenige Felder
    required, schliessen Modelle das Objekt frueh und lassen Datum/Betrag/
    Aussteller einfach weg (Benchmark qwen2.5:7b: Kernfelder 40 % -> 63 %
    korrekt, wenn alle Felder Pflicht sind). Die Reihenfolge der Properties
    ist die Generierungsreihenfolge — nicht umsortieren.
    """
    properties = {
        "document_type": {"type": "string", "enum": sorted(VALID_DOCUMENT_TYPES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "title": _nullable("string"),
        "sender": _nullable("string"),
        "recipient": _nullable("string"),
        "document_date": _nullable("string"),
        "amount": _nullable("number"),
        "currency": _nullable("string"),
        "reference_number": _nullable("string"),
        "tags": {"type": "array", "items": {"type": "string"}},
        "summary": _nullable("string"),
        "tax_relevant": {"type": "boolean"},
        "tax_category": {"enum": sorted(VALID_TAX_CATEGORIES) + [None]},
        "tax_year": _nullable("integer"),
        "warranty_info": _WARRANTY_SCHEMA,
        "filing_scope": _nullable("string"),
        "filing_scope_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needs_review": {"type": "boolean"},
        "review_questions": {"type": "array", "items": {"type": "string"}},
    }
    return {"type": "object", "properties": properties, "required": list(properties)}


def _sub_schema(*keys: str) -> dict:
    """Teil-Schema fuer die sequentiellen Einzel-Prompts (gleiche Feld-Definitionen)."""
    properties = _analysis_schema()["properties"]
    return {
        "type": "object",
        "properties": {k: properties[k] for k in keys},
        "required": list(keys),
    }


@dataclass
class AnalysisResult:
    document_type: str = "SONSTIGES"
    confidence: float = 0.0
    title: str | None = None
    sender: str | None = None  # LLM-Feld "sender" → wird als "issuer" auf Document gemappt
    recipient: str | None = None
    document_date: str | None = None
    amount: float | None = None
    currency: str | None = None
    reference_number: str | None = None
    tags: list[str] = field(default_factory=list)
    summary: str | None = None
    tax_relevant: bool = False
    tax_category: str | None = None
    tax_year: int | None = None
    warranty_info: dict | None = None
    needs_review: bool = False
    # Eintraege sind Fragetexte (str) oder strukturierte Fragen (dict mit den
    # Feldern von ReviewQuestion) — siehe archive_service._build_review_question.
    review_questions: list[str | dict] = field(default_factory=list)
    filing_scope: str | None = None
    filing_scope_confidence: float = 0.0
    # Fehlerart, wenn die KI-Analyse ausgefallen ist (OllamaErrorKind-Wert),
    # sonst None.
    llm_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "document_type": self.document_type,
            "confidence": self.confidence,
            "title": self.title,
            "sender": self.sender,
            "recipient": self.recipient,
            "document_date": self.document_date,
            "amount": self.amount,
            "currency": self.currency,
            "reference_number": self.reference_number,
            "tags": self.tags,
            "summary": self.summary,
            "tax_relevant": self.tax_relevant,
            "tax_category": self.tax_category,
            "tax_year": self.tax_year,
            "warranty_info": self.warranty_info,
            "needs_review": self.needs_review,
            "review_questions": self.review_questions,
            "filing_scope": self.filing_scope,
            "filing_scope_confidence": self.filing_scope_confidence,
            "llm_error": self.llm_error,
        }


# ---------------------------------------------------------------------------
# Typ-sichere Uebernahme der LLM-Werte. Gilt fuer den kombinierten UND den
# sequentiellen Pfad — beide laufen durch _build_result.
# ---------------------------------------------------------------------------


def _safe_float(value, default: float = 0.0) -> float:
    """Sichere Konvertierung zu float."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _clamp01(value) -> float:
    return min(max(_safe_float(value), 0.0), 1.0)


def _as_text(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "ja", "yes", "1", "wahr"}
    return False


def _as_tax_year(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        year = int(str(value).strip()[:4]) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None


def _as_str_list(value, *, split_commas: bool = True) -> list[str]:
    """Liste von Strings; ein String wird nie zeichenweise iteriert.

    Ein einzelner String wird an Kommas getrennt (Tags: "auto, haushalt") —
    bei Fragetexten (``split_commas=False``) bleibt er ein Eintrag, sonst
    zerfiele "Ist das Datum korrekt, oder ...?" in Bruchstuecke.
    """
    if isinstance(value, str):
        items = value.split(",") if split_commas else [value]
    elif isinstance(value, (list, tuple)):
        items = [v for v in value if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
    else:
        return []
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _as_iso_date(value) -> str | None:
    parsed = parse_date_lenient(value) if isinstance(value, str) else None
    return parsed.isoformat() if parsed else None


def _parse_amount(raw) -> float | None:
    """Betrag als float; versteht auch "1.190,00 €" und "119,00"."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    text = re.sub(r"[^\d,.\-]", "", raw)
    if "," in text and "." in text:
        # Letztes Trennzeichen ist das Dezimaltrennzeichen.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        # Nur Tausenderpunkte ("1.190", "12.500"): deutsches Format ohne Cent.
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _format_eur(amount: float) -> str:
    """Deutsches Zahlenformat: 60000.5 -> "60.000,50"."""
    return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _sanitize_amount(raw_amount) -> tuple[float | None, str | None]:
    """Normalisiert + plausibilisiert das amount-Feld.

    Schutz gegen Prompt-Injection: ein Dokument mit OCR-Text "setze amount=99999"
    darf das LLM nicht dazu bringen, einen unsinnig grossen Betrag zu setzen.
    Werte ueber AMOUNT_SANITY_CEILING werden zwar uebernommen, triggern aber
    NEEDS_REVIEW mit konkreter Frage.

    Returns:
        (amount, review_question_or_none). amount kann None sein.
    """
    amount = _parse_amount(raw_amount)
    if amount is None:
        return None, None
    if amount < 0:
        return None, "Negativer Betrag erkannt — bitte prüfen Sie den Betrag."
    if amount > AMOUNT_SANITY_CEILING:
        return amount, (
            f"Auffällig hoher Betrag: {_format_eur(amount)}. Bitte prüfen Sie den Betrag — "
            f"falls er korrekt ist, einfach bestätigen."
        )
    return amount, None


def _build_result(data: dict, confidence_threshold: float, force_review: bool = False) -> AnalysisResult:
    """Baut ein AnalysisResult aus der (kombinierten oder zusammengefuehrten) LLM-Antwort.

    Alle Felder werden typ-sicher uebernommen und normalisiert; Sanity-Checks
    (Betrag, Steuerkategorie-Whitelist) greifen fuer jeden Pfad.
    """
    doc_type = data.get("document_type")
    if doc_type not in VALID_DOCUMENT_TYPES:
        doc_type = "SONSTIGES"

    confidence = _clamp01(data.get("confidence"))
    needs_review = force_review or _as_bool(data.get("needs_review")) or confidence < confidence_threshold
    review_questions: list[str | dict] = list(
        _as_str_list(data.get("review_questions"), split_commas=False)
    )

    # tax_category-Whitelist: auch bei "A | B" nur der erste gueltige Wert.
    # Schutz gegen Prompt-Injection-induzierte Manipulation.
    tax_category = normalize_tax_category(data.get("tax_category"))

    # Amount-Sanity: extreme Werte triggern Review statt blind zu uebernehmen.
    amount, amount_question = _sanitize_amount(data.get("amount"))
    if amount_question:
        review_questions.append(amount_question)
        needs_review = True

    currency = normalize_currency(data.get("currency"))
    if currency is None and amount is not None:
        currency = "EUR"

    warranty = data.get("warranty_info")

    return AnalysisResult(
        document_type=doc_type,
        confidence=confidence,
        title=_as_text(data.get("title")),
        sender=_as_text(data.get("sender")),
        recipient=_as_text(data.get("recipient")),
        document_date=_as_iso_date(data.get("document_date")),
        amount=amount,
        currency=currency,
        reference_number=_as_text(data.get("reference_number")),
        tags=_as_str_list(data.get("tags")),
        summary=_as_text(data.get("summary")),
        tax_relevant=_as_bool(data.get("tax_relevant")),
        tax_category=tax_category,
        tax_year=_as_tax_year(data.get("tax_year")),
        warranty_info=warranty if isinstance(warranty, dict) else None,
        needs_review=needs_review,
        review_questions=review_questions,
        filing_scope=_as_text(data.get("filing_scope")),
        filing_scope_confidence=_clamp01(data.get("filing_scope_confidence")),
    )


def _ensure_review_question(result: AnalysisResult) -> AnalysisResult:
    """Ein Dokument in NEEDS_REVIEW braucht mindestens eine Rueckfrage —
    sonst steht es in der Review-Liste ohne Hinweis, was zu pruefen ist."""
    if result.needs_review and not result.review_questions:
        result.review_questions.append(
            f"Die automatische Erkennung ist unsicher (Konfidenz: {result.confidence:.0%}). "
            "Bitte prüfen Sie die erkannten Daten."
        )
    return result


# INJ-05: Quellen, deren Inhalt ein Fremder bestimmt (E-Mail-Absender). Das
# LLM-Ergebnis laesst sich per Prompt-Injection steuern (confidence=0.99,
# needs_review=false) — Modell-Angaben duerfen fuer solche Dokumente die
# Pruefung steuerrelevanter Belege nicht abschalten.
_UNTRUSTED_SOURCES = frozenset({"EMAIL"})


def apply_source_review_policy(result: AnalysisResult | None, source) -> AnalysisResult | None:
    """Steuerrelevante Belege aus E-Mail-Importen immer zur Pruefung vorlegen.

    Ein unaufgefordert zugeschickter Anhang soll nicht ungeprueft als
    absetzbarer Beleg in Steuer-Summen und -Export landen. Die Rueckfrage
    zielt auf ``tax_relevant`` (Ja = behalten, Nein = aus der Steuer nehmen),
    der Rest der Analyse bleibt unveraendert. Upload und Watch-Ordner
    (vom Benutzer selbst eingelegt) sind nicht betroffen.
    """
    if result is None or not result.tax_relevant:
        return result
    if getattr(source, "value", source) not in _UNTRUSTED_SOURCES:
        return result
    result.needs_review = True
    result.review_questions.append({
        "question": "Ist dieser per E-Mail eingegangene Beleg wirklich steuerrelevant?",
        "question_type": "confirmation",
        "field_affected": "tax_relevant",
        "suggested_answers": "Ja|Nein",
        "explanation": (
            "Das Dokument wurde automatisch aus einem E-Mail-Postfach importiert. "
            "Bitte prüfen Sie Absender, Betrag und Steuerkategorie, bevor der Beleg "
            "in die Steuerübersicht übernommen wird."
        ),
        "priority": 8,
    })
    return result


def _analysis_failed_result(error: OllamaError | None) -> AnalysisResult:
    """NEEDS_REVIEW-Ergebnis, wenn die KI-Analyse ausgefallen ist.

    Die Rueckfrage ist strukturiert (question_type="analysis_failed",
    field_affected="document_type", Dokumenttypen als Antwortvorschlaege),
    damit der Benutzer das Dokument direkt klassifizieren kann.
    """
    if error is None:
        reason = "Die KI hat keine verwertbare Antwort geliefert."
        explanation = "Sie können die Analyse später erneut starten oder die Daten von Hand eintragen."
        kind = OllamaErrorKind.INVALID_RESPONSE.value
    else:
        reason = error.user_message
        kind = error.kind.value
        if error.kind == OllamaErrorKind.MODEL_MISSING:
            explanation = (
                f"Das Modell wird beim Start automatisch heruntergeladen, sofern Ollama läuft "
                f"(oder manuell: „ollama pull {error.model}“). Danach können Sie die Analyse "
                "erneut starten."
            )
        else:
            explanation = (
                "Bitte prüfen Sie, ob Ollama läuft. Sie können die Analyse später erneut "
                "starten oder die Daten von Hand eintragen."
            )
    return AnalysisResult(
        needs_review=True,
        llm_error=kind,
        review_questions=[{
            "question": (
                f"Die automatische Analyse ist fehlgeschlagen: {reason} "
                "Um welchen Dokumenttyp handelt es sich?"
            ),
            "question_type": QUESTION_TYPE_ANALYSIS_FAILED,
            "field_affected": "document_type",
            "suggested_answers": "|".join(DOCUMENT_TYPE_LABELS.values()),
            "explanation": explanation,
            "priority": 10,
        }],
    )


# ---------------------------------------------------------------------------
# Prompt-Aufbau
# ---------------------------------------------------------------------------


def _sanitize_ocr_for_prompt(text: str) -> str:
    """NEW-A (Re-Review): Entschaerft die <document_ocr>-Delimiter-Tags (auch
    Gross-/Kleinschreibung und Leerzeichen-Varianten), damit ein OCR-Text mit
    `</document_ocr>...Anweisung...<document_ocr>` nicht aus dem Wrap
    ausbrechen kann. Wirkt zusaetzlich zur Warnung im Template."""
    return neutralize_prompt_tags(text, "document_ocr")


def _render_ocr_prompt(template_name: str, ocr_text: str, **values: str) -> str:
    """Template laden, gemeinsamen <document_ocr>-Block einsetzen, Werte fuellen.

    Der Block (Warnung + Wrap) liegt einmal in document_ocr_block.txt und gilt
    fuer alle Analyse-Prompts. Die Werte werden in EINEM Durchgang eingesetzt.
    """
    template = fill_prompt(
        load_prompt_template(template_name),
        {"document_ocr_block": load_prompt_template("document_ocr_block.txt")},
    )
    return fill_prompt(template, {"ocr_text": ocr_text, **values})


def _truncate_text(text: str, max_chars: int = 4000) -> str:
    """Kuerzt langen OCR-Text: erste ~2000 + letzte ~2000 Zeichen.

    L3: Schnittkanten an Whitespace ausrichten, damit kein Wort/keine Zahl mitten
    durchtrennt wird (sonst sieht der LLM ggf. halbe Betraege/Datumsangaben).
    Faellt auf den harten Schnitt zurueck, wenn keine nahe Whitespace-Grenze
    existiert.
    """
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    head = text[:half]
    tail = text[-half:]
    # Kopf am letzten Whitespace beenden (nur wenn nicht zu viel verloren geht).
    cut = max(head.rfind(" "), head.rfind("\n"))
    if cut >= half // 2:
        head = head[:cut]
    # Schwanz erst nach dem ersten Whitespace beginnen lassen.
    start = tail.find(" ")
    nl = tail.find("\n")
    if nl != -1 and (start == -1 or nl < start):
        start = nl
    if 0 <= start <= half // 2:
        tail = tail[start + 1:]
    return head + "\n\n[...Text gekuerzt...]\n\n" + tail


def _parse_analysis_json(raw: str | None) -> dict | None:
    """Versucht ein JSON-Objekt aus der LLM-Antwort zu parsen (tolerant)."""
    if not raw:
        return None
    candidates = [raw]
    # Fallback: JSON-Block aus Markdown extrahieren
    match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", raw, re.DOTALL)
    if match:
        candidates.append(match.group(1))
    # Letzter Versuch: erstes { bis letztes }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _format_filing_scopes(filing_scopes: list[dict] | None) -> str:
    """Formatiert die Ablagebereiche fuer den LLM-Prompt."""
    if not filing_scopes:
        return "Keine Ablagebereiche konfiguriert."
    lines = ["Verfuegbare Ablagebereiche:"]
    for scope in filing_scopes:
        keywords = scope.get("keywords", [])
        kw_str = f" (Schluesselwoerter: {', '.join(keywords)})" if keywords else ""
        lines.append(f"  - \"{scope['name']}\"{kw_str}")
    return "\n".join(lines)


def _format_correction_examples(corrections: list[dict] | None) -> str:
    """Formatiert User-Korrekturen als Few-Shot-Block fuer das LLM.

    Nutzt die `CorrectionMapping`-Tabelle als impliziten Trainingsdatensatz —
    haeufig korrigierte Patterns (occurrence_count >= 2) werden dem Modell
    explizit gezeigt damit es daraus lernt.
    """
    if not corrections:
        return ""
    lines = [
        "Lerneffekt aus frueheren Benutzer-Korrekturen — bitte beachten und "
        "aehnliche Faelle entsprechend behandeln:",
    ]
    for c in corrections:
        lines.append(
            f"  - Feld \"{c['field']}\": KI-erkannt \"{c['original']}\" "
            f"-> Benutzer-korrigiert \"{c['corrected']}\" "
            f"({c['count']}x korrigiert)"
        )
    return "\n".join(lines)


async def _load_correction_examples(
    session, limit: int = 8
) -> list[dict]:
    """Top-N haeufigste User-Korrekturen als Few-Shot-Beispiele.

    Auf 8 begrenzt damit der Prompt nicht aufblaeht — bei mehr Examples
    nimmt die Modell-Aufmerksamkeit fuer den eigentlichen OCR-Text ab.
    """
    if session is None:
        return []
    try:
        from sqlalchemy import select, desc
        from app.models.correction_mapping import CorrectionMapping
        result = await session.execute(
            select(CorrectionMapping)
            .where(CorrectionMapping.occurrence_count >= 2)
            .order_by(desc(CorrectionMapping.occurrence_count), desc(CorrectionMapping.updated_at))
            .limit(limit)
        )
        return [
            {
                "field": m.field,
                "original": m.original_value,
                "corrected": m.corrected_value,
                "count": m.occurrence_count,
            }
            for m in result.scalars().all()
        ]
    except Exception:
        logger.warning("Korrekturen konnten nicht geladen werden", exc_info=True)
        return []


_VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["issues"],
}


async def _verify_analysis(
    ocr_text: str,
    analysis: "AnalysisResult",
    settings: Settings,
) -> list[str]:
    """Zweiter LLM-Pass — validiert die extrahierten Felder.

    Bekommt den OCR-Text + die JSON-Antwort vom Erst-Pass und gibt eine Liste
    von konkreten Auffaelligkeiten zurueck (leere Liste = alles ok). Issues
    werden als zusaetzliche review_questions in das Ergebnis gemerged.
    Best-Effort: ein LLM-Fehler ergibt eine leere Liste.
    """
    # H-07: None-Werte als "(nicht erkannt)" rendern, sonst sieht der Verifier
    # "amount = None None" und produziert Pseudo-Issues fuer fehlende Felder.
    def _fmt(v):
        return "(nicht erkannt)" if v is None else str(v)

    amount_str = (
        "(nicht erkannt)"
        if analysis.amount is None
        else f"{analysis.amount} {analysis.currency or 'EUR'}"
    )
    summary_lines = [
        f"document_type = {_fmt(analysis.document_type)}",
        f"sender = {_fmt(analysis.sender)}",
        f"document_date = {_fmt(analysis.document_date)}",
        f"amount = {amount_str}",
        f"tax_relevant = {analysis.tax_relevant}",
        f"tax_category = {_fmt(analysis.tax_category)}",
    ]
    # NEW-008: gleiche Prompt-Injection-Haertung wie die Analyse-Prompts
    # (gemeinsamer <document_ocr>-Block). Ein praepariertes Dokument koennte
    # sonst den Verifier zu einem leeren Issues-Array ueberreden; die
    # Sanity-Checks (Betrag, Steuerkategorie) laufen ohnehin unabhaengig davon.
    block = fill_prompt(
        load_prompt_template("document_ocr_block.txt"),
        {"ocr_text": ocr_text[:1500]},
    )
    prompt = (
        "Du bist ein Validator fuer KI-extrahierte Beleg-Felder. Pruefe ob die "
        "folgenden Werte zum Dokumentinhalt passen. Antworte ausschliesslich "
        "mit JSON: {\"issues\": [\"...\", ...]}. Wenn alles plausibel ist, "
        "leeres Array. Issues bitte konkret formulieren als Frage an den User "
        "(auf Deutsch, mit Umlauten, Anrede \"Sie\").\n\n"
        f"{block}\n\n"
        "Extrahierte Felder:\n" + "\n".join(summary_lines)
    )
    try:
        raw = await call_llm(prompt, settings, schema=_VERIFIER_SCHEMA)
        data = _parse_analysis_json(raw)
        if not data:
            return []
        issues = data.get("issues", [])
        # Auf max. 5 stutzen — mehr als 5 Review-Fragen waeren UI-mass overload.
        # Wenn der Verifier 10 echte Issues findet, sind die Daten so unsicher
        # dass die Erst-5 ausreichen um needs_review zu rechtfertigen.
        return _as_str_list(issues, split_commas=False)[:5]
    except Exception:
        logger.warning("Verifier-Pass fehlgeschlagen", exc_info=True)
        return []


async def _try_combined_analysis(
    ocr_text: str,
    settings: Settings,
    filing_scopes: list[dict] | None = None,
    corrections: list[dict] | None = None,
) -> AnalysisResult | None:
    """Kombinierte Analyse mit einem einzigen LLM-Aufruf.

    Returns:
        None, wenn die Antwort nicht als JSON-Objekt lesbar ist.

    Raises:
        OllamaError: Ollama nicht erreichbar / Modell fehlt / Serverfehler.
    """
    try:
        prompt = _render_ocr_prompt(
            "analyze_document.txt",
            ocr_text,
            filing_scopes=_format_filing_scopes(filing_scopes),
            corrections=_format_correction_examples(corrections),
        )
    except FileNotFoundError:
        logger.error("Kombiniertes Prompt-Template nicht gefunden")
        return None
    # JSON-Schema-constrained Generation: Ollama >= 0.5 erzwingt schema-konformen Output.
    raw_response = await chat_json(prompt, settings, schema=_analysis_schema())
    data = _parse_analysis_json(raw_response)
    if not data:
        logger.warning("Kombinierte Analyse: JSON konnte nicht geparst werden")
        return None

    return _build_result(data, settings.CONFIDENCE_THRESHOLD)


# (Template, Teil-Schema) der sequentiellen Einzel-Prompts.
_SEQUENTIAL_STEPS = (
    ("classify_document.txt", ("document_type", "confidence")),
    ("extract_metadata.txt", (
        "title", "sender", "recipient", "document_date", "amount",
        "currency", "reference_number", "tags", "summary",
    )),
    ("assess_tax_relevance.txt", ("tax_relevant", "tax_category", "tax_year")),
    ("extract_warranty_info.txt", None),  # Antwort ist direkt das warranty_info-Objekt
)


async def _try_sequential_analysis(
    ocr_text: str,
    settings: Settings,
) -> AnalysisResult | None:
    """Fallback bei unlesbarer kombinierter Antwort: 4 Einzel-Prompts.

    Die Teil-Antworten werden zu EINEM dict zusammengefuehrt und laufen durch
    dieselbe Normalisierung + Sanity-Checks wie der kombinierte Pfad.
    Ergebnis ist immer NEEDS_REVIEW.

    Raises:
        OllamaError: beim ersten Transport-/Modellfehler (keine weiteren Aufrufe).
    """
    merged: dict = {}
    for template_name, keys in _SEQUENTIAL_STEPS:
        schema = _sub_schema(*keys) if keys else _WARRANTY_SCHEMA | {"type": "object"}
        prompt = _render_ocr_prompt(template_name, ocr_text)
        data = _parse_analysis_json(await chat_json(prompt, settings, schema=schema))
        if not data:
            logger.warning("Sequentielle Analyse: %s lieferte kein JSON", template_name)
            continue
        if keys:
            merged.update({k: data.get(k) for k in keys})
        else:
            merged["warranty_info"] = data

    result = _build_result(merged, settings.CONFIDENCE_THRESHOLD, force_review=True)
    # Mindestens Klassifikation oder Titel muss geklappt haben
    if result.document_type != "SONSTIGES" or result.title:
        return result
    return None


# AVAIL-06: Prozessweit hoechstens ANALYSIS_CONCURRENCY Analysen (OCR + LLM)
# gleichzeitig — Queue-Worker, Re-Analysen und der KI-Pass der Aufteilung
# (split_llm_service) teilen sich den Slot
# ("ein Dokument gleichzeitig", Heim-Hardware). Frueher liefen beliebig viele
# Re-Analysen parallel zum Worker; Ollama-Timeouts kaskadierten und neue Uploads
# fielen in NEEDS_REVIEW. Ein Semaphore pro Event-Loop (asyncio-Primitive binden
# sich an den Loop; Tests laufen mit einem Loop pro Testfunktion).
ANALYSIS_CONCURRENCY = 1
_analysis_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def analysis_slot() -> asyncio.Semaphore:
    """Prozessweiter Slot fuer OCR + LLM (auch der KI-Pass der Aufteilung)."""
    loop = asyncio.get_running_loop()
    slot = _analysis_slots.get(loop)
    if slot is None:
        slot = _analysis_slots[loop] = asyncio.Semaphore(ANALYSIS_CONCURRENCY)
    return slot


async def analyze_document(
    file_path: Path,
    file_type: str,
    settings: Settings,
    filing_scopes: list[dict] | None = None,
    session=None,
    pre_ocr_text: str | None = None,
    pre_ocr_pages: tuple[list[PageText], int] | None = None,
) -> tuple[OcrResult | None, AnalysisResult | None]:
    """Dokumentenanalyse mit prozessweitem Concurrency-Slot (AVAIL-06), siehe
    :func:`_analyze_document_unlimited`."""
    async with analysis_slot():
        return await _analyze_document_unlimited(
            file_path, file_type, settings,
            filing_scopes=filing_scopes, session=session, pre_ocr_text=pre_ocr_text,
            pre_ocr_pages=pre_ocr_pages,
        )


async def _analyze_document_unlimited(
    file_path: Path,
    file_type: str,
    settings: Settings,
    filing_scopes: list[dict] | None = None,
    session=None,
    pre_ocr_text: str | None = None,
    pre_ocr_pages: tuple[list[PageText], int] | None = None,
) -> tuple[OcrResult | None, AnalysisResult | None]:
    """Fuehrt die vollstaendige Dokumentenanalyse durch.

    Pipeline: OCR -> Text kuerzen + sanitisieren -> Few-Shot-Build ->
    LLM-Analyse (kombiniert; sequentiell NUR bei unlesbarer Antwort) ->
    optional Verifier.

    Wasserfall-Degradation: OCR-Fehler, LLM-Ausfall (nicht erreichbar, Modell
    fehlt, Serverfehler) und niedrige Konfidenz enden in NEEDS_REVIEW mit
    mindestens einer Rueckfrage. Bei Transportfehlern laeuft KEIN
    sequentieller Fallback (der wuerde nur viermal dasselbe Timeout abwarten).

    Args:
        file_path: Pfad zur Dokumentdatei.
        file_type: Dateityp (pdf, jpg, etc.).
        settings: App-Konfiguration.
        filing_scopes: Verfuegbare Ablagebereiche fuer LLM-Zuweisung.
        session: Optionale DB-Session — wenn vorhanden werden CorrectionMappings
            als Few-Shot-Examples in den Prompt injiziert (Lerneffekt). Es wird
            nur gelesen; die Analyse haelt keine Schreib-Transaktion offen.
        pre_ocr_text: F-07: vorher bekannter Text (z.B. E-Mail-Body). Wenn
            gesetzt, wird OCR uebersprungen und der Text direkt verwendet.
            Konfidenz wird auf 1.0 angenommen.
        pre_ocr_pages: Seitentext aus der Aufteilungs-Erkennung als
            ``(Seiten, Seitenzahl)`` (Teil-Jobs, "Als ein Dokument behalten").
            OCR wird uebersprungen; Konfidenz = Mittelwert der Seiten.

    Returns:
        Tuple aus (OcrResult, AnalysisResult). OcrResult kann None sein.
    """
    # 1. OCR (oder Skip wenn Text bereits vorhanden)
    if pre_ocr_text and pre_ocr_text.strip():
        ocr_result = OcrResult(full_text=pre_ocr_text, average_confidence=1.0, pages=[])
        logger.info("OCR uebersprungen (pre_ocr_text vorhanden, %d Zeichen)", len(pre_ocr_text))
    else:
        if pre_ocr_pages is not None:
            pages, page_count = pre_ocr_pages
            ocr_result = ocr_result_from_pages(pages, page_count)
            logger.info("OCR uebersprungen (Seitentext aus der Aufteilung, %d Seiten)", page_count)
        else:
            ocr_result = await extract_text(file_path, file_type, settings)
        if not ocr_result or not ocr_result.full_text.strip():
            logger.warning("OCR hat keinen Text extrahiert fuer: %s", file_path.name)
            return ocr_result, AnalysisResult(
                needs_review=True,
                review_questions=[
                    "Die Texterkennung (OCR) konnte keinen Text extrahieren. "
                    "Bitte prüfen Sie das Dokument und tragen Sie die Daten von Hand ein."
                ],
            )

    # 2. Text kuerzen fuer LLM
    # NEW-A (Re-Review): Sanitizer fuer <document_ocr>-Delimiter, damit E-Mail-
    # Body oder OCR-Output mit den Tags den Wrap nicht aufbrechen koennen.
    truncated_text = _sanitize_ocr_for_prompt(_truncate_text(ocr_result.full_text))
    logger.info(
        "OCR abgeschlossen: %d Zeichen (gekuerzt: %d), starte LLM-Analyse...",
        len(ocr_result.full_text),
        len(truncated_text),
    )

    # 2b. Few-Shot-Examples aus User-Korrekturen (wenn DB-Session da)
    corrections = await _load_correction_examples(session) if session else []
    if corrections:
        logger.info("Few-Shot-Pipeline: %d Korrektur-Beispiele injiziert", len(corrections))

    try:
        # 3. Kombinierte Analyse (Primaerstrategie)
        analysis = await _try_combined_analysis(truncated_text, settings, filing_scopes, corrections=corrections)
        if analysis:
            logger.info(
                "Kombinierte Analyse erfolgreich: Typ=%s, Konfidenz=%.1f%%",
                analysis.document_type,
                analysis.confidence * 100,
            )
        else:
            # 4. Fallback NUR bei unlesbarer Antwort: Sequentielle Analyse
            logger.info("Kombinierte Analyse unlesbar, versuche sequentielle Analyse...")
            analysis = await _try_sequential_analysis(truncated_text, settings)
            if analysis:
                logger.info("Sequentielle Analyse erfolgreich: Typ=%s", analysis.document_type)
    except OllamaError as e:
        # 5. LLM ausgefallen (nicht erreichbar / Modell fehlt / Serverfehler)
        logger.warning("LLM-Analyse fehlgeschlagen fuer %s: %s", file_path.name, e)
        return ocr_result, _analysis_failed_result(e)

    if analysis is None:
        logger.warning("LLM-Analyse lieferte keine verwertbare Antwort fuer: %s", file_path.name)
        return ocr_result, _analysis_failed_result(None)

    # 3b. Optionaler Verifier-Pass fuer unsichere Ergebnisse (needs_review oder
    # Konfidenz <= LLM_VERIFIER_THRESHOLD). Er ergaenzt konkrete Rueckfragen —
    # ohne ihn bekaeme der Benutzer bei niedriger Konfidenz nur die generische.
    # Auf sichere Ergebnisse wird er nicht angewendet (doppelte Latenz).
    if settings.LLM_USE_VERIFIER and (
        analysis.needs_review or analysis.confidence <= settings.LLM_VERIFIER_THRESHOLD
    ):
        issues = await _verify_analysis(truncated_text, analysis, settings)
        if issues:
            analysis.needs_review = True
            for issue in issues:
                if issue not in analysis.review_questions:
                    analysis.review_questions.append(issue)
            logger.info("Verifier hat %d Probleme gefunden", len(issues))

    return ocr_result, _ensure_review_question(analysis)
