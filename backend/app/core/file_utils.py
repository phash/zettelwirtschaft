import re
import uuid
from pathlib import Path

# Magic Bytes fuer erlaubte Dateitypen
MAGIC_BYTES: dict[str, list[bytes]] = {
    "pdf": [b"%PDF"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "tiff": [b"II\x2a\x00", b"MM\x00\x2a"],
    "bmp": [b"BM"],
}

# Maximale Laenge der zu pruefenden Bytes
_MAX_MAGIC_LEN = max(len(sig) for sigs in MAGIC_BYTES.values() for sig in sigs)


def validate_magic_bytes(path: Path, ext: str) -> bool:
    """Prueft ob die Magic Bytes einer Datei zur Extension passen."""
    ext_lower = ext.lower().lstrip(".")
    signatures = MAGIC_BYTES.get(ext_lower)
    if signatures is None:
        return False

    try:
        with open(path, "rb") as f:
            header = f.read(_MAX_MAGIC_LEN)
    except OSError:
        return False

    return any(header.startswith(sig) for sig in signatures)


def sanitize_filename(name: str) -> str:
    """Bereinigt einen Dateinamen fuer sichere Speicherung."""
    # Nur Basename verwenden (Path Traversal verhindern)
    name = Path(name).name
    # Ungueltige Zeichen ersetzen
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Mehrfache Unterstriche zusammenfassen
    name = re.sub(r"_+", "_", name)
    # Fuehrende/nachfolgende Punkte und Leerzeichen entfernen
    name = name.strip(". ")
    return name or "unnamed"


def generate_stored_filename(original_name: str) -> str:
    """Generiert einen eindeutigen Dateinamen mit UUID-Prefix."""
    sanitized = sanitize_filename(original_name)
    prefix = uuid.uuid4().hex[:12]
    return f"{prefix}_{sanitized}"


def get_file_extension(name: str) -> str:
    """Gibt die Extension einer Datei in Kleinbuchstaben zurueck (ohne Punkt)."""
    ext = Path(name).suffix.lower().lstrip(".")
    return ext
