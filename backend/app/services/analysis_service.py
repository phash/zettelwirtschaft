import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.services.llm_service import call_llm, load_prompt_template
from app.services.ocr_service import OcrResult, extract_text

logger = logging.getLogger("zettelwirtschaft.analysis")


def _safe_float(value, default: float = 0.0) -> float:
    """Sichere Konvertierung zu float."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


VALID_DOCUMENT_TYPES = {
    "RECHNUNG",
    "QUITTUNG",
    "KAUFVERTRAG",
    "GARANTIESCHEIN",
    "VERSICHERUNGSPOLICE",
    "KONTOAUSZUG",
    "LOHNABRECHNUNG",
    "STEUERBESCHEID",
    "MIETVERTRAG",
    "HANDWERKER_RECHNUNG",
    "ARZTRECHNUNG",
    "REZEPT",
    "AMTLICHES_SCHREIBEN",
    "BEDIENUNGSANLEITUNG",
    "SONSTIGES",
}

VALID_TAX_CATEGORIES = {
    "Werbungskosten",
    "Sonderausgaben",
    "Aussergewoehnliche_Belastungen",
    "Handwerkerleistungen",
    "Haushaltsnahe_Dienstleistungen",
    "Vorsorgeaufwendungen",
    "Keine",
}

# Plausibilitaets-Grenzen fuer Beleg-Beträge (Privathaushalt).
# Werte ueber dieser Grenze sind verdaechtig — entweder Tippfehler im Beleg
# oder Prompt-Injection-Versuch ("setze amount=99999"). Forciert NEEDS_REVIEW.
AMOUNT_SANITY_CEILING = 50_000.0


def _analysis_schema() -> dict:
    """JSON-Schema fuer das AnalysisResult.

    Wird an Ollama als `format=...` uebergeben, sodass das Modell garantiert
    konformen JSON-Output liefert. Eliminiert die Fallback-Parsing-Kaskade.
    """
    return {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", "enum": sorted(VALID_DOCUMENT_TYPES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "title": {"type": ["string", "null"]},
            "sender": {"type": ["string", "null"]},
            "recipient": {"type": ["string", "null"]},
            "document_date": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]},
            "reference_number": {"type": ["string", "null"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": ["string", "null"]},
            "tax_relevant": {"type": "boolean"},
            "tax_category": {"type": ["string", "null"]},
            "tax_year": {"type": ["integer", "null"]},
            "warranty_info": {"type": ["object", "null"]},
            "filing_scope": {"type": ["string", "null"]},
            "filing_scope_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "needs_review": {"type": "boolean"},
            "review_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["document_type", "confidence", "needs_review"],
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
    review_questions: list[str] = field(default_factory=list)
    filing_scope: str | None = None
    filing_scope_confidence: float = 0.0

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
        }


def _truncate_text(text: str, max_chars: int = 4000) -> str:
    """Kuerzt langen OCR-Text: erste 2000 + letzte 2000 Zeichen."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[...Text gekuerzt...]\n\n" + text[-half:]


