"""Programmordner der Native-Installation (PyInstaller-Onedir-Bundle).

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
