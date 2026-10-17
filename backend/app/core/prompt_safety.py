"""Gemeinsamer Delimiter-Sanitizer fuer LLM-Prompts (INJ-06).

Die Prompt-Templates wrappen untrusted Daten (OCR-Text, E-Mail-Felder,
RAG-Auszuege, User-Fragen) in XML-artige Tags wie ``<document_ocr>`` oder
``<email_data>``. Enthaelt der Datenteil selbst ein solches Tag, koennte er
scheinbar aus dem Wrap ausbrechen und eigene "Anweisungen" anhaengen.

Frueher gab es dafuer drei Kopien mit unterschiedlicher Strenge — die
E-Mail-Variante war case- und whitespace-sensitiv (``</EMAIL_DATA>`` und
``</email_data >`` kamen unveraendert durch). Alle Aufrufer nutzen jetzt diese
eine Funktion: Gross-/Kleinschreibung egal, Leerzeichen innerhalb des Tags
toleriert, Attribute (``<email_data x="1">``) ebenfalls erfasst.
"""

import re
from functools import lru_cache


@lru_cache(maxsize=32)
def _delimiter_re(tags: tuple[str, ...]) -> re.Pattern[str]:
    names = "|".join(re.escape(tag) for tag in tags)
    # <tag>, </tag>, < / TAG >, <tag attr="..."> und <tag/> — alles, was ein
    # Modell als Beginn oder Ende des Wraps lesen koennte.
    return re.compile(rf"<\s*(/?)\s*({names})\b[^<>]*>", re.IGNORECASE)


def neutralize_prompt_tags(text: str | None, *tags: str) -> str:
    """Ersetzt Vorkommen der Delimiter-Tags ``tags`` durch ``&lt;tag&gt;``.

    Der restliche Text bleibt unveraendert. Leere Eingaben -> ``""``.
    """
    if not text:
        return ""
    if not tags:
        return text
    pattern = _delimiter_re(tuple(tag.lower() for tag in tags))
    return pattern.sub(lambda m: f"&lt;{m.group(1)}{m.group(2).lower()}&gt;", text)
