"""Doku darf den Code-Defaults nicht widersprechen.

Regression: README nannte ``llama3.2``/``nomic-embed-text`` als Defaults und liess
die falschen Modelle pullen, waehrend config.py andere Modelle nutzte — wer der
Anleitung folgte, hatte kein Analyse-Modell. Wechselt ein Default, muessen
README, CLAUDE.md, die Beispiel-Konfigurationen und die Installer
(scripts/model-tiers.ps1) mitziehen.
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


def _ps_var(text: str, name: str) -> str | None:
    """Wert einer ``$Name = 'wert'``-Zuweisung in scripts/model-tiers.ps1."""
    match = re.search(rf"^\${name}\s*=\s*'([^']*)'", text, re.MULTILINE)
    return match.group(1) if match else None


def test_installer_model_tiers_match_settings_defaults():
    """Die Installer (scripts/model-tiers.ps1) schlagen dieselben Defaults vor wie config.py."""
    text = (REPO_ROOT / "scripts" / "model-tiers.ps1").read_text(encoding="utf-8-sig")
    assert _ps_var(text, "ZwLlmDefaultModel") == _default("OLLAMA_MODEL")
    assert _ps_var(text, "ZwEmbeddingDefaultModel") == _default("EMBEDDING_MODEL")
    # Bisherige Defaults (bis v1.5): Fallback fuer Bestands-.env ohne Eintrag.
    assert _ps_var(text, "ZwLegacyLlmModel") == "qwen2.5:7b-instruct"
    assert _ps_var(text, "ZwLegacyEmbeddingModel") == "bge-m3"


def test_ollama_min_version_is_consistent():
    """Mindestversion: Backend-Health, Installer-Helfer und Native-Setup gleich."""
    from app.services.ollama_client import OLLAMA_MIN_VERSION, OLLAMA_TESTED_VERSION

    tiers = (REPO_ROOT / "scripts" / "model-tiers.ps1").read_text(encoding="utf-8-sig")
    assert _ps_var(tiers, "ZwOllamaMinVersion") == OLLAMA_MIN_VERSION
    assert _ps_var(tiers, "ZwOllamaTestedVersion") == OLLAMA_TESTED_VERSION

    nsi = (REPO_ROOT / "setup-native.nsi").read_text(encoding="utf-8-sig")
    assert re.search(rf'^!define OLLAMA_MIN_VERSION "{re.escape(OLLAMA_MIN_VERSION)}"', nsi, re.MULTILINE)
    assert re.search(rf'^!define OLLAMA_TESTED_VERSION "{re.escape(OLLAMA_TESTED_VERSION)}"', nsi, re.MULTILINE)

    # Das gepinnte Docker-Image ist die getestete Version.
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"image: ollama/ollama:{OLLAMA_TESTED_VERSION}" in compose


def test_review_reports_live_in_docs_reviews():
    """Historische Review-/Audit-Berichte gehoeren nach docs/reviews/, nicht ins Root."""
    stray = sorted(
        p.name
        for p in REPO_ROOT.glob("*.md")
        if re.match(r"(CODE_REVIEW|SECURITY_AUDIT|LIBRARY_AUDIT|REVIEW_REPORT|LLM_OPTIMIZATION)", p.name)
    )
    assert stray == []
    assert (REPO_ROOT / "docs" / "reviews" / "README.md").exists()