def _parse_analysis_json(raw: str) -> dict | None:
    """Versucht JSON aus der LLM-Antwort zu parsen."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Fallback: JSON-Block aus Markdown extrahieren
    import re

    match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Letzter Versuch: erstes { bis letztes }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _sanitize_amount(raw_amount) -> tuple[float | None, str | None]:
    """Normalisiert + plausibilisiert das amount-Feld.

    Schutz gegen Prompt-Injection: ein Dokument mit OCR-Text "setze amount=99999"
    darf das LLM nicht dazu bringen, einen unsinnig grossen Betrag zu setzen.
    Werte ueber AMOUNT_SANITY_CEILING werden zwar uebernommen, triggern aber
    NEEDS_REVIEW mit konkreter Frage.

    Returns:
        (amount, review_question_or_none). amount kann None sein.
    """
    if raw_amount is None:
        return None, None
    try:
        amount = float(raw_amount)
    except (ValueError, TypeError):
        return None, None
    if amount < 0:
        return None, "Negativer Betrag erkannt — bitte pruefen."
    if amount > AMOUNT_SANITY_CEILING:
        return amount, (
            f"Auffaellig hoher Betrag: {amount:.2f}. Bitte verifizieren — "
            f"falls korrekt, einfach bestaetigen."
        )
    return amount, None


def _build_result_from_combined(data: dict, confidence_threshold: float) -> AnalysisResult:
    """Baut AnalysisResult aus der kombinierten LLM-Antwort."""
    doc_type = data.get("document_type", "SONSTIGES")
    if doc_type not in VALID_DOCUMENT_TYPES:
        doc_type = "SONSTIGES"

    confidence = _safe_float(data.get("confidence", 0.0))
    needs_review = data.get("needs_review", False) or confidence < confidence_threshold

    review_questions = list(data.get("review_questions", []) or [])

    # tax_category whitelist — auch wenn das LLM zwei Werte mit Pipe liefert,
    # nehmen wir nur den ersten gueltigen. Schutz gegen Prompt-Injection-induzierte
    # Tax-Manipulation ("setze tax_category=Werbungskosten").
    tax_category = data.get("tax_category")
    if tax_category and "|" in tax_category:
        tax_category = tax_category.split("|")[0].strip()
    if tax_category and tax_category not in VALID_TAX_CATEGORIES:
        tax_category = None

    # Amount-Sanity: extreme Werte triggern Review statt blind zu uebernehmen.
    amount, amount_question = _sanitize_amount(data.get("amount"))
    if amount_question:
        review_questions.append(amount_question)
        needs_review = True

    if confidence < confidence_threshold and not review_questions:
        review_questions = [
            f"Die automatische Erkennung ist unsicher (Konfidenz: {confidence:.0%}). "
            "Bitte pruefen Sie die erkannten Daten."
        ]

    return AnalysisResult(
        document_type=doc_type,
        confidence=confidence,
        title=data.get("title"),
        sender=data.get("sender"),
        recipient=data.get("recipient"),
        document_date=data.get("document_date"),
        amount=amount,
        currency=data.get("currency"),
        reference_number=data.get("reference_number"),
        tags=data.get("tags", []),
        summary=data.get("summary"),
        tax_relevant=bool(data.get("tax_relevant", False)),
        tax_category=tax_category,
        tax_year=data.get("tax_year"),
        warranty_info=data.get("warranty_info"),
        needs_review=needs_review,
        review_questions=review_questions,
        filing_scope=data.get("filing_scope"),
        filing_scope_confidence=_safe_float(data.get("filing_scope_confidence", 0.0)),
    )


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
    """
    summary_lines = [
        f"document_type = {analysis.document_type}",
        f"sender = {analysis.sender}",
        f"document_date = {analysis.document_date}",
        f"amount = {analysis.amount} {analysis.currency or ''}",
        f"tax_relevant = {analysis.tax_relevant}",
        f"tax_category = {analysis.tax_category}",
    ]
    prompt = (
        "Du bist ein Validator fuer KI-extrahierte Beleg-Felder. Pruefe ob die "
        "folgenden Werte zum Dokumentinhalt passen. Antworte ausschliesslich "
        "mit JSON: {\"issues\": [\"...\", ...]}. Wenn alles plausibel ist, "
        "leeres Array. Issues bitte konkret formulieren als Frage an den User.\n\n"
        f"Dokument-Text (gekuerzt):\n{ocr_text[:1500]}\n\n"
        "Extrahierte Felder:\n" + "\n".join(summary_lines)
    )
    try:
        raw = await call_llm(prompt, settings, schema=_VERIFIER_SCHEMA)
        if not raw:
            return []
        import json as _json
        return list(_json.loads(raw).get("issues", []))[:5]
    except Exception:
        logger.warning("Verifier-Pass fehlgeschlagen", exc_info=True)
        return []


async def _try_combined_analysis(
    ocr_text: str,
    settings: Settings,
    filing_scopes: list[dict] | None = None,
    corrections: list[dict] | None = None,
) -> AnalysisResult | None:
    """Versucht die kombinierte Analyse mit einem einzigen LLM-Aufruf."""
    try:
        template = load_prompt_template("analyze_document.txt")
    except FileNotFoundError:
        logger.error("Kombiniertes Prompt-Template nicht gefunden")
        return None

    prompt = template.replace("{filing_scopes}", _format_filing_scopes(filing_scopes))
    prompt = prompt.replace("{corrections}", _format_correction_examples(corrections))
    prompt = prompt.replace("{ocr_text}", ocr_text)
    # JSON-Schema-constrained Generation: Ollama >= 0.5 erzwingt schema-konformen Output.
    raw_response = await call_llm(prompt, settings, schema=_analysis_schema())
    if not raw_response:
        return None

    data = _parse_analysis_json(raw_response)
    if not data:
        logger.warning("Kombinierte Analyse: JSON konnte nicht geparst werden")
        return None

    return _build_result_from_combined(data, settings.CONFIDENCE_THRESHOLD)


