"""Bundled Binary-Pfade fuer Native-Windows-Build.

Wenn das Backend als PyInstaller-Onedir-Bundle laeuft, sind Tesseract + poppler
im Geschwisterordner `bin/` neben dem `backend/`-Bundle:

    C:\\Programme\\Zettelwirtschaft\\
    +-- backend\\
    |   +-- zettelwirtschaft-backend.exe  (sys.executable bei frozen)
    |   +-- _internal\\, app\\, ...
    +-- bin\\
        +-- tesseract.exe
        +-- tessdata\\ (deu.traineddata, eng.traineddata)
        +-- poppler\\
            +-- Library\\bin\\pdftoppm.exe etc.

Dieser Modul-Import muss SEHR FRUEH erfolgen (vor pdf2image / pytesseract),
damit die Pfade bekannt sind.

Ausserhalb des Bundles (Dev/Docker) greift nur ein ``<repo>/bin/`` mit
``tesseract.exe`` (lokaler Test des Bundles auf Windows); sonst ist das Modul
ein No-Op und der System-PATH gilt. Im Docker-Image waere ``<repo>`` = ``/``
und ``/bin`` existiert immer — deshalb zaehlt ein Ordner nur mit tesseract.exe.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.core.install_dir import native_install_dir

logger = logging.getLogger("zettelwirtschaft.bin_paths")


def _is_bundle_bin_dir(candidate: Path) -> bool:
    return (candidate / "tesseract.exe").is_file()


def _find_bin_dir() -> Path | None:
    """Ermittelt das gebundelte `bin/`-Verzeichnis (nur mit tesseract.exe)."""
    install_dir = native_install_dir()
    if install_dir is not None:
        candidate = install_dir / "bin"
        if _is_bundle_bin_dir(candidate):
            return candidate
    # Dev-Mode: schaue ob <repo>/bin/ existiert (z.B. fuer lokale Tests des Bundles)
    repo_bin = Path(__file__).resolve().parents[2] / "bin"
    if _is_bundle_bin_dir(repo_bin):
        return repo_bin
    return None


def configure_bundled_binaries() -> None:
    """PATH + pytesseract + pdf2image-poppler auf gebundelte Binaries zeigen.

    Idempotent. Wenn kein bin/-Ordner existiert, no-op (Docker/Linux-Pfad).
    """
    bin_dir = _find_bin_dir()
    if bin_dir is None:
        logger.debug("Kein gebundelter bin/-Ordner gefunden — System-PATH wird verwendet")
        return

    tesseract_exe = bin_dir / "tesseract.exe"
    tessdata_dir = bin_dir / "tessdata"
    poppler_bin = bin_dir / "poppler" / "Library" / "bin"

    # PATH erweitern (vorne anhaengen, damit unsere Versionen Vorrang haben)
    extra_paths = [str(bin_dir)]
    if poppler_bin.exists():
        extra_paths.append(str(poppler_bin))
    os.environ["PATH"] = os.pathsep.join(extra_paths + [os.environ.get("PATH", "")])

    # tesseract_cmd direkt setzen — pytesseract sucht sonst im PATH und kann
    # auf einer System-Installation landen (falsche Sprachpakete).
    if tesseract_exe.exists():
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_exe)
            logger.info("Tesseract gebundelt: %s", tesseract_exe)
        except ImportError:
            pass

    if tessdata_dir.exists():
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)

    # poppler-Pfad fuer pdf2image: pdf2image akzeptiert poppler_path als Param,
    # nutzt aber bei None den PATH — den haben wir oben erweitert.
    # Trotzdem als ENV setzen falls jemand spaeter explizit liest.
    if poppler_bin.exists():
        os.environ["POPPLER_PATH"] = str(poppler_bin)
        logger.info("poppler gebundelt: %s", poppler_bin)
