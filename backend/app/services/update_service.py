"""Update-Pruefung (opt-in).

Vergleicht die installierte Version mit einem veroeffentlichten Manifest
(``latest.json``). Telemetriefrei: es werden KEINE Nutzungs- oder Dokumentdaten
gesendet — es wird lediglich eine statische JSON-Datei per HTTP GET abgerufen.
Die Pruefung ist standardmaessig deaktiviert und muss in den Einstellungen
ausdruecklich aktiviert werden.
"""

import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Kleiner In-Memory-Cache, damit wiederholte UI-Aufrufe (Settings-Polling,
# Seitenwechsel) nicht jedes Mal den Update-Server kontaktieren.
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 Stunden
_cache: dict = {"at": 0.0, "data": None}


def read_local_version() -> str:
    """Installierte Version aus VERSION bzw. data/.version (analog main._read_version)."""
    for path in (Path("VERSION"), Path("data/.version")):
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except (FileNotFoundError, OSError):
            continue
    return "unknown"


def parse_version(value: str | None) -> tuple[int, ...] | None:
    """'1.4.0' / 'v1.4.0' / '1.4.0-beta1' -> (1, 4, 0). None wenn nicht parsebar."""
    if not value:
        return None
    v = value.strip().lstrip("vV")
    for sep in ("-", "+", " "):
        if sep in v:
            v = v.split(sep, 1)[0]
    parts = v.split(".")
    nums: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        nums.append(int(part))
    return tuple(nums) if nums else None


def is_newer(local: str | None, latest: str | None) -> bool:
    """True wenn ``latest`` echt groesser als ``local`` ist (Update verfuegbar)."""
    lv, rv = parse_version(local), parse_version(latest)
    if lv is None or rv is None:
        return False
    width = max(len(lv), len(rv))
    lv = lv + (0,) * (width - len(lv))
    rv = rv + (0,) * (width - len(rv))
    return rv > lv


def _empty_result(current: str) -> dict:
    return {
        "current_version": current,
        "latest_version": None,
        "update_available": False,
        "release_url": None,
        "published_at": None,
        "notes": None,
        "error": None,
        "cached": False,
    }


async def check_for_update(
    manifest_url: str, *, force: bool = False, timeout: float = 6.0
) -> dict:
    """Manifest abrufen und mit der lokalen Version vergleichen.

    Schlaegt nie fehl (graceful degradation): bei Netzwerk-/Parse-Fehlern wird
    ``error`` gesetzt statt eine Exception zu werfen.
    """
    current = read_local_version()
    now = time.monotonic()

    if not force and _cache["data"] is not None and (now - _cache["at"]) < _CACHE_TTL_SECONDS:
        cached = dict(_cache["data"])
        cached["current_version"] = current
        cached["cached"] = True
        cached["update_available"] = is_newer(current, cached.get("latest_version"))
        return cached

    result = _empty_result(current)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(manifest_url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            manifest = resp.json()
        latest = str(manifest.get("latest_version") or manifest.get("version") or "").strip()
        if not latest:
            result["error"] = "Manifest ohne Versionsangabe."
            return result
        result["latest_version"] = latest
        result["release_url"] = manifest.get("release_url")
        result["published_at"] = manifest.get("published_at")
        result["notes"] = manifest.get("notes")
        result["update_available"] = is_newer(current, latest)
        _cache["at"] = now
        _cache["data"] = dict(result)
    except Exception as exc:  # graceful: Update-Pruefung darf nie das System stoeren
        logger.warning("Update-Pruefung fehlgeschlagen: %s", exc)
        result["error"] = "Update-Server nicht erreichbar."
    return result


def _reset_cache() -> None:
    """Nur fuer Tests."""
    _cache["at"] = 0.0
    _cache["data"] = None
