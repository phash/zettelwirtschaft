"""Zettelwirtschaft — Generator fuer ein selbstsigniertes Server-Zertifikat.

Erzeugt cert.pem + key.pem fuer Installationen im Heim-WLAN, in denen kein
echtes CA-Zertifikat verfuegbar ist. Das Zertifikat wird gebraucht, damit der
**Smartphone-Scan** (Kamera-API `getUserMedia`) und der **PWA-Service-Worker**
funktionieren — beide verlangen einen "Secure Context" (HTTPS oder localhost);
ueber `http://<rechner>:8080` verweigert der Browser die Kamera.

Eigenschaften (analog praxiszeit v1.8.7):
- 2048-bit RSA, SHA-256, 10 Jahre gueltig.
- End-Entity-Server-Zertifikat: BasicConstraints CA:FALSE (critical),
  KeyUsage digitalSignature+keyEncipherment, ExtendedKeyUsage **serverAuth**.
  Ohne serverAuth-EKU akzeptieren moderne Browser das Cert nicht als
  Server-Zertifikat.
- SubjectAltName: DNS:localhost, IP:127.0.0.1, erkannte LAN-IP(s), Hostname.

Nutzt das `cryptography`-Paket (bereits in den Backend-Requirements — der
gebundelte Python-Interpreter im Native-Bundle kann das Script direkt
ausfuehren, kein openssl noetig).

Aufruf:
    python generate-self-signed-cert.py [<ip>] [<out_dir>]

Ohne <ip> wird die primaere LAN-IP automatisch erkannt. <out_dir> ist
standardmaessig das Verzeichnis dieses Scripts (ssl/).
"""

from __future__ import annotations

import ipaddress
import os
import platform
import socket
import sys
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def detect_lan_ip() -> str | None:
    """Primaere ausgehende IPv4 ermitteln, ohne tatsaechlich Pakete zu senden."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    ip = argv[0] if argv else (detect_lan_ip() or "")
    out_dir = argv[1] if len(argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)

    # SAN-Liste aufbauen: localhost + Loopback + erkannte/uebergebene IP + Hostname
    sans: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    if ip:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            print(f"WARN: {ip!r} ist keine gueltige IP — wird uebersprungen", file=sys.stderr)
            ip = ""
    hostname = socket.gethostname()
    if hostname and hostname.lower() != "localhost":
        sans.append(x509.DNSName(hostname))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cn = hostname or ip or "zettelwirtschaft"
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Zettelwirtschaft"),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path = os.path.join(out_dir, "cert.pem")
    key_path = os.path.join(out_dir, "key.pem")

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

    # Der Docker-Frontend-Container laeuft als non-root (USER nginx). Damit der
    # gemountete Key lesbar ist, 0644 statt 0600 — vertretbar fuer ein
    # selbstsigniertes LAN-Zertifikat auf demselben Host. (Native: ACL erbt.)
    if platform.system() != "Windows":
        os.chmod(key_path, 0o644)
        os.chmod(cert_path, 0o644)

    san_repr = ", ".join(
        ("localhost", "127.0.0.1", *( (ip,) if ip else () ), *((hostname,) if hostname and hostname.lower() != "localhost" else ()))
    )
    print(f"OK cert: {cert_path}")
    print(f"OK key:  {key_path}")
    print(f"CN={cn}  SAN={san_repr}")
    print("Gueltig 10 Jahre, serverAuth-EKU + SAN (von Browsern als Server-Cert akzeptiert).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
