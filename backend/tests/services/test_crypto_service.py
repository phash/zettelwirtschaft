import pytest

from app.services.crypto_service import encrypt_password, decrypt_password, generate_encryption_key


def test_encrypt_decrypt_roundtrip():
    key = generate_encryption_key()
    password = "mein-geheimes-passwort"
    encrypted = encrypt_password(password, key)
    assert encrypted != password
    decrypted = decrypt_password(encrypted, key)
    assert decrypted == password


def test_encrypt_produces_different_ciphertexts():
    key = generate_encryption_key()
    enc1 = encrypt_password("test", key)
    enc2 = encrypt_password("test", key)
    assert enc1 != enc2


def test_decrypt_with_wrong_key_fails():
    key1 = generate_encryption_key()
    key2 = generate_encryption_key()
    encrypted = encrypt_password("test", key1)
    with pytest.raises(Exception):
        decrypt_password(encrypted, key2)


def test_empty_password():
    key = generate_encryption_key()
    encrypted = encrypt_password("", key)
    assert decrypt_password(encrypted, key) == ""
