"""Installierte App-Version ermitteln (Single Source fuer alle Backend-Stellen).

Quellen, in dieser Reihenfolge:
1. ``VERSION`` im Arbeitsverzeichnis (Dev: Repo-Root als CWD).
2. ``data/.version`` im Arbeitsverzeichnis (Docker: vom Installer geschrieben,
   ueber das ``./data:/app/data``-Volume sichtbar; das Backend-Image selbst
   enthaelt keine VERSION-Datei, weil der Build-Context nur ``backend/`` ist).
3. Native (PyInstaller-Onedir): ``<install>/VERSION``. Die Exe liegt unter
   ``<install>/backend/``, NSSM setzt das CWD auf ``<install>/backend`` — die
   relativen Pfade 1+2 greifen dort nicht, daher explizit relativ zur Exe.
"""

import sys
from pathlib import Path

UNKNOWN_VERSION = "unknown"


def _candidate_paths() -> list[Path]:
    paths = [Path("VERSION"), Path("data/.version")]
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent.parent / "VERSION")
    return paths


def read_app_version() -> str:
    """Erste nicht-leere Versionsangabe aus den bekannten Quellen, sonst ``unknown``."""
    for path in _candidate_paths():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return UNKNOWN_VERSION
