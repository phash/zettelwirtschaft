"""Installierte App-Version ermitteln (Single Source fuer alle Backend-Stellen).

Quellen, in dieser Reihenfolge:
1. ``VERSION`` im Arbeitsverzeichnis (Start aus dem Repo-Root).
2. ``data/.version`` im Arbeitsverzeichnis (Docker: vom Installer geschrieben,
   ueber das ``./data:/app/data``-Volume sichtbar; das Backend-Image selbst
   enthaelt keine VERSION-Datei, weil der Build-Context nur ``backend/`` ist).
3. Native (PyInstaller-Onedir): ``<install>/VERSION``. Die Exe liegt unter
   ``<install>/backend/``, NSSM setzt das CWD auf ``<install>/backend`` — die
   relativen Pfade 1+2 greifen dort nicht, daher explizit relativ zur Exe.
4. Dev (nicht gebundelt): ``VERSION`` im Repo-Root relativ zu diesem Modul —
   der dokumentierte Dev-Start (``uvicorn app.main:app`` aus ``backend/``)
   hat weder 1 noch 2 im CWD.
"""

from pathlib import Path

from app.core.install_dir import native_install_dir

UNKNOWN_VERSION = "unknown"


def _candidate_paths() -> list[Path]:
    paths = [Path("VERSION"), Path("data/.version")]
    install_dir = native_install_dir()
    if install_dir is not None:
        paths.append(install_dir / "VERSION")
    else:
        # app/core/version.py -> <repo>/VERSION
        paths.append(Path(__file__).resolve().parents[3] / "VERSION")
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
