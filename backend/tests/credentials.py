"""Zentrale Test-Zugangsdaten (PIN, IMAP-/SMB-Passwoerter, Fernet-Key).

Im Repo stehen bewusst KEINE Credential-Literale (Secret-Scanner wie GitGuardian
melden sonst "Generic Password"). Die Werte kommen aus genau einer Quelle:

1. Umgebungsvariable gleichen Namens (z.B. ``TEST_PIN``) — fuer CI/ad-hoc,
2. ``backend/.env.test`` (gitignored, Vorlage: ``backend/.env.test.example``),
3. sonst einmal pro Test-Session zufaellig erzeugt (``secrets`` / Fernet).

Alle Tests importieren die Werte von hier — nie selbst Passwoerter/PINs tippen.
Absichtlich ungueltige Eingaben (zu kurzer PIN, falscher Key) sind keine
Credentials und bleiben als sprechende Literale in den Tests.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values

ENV_TEST_FILE = Path(__file__).resolve().parent.parent / ".env.test"

# Der Installer erzeugt 6-stellige PINs; die App verlangt >= 4 Zeichen.
_PIN_LENGTH = 6
_MIN_PIN_LENGTH = 4

_file_values: dict[str, str | None] = dotenv_values(ENV_TEST_FILE) if ENV_TEST_FILE.is_file() else {}


def _value(name: str, generate) -> str:
    value = os.environ.get(name) or _file_values.get(name)
    return value.strip() if value and value.strip() else generate()


def _random_pin() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(_PIN_LENGTH))


def _random_password() -> str:
    return secrets.token_urlsafe(18)


def _random_fernet_key() -> str:
    return Fernet.generate_key().decode()


TEST_PIN = _value("TEST_PIN", _random_pin)
TEST_IMAP_PASSWORD = _value("TEST_IMAP_PASSWORD", _random_password)
TEST_IMAP_PASSWORD_2 = _value("TEST_IMAP_PASSWORD_2", _random_password)
TEST_EMAIL_ENCRYPTION_KEY = _value("TEST_EMAIL_ENCRYPTION_KEY", _random_fernet_key)
# Dummy-Zugangsdaten einer (gefaelschten) SMB-Freigabe fuer Netzwerkordner-Tests.
TEST_SMB_USER = _value("TEST_SMB_USER", lambda: "zw-test-" + secrets.token_hex(4))
TEST_SMB_PASSWORD = _value("TEST_SMB_PASSWORD", _random_password)
TEST_SMB_PASSWORD_2 = _value("TEST_SMB_PASSWORD_2", _random_password)


def _validate() -> None:
    source = f"(Umgebung oder {ENV_TEST_FILE})"
    if not (TEST_PIN.isdigit() and len(TEST_PIN) >= _MIN_PIN_LENGTH):
        raise RuntimeError(f"TEST_PIN muss aus mindestens {_MIN_PIN_LENGTH} Ziffern bestehen {source}")
    if TEST_IMAP_PASSWORD == TEST_IMAP_PASSWORD_2:
        raise RuntimeError(f"TEST_IMAP_PASSWORD und TEST_IMAP_PASSWORD_2 muessen sich unterscheiden {source}")
    if TEST_SMB_PASSWORD == TEST_SMB_PASSWORD_2:
        raise RuntimeError(f"TEST_SMB_PASSWORD und TEST_SMB_PASSWORD_2 muessen sich unterscheiden {source}")
    # Sentinel-Tests suchen das Passwort in Logs/Antworten — es muss eindeutig sein.
    if len(TEST_SMB_PASSWORD) < 12:
        raise RuntimeError(f"TEST_SMB_PASSWORD muss mindestens 12 Zeichen lang sein {source}")
    try:
        Fernet(TEST_EMAIL_ENCRYPTION_KEY)
    except ValueError as exc:
        raise RuntimeError(f"TEST_EMAIL_ENCRYPTION_KEY ist kein gueltiger Fernet-Key {source}") from exc


_validate()

# Garantiert falscher PIN gleicher Laenge: jede Ziffer um 1 verschoben.
TEST_WRONG_PIN = "".join(str((int(c) + 1) % 10) for c in TEST_PIN)
