"""Docker-Release-Paket aus release-manifest.txt zusammenstellen.

release-manifest.txt (Repo-Root) ist die einzige Liste der Dateien, die ein
Docker-Release ausliefert. Frueher pflegten release.yml (ZIP) und setup.nsi
(Setup.exe + Uninstaller) eigene Listen; generate-mounts.ps1, ssl/ und
docker-compose.ssl.yml fehlten in beiden, sodass Host-Ordner und HTTPS bei
Release-Installationen nicht funktionierten.

Aufruf:
    python scripts/release_manifest.py check
        -> prueft, dass alle gelisteten Dateien existieren (Exit 1 sonst)
    python scripts/release_manifest.py stage <zielordner>
        -> kopiert die Dateien nach <zielordner> und schreibt
           <zielordner>/manifest.nsh mit den Makros ZW_INSTALL_FILES und
           ZW_UNINSTALL_FILES fuer setup.nsi
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "release-manifest.txt"


def read_manifest(path: Path = MANIFEST) -> list[PurePosixPath]:
    entries: list[PurePosixPath] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        p = PurePosixPath(line)
        if p.is_absolute() or ".." in p.parts or "\\" in line:
            raise ValueError(f"Ungueltiger Manifest-Eintrag (relativ, mit '/'): {line}")
        entries.append(p)
    if len(set(entries)) != len(entries):
        raise ValueError("Doppelte Eintraege in release-manifest.txt")
    return entries


def missing_files(entries: list[PurePosixPath], root: Path = REPO_ROOT) -> list[str]:
    return [str(e) for e in entries if not (root / Path(*e.parts)).is_file()]


def _win(p: PurePosixPath) -> str:
    return "\\".join(p.parts)


def render_nsh(entries: list[PurePosixPath]) -> str:
    """NSIS-Makros zum Installieren/Entfernen der Manifest-Dateien."""
    lines = [
        "; Automatisch erzeugt von scripts/release_manifest.py aus release-manifest.txt.",
        "; Nicht von Hand bearbeiten.",
        "",
        "!macro ZW_INSTALL_FILES",
    ]
    current_dir: PurePosixPath | None = None
    for e in entries:
        parent = e.parent
        if parent != current_dir:
            target = "$INSTDIR" if str(parent) == "." else f"$INSTDIR\\{_win(parent)}"
            lines.append(f'    SetOutPath "{target}"')
            current_dir = parent
        # Quellpfad mit Backslash: makensis unter Windows braucht ihn, die Linux-
        # Variante (Release-CI) wandelt Backslashes selbst in "/" um.
        lines.append(f'    File "{_win(e)}"')
    lines.append('    SetOutPath "$INSTDIR"')
    lines.append("!macroend")
    lines.append("")
    lines.append("!macro ZW_UNINSTALL_FILES")
    for e in entries:
        lines.append(f'    Delete "$INSTDIR\\{_win(e)}"')
    # Unterordner nur entfernen, wenn leer (z.B. bleiben selbst erzeugte
    # ssl\cert.pem/key.pem erhalten).
    subdirs = sorted({e.parent for e in entries if str(e.parent) != "."}, key=lambda d: -len(d.parts))
    for d in subdirs:
        lines.append(f'    RMDir "$INSTDIR\\{_win(d)}"')
    lines.append("!macroend")
    return "\r\n".join(lines) + "\r\n"


def stage(dest: Path, entries: list[PurePosixPath], root: Path = REPO_ROOT) -> None:
    missing = missing_files(entries, root)
    if missing:
        raise FileNotFoundError("Im Repo fehlen: " + ", ".join(missing))
    for e in entries:
        target = dest / Path(*e.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / Path(*e.parts), target)
    (dest / "manifest.nsh").write_bytes(render_nsh(entries).encode("utf-8"))


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("check", "stage") or (argv[0] == "stage" and len(argv) != 2):
        print(__doc__)
        return 2
    entries = read_manifest()
    if argv[0] == "check":
        missing = missing_files(entries)
        if missing:
            print("Fehlende Dateien: " + ", ".join(missing), file=sys.stderr)
            return 1
        print(f"OK: {len(entries)} Dateien")
        return 0
    stage(Path(argv[1]), entries)
    print(f"{len(entries)} Dateien nach {argv[1]} kopiert, manifest.nsh erzeugt")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
