# PyInstaller Spec fuer Zettelwirtschaft-Backend (Native-Windows)
#
# Build:
#   cd backend
#   python -m PyInstaller zettelwirtschaft.spec --clean --noconfirm
#
# Output: dist/zettelwirtschaft-backend/ (onedir)
# Entry:  dist/zettelwirtschaft-backend/zettelwirtschaft-backend.exe

# noqa - PyInstaller-Spec ist kein normales Python-Modul

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --- Data-Files: nicht-Python-Assets in das Bundle packen ---
datas = [
    ("app/prompts/*.txt", "app/prompts"),
    ("alembic/versions/*.py", "alembic/versions"),
    ("alembic/env.py", "alembic"),
    ("alembic/script.py.mako", "alembic"),
    ("alembic.ini", "."),
    ("migrate.py", "."),
]

# ChromaDB hat Resource-Files (Settings-Templates etc.)
datas += collect_data_files("chromadb")

# pydantic + pydantic-settings (manche Versionen brauchen sub-modules)
datas += collect_data_files("pydantic_settings")

# certifi-Bundle fuer httpx
datas += collect_data_files("certifi")

# --- Hidden Imports: PyInstaller findet diese nicht automatisch ---
hiddenimports = [
    # Die App selbst: entrypoint.py startet uvicorn per Import-String
    # ("app.main:app") — den sieht die PyInstaller-Analyse nicht. Ohne diesen
    # Eintrag fehlen app.main + alle Router im Bundle und die Exe bricht mit
    # 'Could not import module "app.main"' ab (frueher nur zufaellig ueber den
    # --version-Import aus app.main mitgezogen).
    "app.main",

    # SQLAlchemy + aiosqlite
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "sqlalchemy.ext.asyncio",

    # FastAPI + uvicorn-Komponenten
    "uvicorn.logging",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    # uvicorn >= 0.50: "auto" waehlt die Sans-I/O-Implementierung (Legacy-
    # websockets_impl ist deprecated).
    "uvicorn.protocols.websockets.websockets_sansio_impl",
    "uvicorn.lifespan.on",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",

    # Alembic-Versionen — alle dynamisch geladen
    "alembic.runtime.migration",
    "alembic.script",
    "alembic.config",

    # ChromaDB-Embedding-Functions + Backends
    "chromadb.utils.embedding_functions",
    "chromadb.api.segment",
    "chromadb.db.impl.sqlite",
    "chromadb.segment.impl.metadata.sqlite",
    "chromadb.segment.impl.vector.local_persistent_hnsw",
    "chromadb.segment.impl.vector.local_hnsw",
    "chromadb.execution.executor.local",

    # PDF / OCR
    "pdf2image",
    "pdfplumber",
    "pytesseract",

    # Crypto / Email
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.fernet",
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.application",

    # Globales Rate-Limit (app/core/rate_limit.py): limits-Package, Memory-
    # Backend + Moving-Window — statisch importiert, Daten sammelt der
    # pyinstaller-hooks-contrib-Hook "hook-limits".
    "limits.storage",
    "limits.strategies",

    # Zeitzonen-Daten fuer TIMEZONE (E-Mail-CRON in Ortszeit): Windows hat
    # keine System-tzdata, zoneinfo laedt das tzdata-Package erst zur Laufzeit
    # (fuer die Analyse unsichtbar). Der Eintrag triggert "hook-tzdata", der
    # Daten + Unterpakete (zoneinfo.Europe ...) einsammelt — sonst faellt
    # TIMEZONE im Bundle still auf UTC zurueck.
    "tzdata",
]

# Submodules die manchmal lazy-loaded sind
hiddenimports += collect_submodules("chromadb")
# Alle App-Module: die Migrationen unter alembic/versions/ werden als datas zur
# Laufzeit geladen; ihre Imports (z.B. app.core.sqlite_migration) sieht die
# PyInstaller-Analyse nicht -> sonst ModuleNotFoundError bei --migrate-only.
hiddenimports += collect_submodules("app")
# Die App-Migrationen (alembic/versions/*.py) sind kein Paket: sie liegen als
# datas im Bundle (oben) und werden von alembic/env.py zur Laufzeit geladen.

# --- Analyse ---
a = Analysis(
    ["app/entrypoint.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Tests + Dev-Pakete raus, sparen ~50 MB
        "pytest",
        "pytest_asyncio",
        "playwright",
        "IPython",
        "jupyter",
        "matplotlib",
        "tornado",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Wichtig: onedir-Mode (kein --onefile). Das gibt:
#   1. Schnelleren Startup (kein Selbst-Entpacken bei jedem Start)
#   2. Weniger Antivirus-False-Positives (PyInstaller-Stub ist signaturbekannt)
#   3. Einfacheres Updaten (Files austauschen ohne kompletten Rebuild)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="zettelwirtschaft-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX triggert vermehrt Antivirus
    console=True,  # Service-Wrapper (NSSM) braucht Konsole fuer stdout/stderr
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="zettelwirtschaft.ico",  # TODO: Phase 4
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="zettelwirtschaft-backend",
)
