"""Passwort-Verschluesselung fuer E-Mail-Konten via Fernet (AES-128-CBC)."""

from cryptography.fernet import Fernet


def generate_encryption_key() -> str:
    """Generiert einen neuen Fernet-Schluessel."""
    return Fernet.generate_key().decode()


def encrypt_password(password: str, key: str) -> str:
    """Verschluesselt ein Passwort mit dem gegebenen Fernet-Schluessel."""
    f = Fernet(key.encode())
    return f.encrypt(password.encode()).decode()


def decrypt_password(encrypted: str, key: str) -> str:
    """Entschluesselt ein Passwort mit dem gegebenen Fernet-Schluessel."""
    f = Fernet(key.encode())
    return f.decrypt(encrypted.encode()).decode()
