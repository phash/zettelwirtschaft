import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.services.llm_service import call_llm, load_prompt_template
from app.services.ocr_service import OcrResult, extract_text

logger = logging.getLogger("zettelwirtschaft.analysis")

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


@dataclass
class AnalysisResult:
    document_type: str = "SONSTIGES"
    confidence: float = 0.0
    title: str | None = None
    sender: str | None = None
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


def _build_result_from_combined(data: dict, confidence_threshold: float) -> AnalysisResult:
    """Baut AnalysisResult aus der kombinierten LLM-Antwort."""
    doc_type = data.get("document_type", "SONSTIGES")
    if doc_type not in VALID_DOCUMENT_TYPES:
        doc_type = "SONSTIGES"

    confidence = float(data.get("confidence", 0.0))
    needs_review = data.get("needs_review", False) or confidence < confidence_threshold

    review_questions = data.get("review_questions", [])
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
        amount=data.get("amount"),
        currency=data.get("currency"),
        reference_number=data.get("reference_number"),
        tags=data.get("tags", []),
        summary=data.get("summary"),
        tax_relevant=data.get("tax_relevant", False),
        tax_category=data.get("tax_category"),
        tax_year=data.get("tax_year"),
        warranty_info=data.get("warranty_info"),
        needs_review=needs_review,
        review_questions=review_questions,
    )


async def _try_combined_analysis(
    ocr_text: str,
    settings: Settings,
) -> AnalysisResult | None:
    """Versucht die kombinierte Analyse mit einem einzigen LLM-Aufruf."""
    try:
        template = load_prompt_template("analyze_document.txt")
    except FileNotFoundError:
        logger.error("Kombiniertes Prompt-Template nicht gefunden")
        return None

    prompt = template.replace("{ocr_text}", ocr_text)
    raw_response = await call_llm(prompt, settings)
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
                result.confidence = float(data.get("confidence", 0.0))
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
) -> tuple[OcrResult | None, AnalysisResult | None]:
    """Fuehrt die vollstaendige Dokumentenanalyse durch.

    Pipeline: OCR -> Text kuerzen -> LLM-Analyse (kombiniert, Fallback sequentiell)

    Args:
        file_path: Pfad zur Dokumentdatei.
        file_type: Dateityp (pdf, jpg, etc.).
        settings: App-Konfiguration.

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

    # 3. Kombinierte Analyse (Primaerstrategie)
    analysis = await _try_combined_analysis(truncated_text, settings)
    if analysis:
        logger.info(
            "Kombinierte Analyse erfolgreich: Typ=%s, Konfidenz=%.1f%%",
            analysis.document_type,
            analysis.confidence * 100,
        )
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
