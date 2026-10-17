r"""Programmordner der Native-Installation (PyInstaller-Onedir-Bundle).

Layout (Setup.exe)::

    <install>\
    +-- backend\zettelwirtschaft-backend.exe   (sys.executable)
    +-- bin\                                   (Tesseract, poppler)
    +-- frontend\dist\
    +-- VERSION

Einzige Stelle, die den Ordner aus ``sys.executable`` ableitet (genutzt von
bin_paths, core.version und der Ordner-Validierung in api.system). Bewusst
ohne App-Imports: bin_paths laedt dieses Modul sehr frueh.
"""

import sys
from pathlib import Path


def native_install_dir() -> Path | None:
    """``<install>`` im gebundelten Betrieb, sonst ``None`` (Dev/Docker)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return None


def frontend_dist_dir(configured: str) -> Path | None:
    """Ordner, aus dem das Backend die Weboberflaeche ausliefert.

    INFRA-01: Im gebundelten Betrieb IMMER ``<install>/frontend`` — der
    Programmordner ist nur fuer Administratoren beschreibbar, die config.toml
    im (frei waehlbaren) Datenordner dagegen nicht zwingend. Ein
    ``FRONTEND_DIST_DIR = "C:/"`` aus einer manipulierten config.toml machte den
    SPA-Catch-All sonst zu einem Dateiserver fuer alles, was SYSTEM lesen darf
    (ohne PIN, die PIN-Middleware schuetzt nur ``/api``). Ausserhalb des Bundles
    (Dev, Tests) gilt der konfigurierte Wert; leer = nicht ausliefern.
    """
    install_dir = native_install_dir()
    if install_dir is not None:
        return install_dir / "frontend"
    return Path(configured) if configured else None
