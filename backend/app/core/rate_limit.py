"""Globaler slowapi-Limiter — zentral importierbar fuer Endpoint-Decoratoren.

Wird aus main.py beim FastAPI-App-Setup als app.state.limiter registriert.
Endpoints die das globale Default-Limit (200/min) nicht treffen sollen
(z.B. Health-Probes von docker-compose) koennen `@limiter.exempt` nutzen.
"""

import ipaddress
import logging

from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

# NEW-002 (SECURITY_AUDIT_v3): X-Real-IP nur trusten wenn die Verbindung
# selbst aus einem Trusted-Proxy-Netz kommt. In unserer Docker-Topologie
# ist das die compose-interne Bridge (172.16/12) — nginx leitet von dort
# weiter. Wenn jemand das Backend je direkt von ausserhalb erreichen kann,
# wird der Header ignoriert und der echte Socket-Peer benutzt.
_TRUSTED_PROXY_NETWORKS = [
    ipaddress.IPv4Network("127.0.0.0/8"),     # localhost
    ipaddress.IPv4Network("172.16.0.0/12"),   # Docker default + compose
    ipaddress.IPv4Network("10.0.0.0/8"),      # alternative Docker-Setups
    ipaddress.IPv4Network("192.168.0.0/16"),  # bei host-network-Setups
]


def _is_trusted_proxy(host: str) -> bool:
    try:
        return any(ipaddress.ip_address(host) in net for net in _TRUSTED_PROXY_NETWORKS)
    except (ValueError, TypeError):
        return False


def trusted_client_ip(request) -> str:
    """Effektive Client-IP unter Beruecksichtigung von X-Real-IP-Trust.

    Wird sowohl von slowapi als auch vom auth.py-Limiter benutzt — beide
    sollten dieselbe Logik teilen, sonst kann der Pin-Login-Bypass von
    SECURITY_AUDIT_v3 NEW-002 wieder reinkommen.
    """
    socket_host = get_remote_address(request)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip and _is_trusted_proxy(socket_host):
        return real_ip
    if real_ip and not _is_trusted_proxy(socket_host):
        # Verdaechtige Konstellation: X-Real-IP gesetzt, aber Connection
        # nicht aus Trusted-Proxy-Netz. Loggen + ignorieren.
        logger.warning(
            "X-Real-IP=%s ignoriert (Connection aus untrusted Netz: %s)",
            real_ip, socket_host,
        )
    return socket_host


limiter = Limiter(key_func=trusted_client_ip, default_limits=["200/minute"])