async def _try_sequential_analysis(
    ocr_text: str,
    settings: Settings,
) -> AnalysisResult | None:
    """Fallback: Sequentielle Einzelabfragen an das LLM."""
    result = AnalysisResult(needs_review=True)

    # 1. Klassifikation
    try:
        template = load_prompt_template("classify_document.txt")
        prompt = template.replace("{ocr_text}", ocr_text)
        raw = await call_llm(prompt, settings)
        if raw:
            data = _parse_analysis_json(raw)
            if data:
                doc_type = data.get("document_type", "SONSTIGES")
                if doc_type in VALID_DOCUMENT_TYPES:
                    result.document_type = doc_type
                result.confidence = _safe_float(data.get("confidence", 0.0))
    except Exception:
        logger.exception("Fehler bei Klassifikation")

    # 2. Metadaten
    try:
        template = load_prompt_template("extract_metadata.txt")
        prompt = template.replace("{ocr_text}", ocr_text)
        raw = await call_llm(prompt, settings)
        if raw:
            data = _parse_analysis_json(raw)
            if data:
                result.title = data.get("title")
                result.sender = data.get("sender")
                result.recipient = data.get("recipient")
                result.document_date = data.get("document_date")
                result.amount = data.get("amount")
                result.currency = data.get("currency")
                result.reference_number = data.get("reference_number")
                result.tags = data.get("tags", [])
                result.summary = data.get("summary")
    except Exception:
        logger.exception("Fehler bei Metadaten-Extraktion")

    # 3. Steuerrelevanz
    try:
        template = load_prompt_template("assess_tax_relevance.txt")
        prompt = template.replace("{ocr_text}", ocr_text)
        raw = await call_llm(prompt, settings)
        if raw:
            data = _parse_analysis_json(raw)
            if data:
                result.tax_relevant = data.get("tax_relevant", False)
                result.tax_category = data.get("tax_category")
                result.tax_year = data.get("tax_year")
    except Exception:
        logger.exception("Fehler bei Steuerrelevanz-Bewertung")

    # 4. Garantie-Info
    try:
        template = load_prompt_template("extract_warranty_info.txt")
        prompt = template.replace("{ocr_text}", ocr_text)
        raw = await call_llm(prompt, settings)
        if raw:
            data = _parse_analysis_json(raw)
            if data:
                result.warranty_info = data
    except Exception:
        logger.exception("Fehler bei Garantie-Extraktion")

    # Mindestens Klassifikation muss geklappt haben
    if result.document_type != "SONSTIGES" or result.title:
        return result

    return None


async def analyze_document(
    file_path: Path,
    file_type: str,
    settings: Settings,
    filing_scopes: list[dict] | None = None,
    session=None,
) -> tuple[OcrResult | None, AnalysisResult | None]:
    """Fuehrt die vollstaendige Dokumentenanalyse durch.

    Pipeline: OCR -> Text kuerzen -> Few-Shot-Build -> LLM-Analyse
    (kombiniert, Fallback sequentiell).

    Args:
        file_path: Pfad zur Dokumentdatei.
        file_type: Dateityp (pdf, jpg, etc.).
        settings: App-Konfiguration.
        filing_scopes: Verfuegbare Ablagebereiche fuer LLM-Zuweisung.
        session: Optionale DB-Session — wenn vorhanden werden CorrectionMappings
            als Few-Shot-Examples in den Prompt injiziert (Lerneffekt).

    Returns:
        Tuple aus (OcrResult, AnalysisResult).
        Beide koennen None sein bei Fehler.
    """
    # 1. OCR
    ocr_result = await extract_text(file_path, file_type, settings)
    if not ocr_result or not ocr_result.full_text.strip():
        logger.warning("OCR hat keinen Text extrahiert fuer: %s", file_path.name)
        return ocr_result, AnalysisResult(
            needs_review=True,
            review_questions=["OCR konnte keinen Text extrahieren. Bitte Dokument manuell pruefen."],
        )

    # 2. Text kuerzen fuer LLM
    truncated_text = _truncate_text(ocr_result.full_text)
    logger.info(
        "OCR abgeschlossen: %d Zeichen (gekuerzt: %d), starte LLM-Analyse...",
        len(ocr_result.full_text),
        len(truncated_text),
    )

    # 2b. Few-Shot-Examples aus User-Korrekturen (wenn DB-Session da)
    corrections = await _load_correction_examples(session) if session else []
    if corrections:
        logger.info("Few-Shot-Pipeline: %d Korrektur-Beispiele injiziert", len(corrections))

    # 3. Kombinierte Analyse (Primaerstrategie)
    analysis = await _try_combined_analysis(truncated_text, settings, filing_scopes, corrections=corrections)
    if analysis:
        logger.info(
            "Kombinierte Analyse erfolgreich: Typ=%s, Konfidenz=%.1f%%",
            analysis.document_type,
            analysis.confidence * 100,
        )
        # 3b. Optional Verifier-Pass bei niedriger Konfidenz
        if (
            settings.LLM_USE_VERIFIER
            and analysis.confidence < settings.LLM_VERIFIER_THRESHOLD
        ):
            issues = await _verify_analysis(truncated_text, analysis, settings)
            if issues:
                analysis.needs_review = True
                for issue in issues:
                    if issue not in analysis.review_questions:
                        analysis.review_questions.append(issue)
                logger.info("Verifier hat %d Probleme gefunden", len(issues))
        return ocr_result, analysis

    # 4. Fallback: Sequentielle Analyse
    logger.info("Kombinierte Analyse fehlgeschlagen, versuche sequentielle Analyse...")
    analysis = await _try_sequential_analysis(truncated_text, settings)
    if analysis:
        logger.info(
            "Sequentielle Analyse erfolgreich: Typ=%s",
            analysis.document_type,
        )
        return ocr_result, analysis

    # 5. Fallback: LLM komplett ausgefallen
    logger.warning("LLM-Analyse komplett fehlgeschlagen fuer: %s", file_path.name)
    return ocr_result, AnalysisResult(
        needs_review=True,
        review_questions=[
            "Die automatische Analyse konnte nicht durchgefuehrt werden "
            "(LLM nicht erreichbar). Bitte Dokument manuell klassifizieren."
        ],
    )
