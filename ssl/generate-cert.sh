#!/bin/bash
# Generiert ein selbstsigniertes SSL-Zertifikat fuer Zettelwirtschaft.
# Gueltig 10 Jahre, akzeptiert localhost + die lokale(n) LAN-IP(s).
#
# Wird gebraucht, damit der Smartphone-Scan (Kamera-API) und der
# PWA-Service-Worker im Heim-WLAN funktionieren — beide verlangen HTTPS.
#
# Nutzung:
#   bash ssl/generate-cert.sh
#   docker compose -f docker-compose.yml -f docker-compose.ssl.yml up -d
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Generiere selbstsigniertes SSL-Zertifikat..."

# Server-IP ermitteln
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Erkannte Server-IP: ${SERVER_IP:-<keine>}"

if command -v openssl >/dev/null 2>&1; then
    # SAN konditionell: leeres SERVER_IP (IPv6-only/loopback) wuerde openssl brechen.
    SAN="DNS:localhost,IP:127.0.0.1"
    [ -n "$SERVER_IP" ] && SAN="$SAN,IP:$SERVER_IP"
    HOST_FQDN=$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)
    [ -n "$HOST_FQDN" ] && [ "$HOST_FQDN" != "localhost" ] && SAN="$SAN,DNS:$HOST_FQDN"

    # End-Entity-Server-Zertifikat: CA:FALSE + keyUsage + extendedKeyUsage=serverAuth.
    # Ohne diese Extensions akzeptieren Browser das Cert nicht als Server-Cert.
    openssl req -x509 -nodes -days 3650 \
        -newkey rsa:2048 \
        -keyout "$SCRIPT_DIR/key.pem" \
        -out "$SCRIPT_DIR/cert.pem" \
        -subj "/O=Zettelwirtschaft/CN=${HOST_FQDN:-zettelwirtschaft}" \
        -addext "subjectAltName=$SAN" \
        -addext "basicConstraints=critical,CA:FALSE" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth"
else
    echo "openssl nicht gefunden — nutze Python-Generator (cryptography)."
    python3 "$SCRIPT_DIR/generate-self-signed-cert.py" "$SERVER_IP" "$SCRIPT_DIR"
fi

# 0644 statt 0600: der non-root Frontend-Container (USER nginx) muss den
# gemounteten Key lesen koennen. Selbstsigniertes LAN-Cert auf demselben Host.
chmod 0644 "$SCRIPT_DIR/key.pem" "$SCRIPT_DIR/cert.pem" 2>/dev/null || true

echo ""
echo "Zertifikat erstellt:"
echo "  $SCRIPT_DIR/cert.pem"
echo "  $SCRIPT_DIR/key.pem"
echo ""
echo "Naechste Schritte:"
echo "  1. docker compose down"
echo "  2. docker compose -f docker-compose.yml -f docker-compose.ssl.yml up -d"
echo "  3. Am Smartphone https://${SERVER_IP:-<server-ip>}/ oeffnen"
echo "  4. Beim ersten Zugriff die Browser-Warnung einmalig akzeptieren"
