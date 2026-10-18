"""SEC-DATA-01: IMAPS muss Zertifikat und Hostnamen pruefen.

Ein echter TLS-Server auf 127.0.0.1 (Thread) spielt den Mailserver bzw. den
Angreifer im LAN. Geprueft wird, ob ``LOGIN`` (mit dem Klartext-Passwort)
beim Server ankommt.
"""

import datetime
import ipaddress
import socket
import ssl
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.services import email_fetch_service
from app.services.email_fetch_service import (
    TLS_CERT_FAILED_MESSAGE,
    EmailFetchError,
    _connect_imap,
    classify_imap_error,
    imap_ssl_context,
)
from tests.credentials import TEST_IMAP_PASSWORD

PASSWORD = TEST_IMAP_PASSWORD


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject_cn, key, issuer_cn, issuer_key, *, ca=False, san=None) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject_cn))
        .issuer_name(_name(issuer_cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        # Python 3.13 setzt VERIFY_X509_STRICT -> AKI/SKI Pflicht.
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False
        )
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def _pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> SimpleNamespace:
    """Eigene CA + Server-Zertifikate (127.0.0.1 passend / fremder Name / selbstsigniert)."""
    tmp: Path = tmp_path_factory.mktemp("pki")
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("Test-CA", ca_key, "Test-CA", ca_key, ca=True)
    (tmp / "ca.pem").write_bytes(_pem(ca))

    def leaf(name: str, san, issuer=(ca, ca_key)) -> tuple[Path, Path]:
        key = ec.generate_private_key(ec.SECP256R1())
        issuer_cert, issuer_key = issuer if issuer else (None, key)
        issuer_cn = issuer_cert.subject.rfc4514_string().split("=", 1)[1] if issuer_cert else name
        cert = _cert(name, key, issuer_cn, issuer_key, san=san)
        (tmp / f"{name}.pem").write_bytes(_pem(cert))
        (tmp / f"{name}.key").write_bytes(_key_pem(key))
        return tmp / f"{name}.pem", tmp / f"{name}.key"

    return SimpleNamespace(
        ca=tmp / "ca.pem",
        good=leaf("mail-ok", [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
        wrong_host=leaf("mail-other", [x509.DNSName("imap.other.example")]),
        self_signed=leaf("evil.attacker", [x509.DNSName("evil.attacker")], issuer=None),
    )


class _TlsImapServer:
    """Minimaler IMAPS-Server: Greeting, CAPABILITY, LOGIN (protokolliert)."""

    def __init__(self, cert_chain: tuple[Path, Path]):
        self.logins: list[str] = []
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(*cert_chain)
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(10)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            raw, _ = self._sock.accept()
        except OSError:
            return
        try:
            conn = self._ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            raw.close()  # Client hat den Handshake abgebrochen (erwartet)
            return
        with conn, conn.makefile("rwb") as f:
            f.write(b"* OK IMAP4rev1 ready\r\n")
            f.flush()
            for line in iter(f.readline, b""):
                tag, _, rest = line.strip().partition(b" ")
                cmd = rest.split(b" ", 1)[0].upper()
                if cmd == b"CAPABILITY":
                    f.write(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + tag + b" OK done\r\n")
                elif cmd == b"LOGIN":
                    self.logins.append(line.decode(errors="replace"))
                    f.write(tag + b" OK logged in\r\n")
                elif cmd == b"LOGOUT":
                    f.write(b"* BYE\r\n" + tag + b" OK\r\n")
                    f.flush()
                    return
                else:
                    f.write(tag + b" OK\r\n")
                f.flush()

    def close(self) -> None:
        self._sock.close()
        self._thread.join(timeout=5)


def _account(port: int) -> SimpleNamespace:
    return SimpleNamespace(imap_host="127.0.0.1", imap_port=port, use_ssl=True, username="victim@gmx.de")


@pytest.fixture
def trust_test_ca(pki, monkeypatch):
    """Wie imap_ssl_context, aber mit der Test-CA als einzigem Vertrauensanker."""
    def context() -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(pki.ca))

    monkeypatch.setattr(email_fetch_service, "imap_ssl_context", context)


def test_default_context_verifies_certificate_and_hostname():
    imap_ssl_context.cache_clear()
    ctx = imap_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.cert_store_stats()["x509_ca"] > 0  # System-Store/certifi geladen


def test_self_signed_mitm_certificate_gets_no_password(pki):
    """Repro der Finding: selbstsigniertes Angreifer-Zertifikat, Default-Kontext."""
    server = _TlsImapServer(pki.self_signed)
    try:
        with pytest.raises(EmailFetchError) as exc_info:
            _connect_imap(_account(server.port), PASSWORD, timeout=5)
    finally:
        server.close()
    assert exc_info.value.message == TLS_CERT_FAILED_MESSAGE
    assert server.logins == []


def test_hostname_mismatch_gets_no_password(pki, trust_test_ca):
    """Gueltige Kette, aber Zertifikat fuer einen anderen Namen."""
    server = _TlsImapServer(pki.wrong_host)
    try:
        with pytest.raises(EmailFetchError):
            _connect_imap(_account(server.port), PASSWORD, timeout=5)
    finally:
        server.close()
    assert server.logins == []


def test_trusted_matching_certificate_logs_in(pki, trust_test_ca):
    server = _TlsImapServer(pki.good)
    try:
        conn = _connect_imap(_account(server.port), PASSWORD, timeout=5)
        conn.logout()
    finally:
        server.close()
    assert len(server.logins) == 1 and PASSWORD in server.logins[0]


def test_cert_error_is_classified_for_the_ui():
    err = ssl.SSLCertVerificationError(1, "certificate verify failed")
    assert classify_imap_error(err) == TLS_CERT_FAILED_MESSAGE
