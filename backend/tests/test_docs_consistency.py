"""Doku darf den Code-Defaults nicht widersprechen.

Regression: README nannte ``llama3.2``/``nomic-embed-text`` als Defaults und liess
die falschen Modelle pullen, waehrend config.py ``qwen2.5:7b-instruct``/``bge-m3``
nutzt — wer der Anleitung folgte, hatte kein Analyse-Modell. Wechselt ein
Default, muessen README, CLAUDE.md und die Beispiel-Konfigurationen mitziehen.
"""

import re
from pathlib import Path

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

MODEL_SETTINGS = ("OLLAMA_MODEL", "EMBEDDING_MODEL")

# Nur im Repo-Checkout sinnvoll (nicht in einem Image, das nur backend/ enthaelt).
pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "README.md").exists(), reason="kein Repo-Checkout"
)


def _default(name: str) -> str:
    return Settings.model_fields[name].default


def _table_default(markdown: str, name: str) -> str | None:
    """Default-Spalte der Markdown-Tabellenzeile ``| `NAME` | `wert` | ...``."""
    match = re.search(rf"^\|\s*`{name}`\s*\|\s*`([^`]*)`", markdown, re.MULTILINE)
    return match.group(1) if match else None


@pytest.mark.parametrize("doc", ["README.md", "CLAUDE.md"])
@pytest.mark.parametrize("name", MODEL_SETTINGS)
def test_documented_model_defaults_match_settings(doc: str, name: str):
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    documented = _table_default(text, name)
    if documented is None:
        pytest.skip(f"{doc} dokumentiert {name} nicht in einer Tabelle")
    assert documented == _default(name)


def test_readme_pull_commands_use_configured_tags():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    pulled = set(re.findall(r"ollama pull (\S+)", text))
    assert pulled, "README enthaelt keine Pull-Befehle mehr — Test anpassen"
    assert pulled <= {_default(name) for name in MODEL_SETTINGS}


@pytest.mark.parametrize(
    ("example", "pattern"),
    [
        (".env.example", r"^{name}=(\S+)"),
        ("config.toml.example", r'^{name}\s*=\s*"([^"]+)"'),
    ],
)
def test_example_configs_use_settings_defaults(example: str, pattern: str):
    text = (REPO_ROOT / example).read_text(encoding="utf-8")
    for name in MODEL_SETTINGS:
        match = re.search(pattern.format(name=name), text, re.MULTILINE)
        if match:
            assert match.group(1) == _default(name), f"{example}: {name}"


def test_review_reports_live_in_docs_reviews():
    """Historische Review-/Audit-Berichte gehoeren nach docs/reviews/, nicht ins Root."""
    stray = sorted(
        p.name
        for p in REPO_ROOT.glob("*.md")
        if re.match(r"(CODE_REVIEW|SECURITY_AUDIT|LIBRARY_AUDIT|REVIEW_REPORT|LLM_OPTIMIZATION)", p.name)
    )
    assert stray == []
    assert (REPO_ROOT / "docs" / "reviews" / "README.md").exists()
