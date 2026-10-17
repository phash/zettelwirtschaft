"""Text-Helfer, die mehrere Module teilen (Slugs, Dateinamen)."""

import unicodedata

# Deutsche Umlaute werden fachlich korrekt transliteriert (ä -> ae), alles
# andere ueber die Unicode-Zerlegung (é -> e). Ohne das wurde aus "Müller"
# im Steuerpaket "M_ller" und aus "Ärzte" der Slug "rzte".
_GERMAN_TRANSLITERATION = {
    "ä": "ae", "ö": "oe", "ü": "ue",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss", "ẞ": "SS",
}


def transliterate_ascii(text: str) -> str:
    """Wandelt Text in ASCII um: Umlaute -> ae/oe/ue/ss, Akzente entfernt.

    Zeichen ohne ASCII-Entsprechung (Emoji, CJK, €) fallen weg — Aufrufer
    muessen mit einem leeren Ergebnis rechnen.
    """
    if not text:
        return ""
    for char, repl in _GERMAN_TRANSLITERATION.items():
        text = text.replace(char, repl)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).encode(
        "ascii", "ignore"
    ).decode("ascii")
