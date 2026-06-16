"""Test fuer den selbstsignierten Zertifikat-Generator (ssl/generate-self-signed-cert.py).

Skippt, wenn die ssl/-Datei nicht erreichbar ist (z.B. wenn nur backend/ ohne
Repo-Root gemountet ist). Im vollen Checkout (ci-local) laeuft er.
"""

import importlib.util
from pathlib import Path

import pytest

GEN = Path(__file__).resolve().parents[3] / "ssl" / "generate-self-signed-cert.py"


@pytest.mark.skipif(not GEN.exists(), reason="ssl/generate-self-signed-cert.py nicht im Kontext")
def test_generates_valid_server_cert(tmp_path):
    spec = importlib.util.spec_from_file_location("zw_certgen", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rc = mod.main(["192.168.1.50", str(tmp_path)])
    assert rc == 0

    cert_pem = tmp_path / "cert.pem"
    key_pem = tmp_path / "key.pem"
    assert cert_pem.exists() and key_pem.exists()

    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID

    cert = x509.load_pem_x509_certificate(cert_pem.read_bytes())

    # serverAuth-EKU (sonst akzeptieren Browser das Cert nicht als Server-Cert)
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku

    # SAN: localhost + 127.0.0.1 + die uebergebene IP
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = san.get_values_for_type(x509.DNSName)
    ips = [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    assert "localhost" in dns
    assert "127.0.0.1" in ips
    assert "192.168.1.50" in ips

    # End-Entity (CA:FALSE) + KeyUsage
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is False
    ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.digital_signature and ku.key_encipherment

    # RSA-2048, ~10 Jahre gueltig
    assert isinstance(cert.public_key(), rsa.RSAPublicKey)
    assert cert.public_key().key_size == 2048
    span = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert span.days > 3640

    # Private Key owner-only (0600), oeffentliches Cert 0644 — kein world-readable Key
    import sys

    if sys.platform != "win32":
        assert (key_pem.stat().st_mode & 0o777) == 0o600
        assert (cert_pem.stat().st_mode & 0o777) == 0o644


@pytest.mark.skipif(not GEN.exists(), reason="ssl/generate-self-signed-cert.py nicht im Kontext")
def test_generates_without_ip_argument(tmp_path):
    """Ohne IP-Argument: Auto-Detect, aber localhost/127.0.0.1 immer vorhanden."""
    spec = importlib.util.spec_from_file_location("zw_certgen2", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rc = mod.main([str(tmp_path)]) if False else mod.main(["", str(tmp_path)])
    assert rc == 0
    from cryptography import x509

    cert = x509.load_pem_x509_certificate((tmp_path / "cert.pem").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert "127.0.0.1" in [str(i) for i in san.get_values_for_type(x509.IPAddress)]
