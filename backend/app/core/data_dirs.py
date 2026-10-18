r"""Datenverzeichnisse beim Start anlegen (Lifespan und Native-Entrypoint).

NET-SEC-06: Watch- und Export-Ordner aus config.toml/.env koennen
Netzwerkpfade (``\\server\freigabe``) sein. Ein ``mkdir`` darauf liefe ohne
eigene Verbindung — der Dienst (LocalSystem) meldete sich mit dem
Computerkonto am Server an. Sie werden deshalb hier nie angefasst; der
Watch-Task bzw. der Export legt sie erst nach dem Verbinden an. Ohne
Zugangsdaten (Weboberflaeche) und ohne ALLOW_NETWORK_FOLDERS werden sie zur
Laufzeit gar nicht verwendet — das meldet der Start im Log.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core import network_share


def ensure_startup_dirs(settings, logger: logging.Logger | None = None) -> list[str]:
    """Legt die lokalen Datenverzeichnisse an; liefert die angelegten Pfade."""
    log = logger or logging.getLogger("zettelwirtschaft")
    created: list[str] = []
    for dir_path in (settings.UPLOAD_DIR, settings.WATCH_DIR, settings.ARCHIVE_DIR, settings.THUMBNAIL_DIR,
                     str(settings.split_dir), settings.EXPORT_DIR):
        if not dir_path:
            continue
        if network_share.is_unc(dir_path) and dir_path in (settings.WATCH_DIR, settings.EXPORT_DIR):
            name = "WATCH_DIR" if dir_path == settings.WATCH_DIR else "EXPORT_DIR"
            hint = (
                "wird erst nach dem Verbinden angelegt" if settings.ALLOW_NETWORK_FOLDERS
                else "wird ohne Benutzer/Passwort der Freigabe (Weboberfläche) nicht verwendet"
            )
            log.warning("%s ist ein Netzwerkpfad (%s) – %s.", name, dir_path, hint)
            continue
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        created.append(dir_path)
    return created
