import json
import logging
import re
from pathlib import Path

from app.config import Settings
from app.services.ollama_client import (
    OllamaError,
    OllamaErrorKind,
    aclose_client,  # noqa: F401 — Re-Export fuer den lifespan-Hook (main.py)
    forget_capability,
    model_capabilities,
    post_json,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Anteil des Kontextfensters, ab dem gewarnt wird — Ollama schneidet zu lange
# Prompts stillschweigend ab (prompt_eval_count == num_ctx).
_CONTEXT_WARN_RATIO = 0.95
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def load_prompt_template(name: str) -> str:
    """Laedt ein Prompt-Template aus dem prompts-Verzeichnis.

    Args:
        name: Dateiname ohne Pfad (z.B. "analyze_document.txt").

    Returns:
        Inhalt der Template-Datei.

    Raises:
        FileNotFoundError: Wenn das Template nicht existiert.
    """
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt-Template nicht gefunden: {path}")
    return path.read_text(encoding="utf-8")


def fill_prompt(template: str, values: dict[str, str]) -> str:
    """Ersetzt ``{name}``-Platzhalter in EINEM Durchgang.

    Eingesetzte Werte werden nicht erneut durchsucht: steht in einem OCR-Text
    "{question}", bleibt das Text und wird nicht durch die Benutzerfrage
    ersetzt. Unbekannte Platzhalter (und JSON-Klammern im Template) bleiben
    unveraendert.
    """
    return _PLACEHOLDER_RE.sub(
        lambda m: values[m.group(1)] if m.group(1) in values else m.group(0),
        template,
    )


# ---------------------------------------------------------------------------
# Minimaler JSON-Schema-Validator (Teilmenge, die unsere Schemas nutzen).
# Ollama antwortet auch dann mit HTTP 200, wenn ein Modell das Schema verletzt
# (z.B. abgebrochen bei num_predict) — deshalb pruefen wir selbst.
# ---------------------------------------------------------------------------

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _matches_type(value, type_name: str) -> bool:
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _JSON_TYPES.get(type_name)
    return expected is None or isinstance(value, expected)


def schema_violations(value, schema: dict, path: str = "$") -> list[str]:
    """Liste der Schema-Verletzungen (leer = konform).

    Unterstuetzt type (auch Listen), required, properties, items, enum,
    minimum, maximum.
    """
    problems: list[str] = []
    types = schema.get("type")
    if types is not None:
        type_list = types if isinstance(types, list) else [types]
        if not any(_matches_type(value, t) for t in type_list):
            return [f"{path}: Typ {type(value).__name__} statt {types}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: Wert {value!r} nicht erlaubt")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: {value} > {schema['maximum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key}: fehlt")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                problems.extend(schema_violations(value[key], sub, f"{path}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            problems.extend(schema_violations(item, schema["items"], f"{path}[{i}]"))
    return problems


def _log_metrics(data: dict, settings: Settings, label: str) -> None:
    """Laufzeiten aus der Ollama-Antwort loggen (Nanosekunden -> Sekunden)."""
    def _secs(key: str) -> float:
        return (data.get(key) or 0) / 1e9

    prompt_tokens = data.get("prompt_eval_count") or 0
    logger.info(
        "LLM-%s: Modell %s, Prompt %d Tokens (%.1fs), Ausgabe %d Tokens (%.1fs), "
        "Laden %.1fs, gesamt %.1fs",
        label, data.get("model") or settings.OLLAMA_MODEL,
        prompt_tokens, _secs("prompt_eval_duration"),
        data.get("eval_count") or 0, _secs("eval_duration"),
        _secs("load_duration"), _secs("total_duration"),
    )
    if prompt_tokens >= settings.OLLAMA_NUM_CTX * _CONTEXT_WARN_RATIO:
        logger.warning(
            "LLM-Prompt fuellt das Kontextfenster (%d von %d Tokens) — Text wurde "
            "eventuell abgeschnitten. OLLAMA_NUM_CTX erhoehen.",
            prompt_tokens, settings.OLLAMA_NUM_CTX,
        )
    if data.get("done_reason") == "length":
        logger.warning("LLM-%s-Antwort wurde bei der Token-Grenze abgeschnitten", label)


async def _chat(
    prompt: str,
    settings: Settings,
    *,
    system_prompt: str | None,
    response_format: str | dict | None,
    temperature: float,
    timeout: float,
    num_predict: int | None,
) -> str:
    """Ein /api/chat-Aufruf. Gibt den Antworttext zurueck ("" wenn leer).

    Raises:
        OllamaError: Transport-, Modell- oder Serverfehler.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    model = settings.OLLAMA_MODEL
    options: dict = {"temperature": temperature, "num_ctx": settings.OLLAMA_NUM_CTX}
    if num_predict:
        options["num_predict"] = num_predict
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": options,
        "keep_alive": settings.OLLAMA_KEEP_ALIVE,
    }
    if response_format:
        payload["format"] = response_format

    # "think" an Modelle senden, die es koennen — andere lehnen das Feld ab
    # (bzw. aeltere Ollama-Versionen kennen es nicht). Hybrid-Modelle wie
    # gemma4 "denken" ohne think=false standardmaessig tausende Tokens lang.
    # Sind die Capabilities unbekannt (/api/show gerade nicht erreichbar),
    # wird think trotzdem gesendet: eine Ablehnung faengt der Retry unten ab,
    # ein stillschweigend denkendes Modell dagegen sprengt jedes Timeout.
    capabilities = await model_capabilities(settings, model)
    if capabilities is None or "thinking" in capabilities:
        payload["think"] = settings.OLLAMA_THINK

    label = "Schema" if isinstance(response_format, dict) else ("JSON" if response_format else "Text")
    try:
        data = await post_json("/api/chat", payload, settings, timeout=timeout, purpose="llm")
    except OllamaError as e:
        if "think" in payload and e.kind == OllamaErrorKind.BAD_REQUEST and "think" in e.detail.lower():
            logger.warning("Ollama lehnt 'think' fuer %s ab — sende ohne", model)
            forget_capability(settings, model, "thinking")
            payload = {k: v for k, v in payload.items() if k != "think"}
            data = await post_json("/api/chat", payload, settings, timeout=timeout, purpose="llm")
        else:
            raise

    _log_metrics(data, settings, label)
    content = (data.get("message") or {}).get("content") or ""
    content = _THINK_BLOCK_RE.sub("", content).strip()
    if not content:
        logger.warning("LLM-%s-Antwort leer", label)
    return content


async def chat_json(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
    schema: dict | None = None,
) -> str:
    """JSON-Antwort von Ollama; mit ``schema`` als JSON-Schema-constrained Generation.

    Ollama meldet HTTP 200 auch bei schema-verletzender Ausgabe (leer, abgebrochen,
    Pflichtfeld fehlt). Dann wird EINMAL neu angefragt; bleibt die Antwort
    ungueltig, kommt sie trotzdem zurueck — die Aufrufer parsen tolerant.

    Returns:
        Antworttext ("" wenn Ollama nichts geliefert hat).

    Raises:
        OllamaError: bei Transport-, Modell- oder Serverfehlern (kein Retry
            ueber die Politik von ``post_json`` hinaus).
    """
    fmt: str | dict = schema if schema is not None else "json"
    content = ""
    for attempt in range(2):
        content = await _chat(
            prompt, settings,
            system_prompt=system_prompt,
            response_format=fmt,
            temperature=0.1,
            timeout=settings.OLLAMA_ANALYSIS_TIMEOUT,
            num_predict=settings.OLLAMA_NUM_PREDICT,
        )
        problems = _json_problems(content, schema)
        if not problems:
            return content
        logger.warning(
            "LLM-JSON-Antwort ungueltig (Versuch %d/2): %s",
            attempt + 1, "; ".join(problems[:5]),
        )
    return content


def _json_problems(content: str, schema: dict | None) -> list[str]:
    if not content:
        return ["leere Antwort"]
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return [f"kein gueltiges JSON ({e.msg})"]
    if schema is None:
        return [] if isinstance(data, dict) else ["kein JSON-Objekt"]
    return schema_violations(data, schema)


async def chat_text(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
) -> str:
    """Freitext-Antwort von Ollama ("" wenn leer). Raises OllamaError."""
    return await _chat(
        prompt, settings,
        system_prompt=system_prompt,
        response_format=None,
        temperature=0.3,
        timeout=settings.OLLAMA_TIMEOUT,
        num_predict=None,
    )


async def call_llm(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
    schema: dict | None = None,
) -> str | None:
    """Best-Effort-Variante von ``chat_json``: ``None`` bei Fehler oder leerer Antwort.

    Fuer Aufrufer, die ohne LLM sinnvoll weiterarbeiten (Reranker, Verifier,
    E-Mail-Relevanz). Wer die Fehlerart braucht, nutzt ``chat_json``.
    """
    try:
        return await chat_json(prompt, settings, system_prompt=system_prompt, schema=schema) or None
    except OllamaError:
        return None


async def call_llm_text(
    prompt: str,
    settings: Settings,
    system_prompt: str | None = None,
) -> str | None:
    """Best-Effort-Variante von ``chat_text``: ``None`` bei Fehler oder leerer Antwort."""
    try:
        return await chat_text(prompt, settings, system_prompt=system_prompt) or None
    except OllamaError:
        return None
