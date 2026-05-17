"""LLM-basierte Relevanzpruefung fuer E-Mails."""

import json
import logging

from app.config import Settings
from app.services.llm_service import call_llm, load_prompt_template

logger = logging.getLogger(__name__)


def _sanitize_email_field(text: str) -> str:
    """F-11 (Re-Review): Entschaerft die <email_data>-Delimiter-Tags damit
    ein Absender/Betreff mit `</email_data>...neue Anweisung...<email_data>`
    nicht aus dem Wrap ausbrechen kann.
    """
    if not text:
        return ""
    return (
        text.replace("<email_data>", "&lt;email_data&gt;")
        .replace("</email_data>", "&lt;/email_data&gt;")
    )


async def check_email_relevance(
    sender: str,
    subject: str,
    body_snippet: str,
    attachment_names: list[str],
    settings: Settings,
) -> dict:
    """Prueft per LLM ob eine E-Mail archivierungswuerdige Dokumente enthaelt.

    Returns:
        {"relevant": bool, "reason": str}
        Bei Fehler: {"relevant": True, "reason": "..."} (Fallback: lieber zu viel archivieren)
    """
    template = load_prompt_template("email_relevance.txt")
    prompt = template.format(
        sender=_sanitize_email_field(sender) or "unbekannt",
        subject=_sanitize_email_field(subject) or "(kein Betreff)",
        body_snippet=_sanitize_email_field((body_snippet or "")[:1000]),
        attachment_names=_sanitize_email_field(
            ", ".join(attachment_names) if attachment_names else "keine"
        ),
    )

    try:
        response = await call_llm(prompt, settings)
        if not response:
            logger.warning("LLM-Antwort leer bei Relevanzpruefung")
            return {"relevant": True, "reason": "LLM-Fehler, Fallback: als relevant markiert"}

        data = json.loads(response)
        return {
            "relevant": bool(data.get("relevant", True)),
            "reason": str(data.get("reason", "")),
        }
    except json.JSONDecodeError:
        logger.warning("LLM-Antwort kein gueltiges JSON: %s", response[:200] if response else "")
        return {"relevant": True, "reason": "JSON-Fehler, Fallback: als relevant markiert"}
    except Exception:
        logger.exception("Fehler bei E-Mail-Relevanzpruefung")
        return {"relevant": True, "reason": "Unerwarteter Fehler, Fallback: als relevant markiert"}
