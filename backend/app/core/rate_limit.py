"""Client-IP-Ermittlung und globales Rate-Limit (200 Requests/Minute pro IP).

Das globale Limit laeuft als App-weite FastAPI-Dependency (siehe main.py:
``FastAPI(dependencies=[Depends(global_rate_limit)])``) — also NACH dem Routing,
mit dem aufgeloesten Endpoint in ``request.scope["endpoint"]``. Die fruehere
slowapi-Middleware fand ab FastAPI 0.143 (``_IncludedRouter`` ohne
``.endpoint``) keinen Handler mehr und liess damit jeden /api-Request ungeprueft
durch. Endpoints, die das Limit nicht treffen soll (Health-Probes von
docker-compose, SPA-Shell), markieren sich mit ``@limiter.exempt``.
"""

import ipaddress
import logging
import math
import time

from fastapi import HTTPException, Request
from limits import parse_many
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from app.config import Settings, settings_for_request

logger = logging.getLogger(__name__)

GLOBAL_RATE_LIMITS = "200/minute"


def is_trusted_proxy(host: str | None, settings: Settings) -> bool:
    """True wenn ``host`` (Socket-Peer) ein vertrauenswuerdiger Reverse-Proxy ist.

    NEW-002 (docs/reviews/SECURITY_AUDIT_v3.md): Nur im Docker-Betrieb und nur aus
    ``TRUSTED_PROXIES`` (Docker-Netze) — dort ist das Backend ausschliesslich
    ueber nginx erreichbar. Native/Dev haben keinen Proxy: uvicorn steht direkt
    im LAN, jeder LAN-Client waere sonst "Proxy" und koennte X-Real-IP frei
    rotieren (unbegrenzte PIN-Versuche).
    """
    networks = settings.trusted_proxy_networks
    if not host or not networks:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def trusted_client_ip(request: Request, settings: Settings | None = None) -> str:
    """Effektive Client-IP: X-Real-IP nur von einem vertrauenswuerdigen Proxy.

    Gemeinsamer Schluessel fuer das globale Rate-Limit und den PIN-Lockout
    (auth.py) — beide muessen dieselbe Logik teilen, sonst kommt der
    Pin-Login-Bypass aus NEW-002 wieder herein. nginx setzt X-Real-IP auf
    ``$remote_addr`` (Header des Clients werden ueberschrieben).
    """
    if settings is None:
        settings = settings_for_request(request)
    socket_host = request.client.host if request.client else "unknown"
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if not real_ip:
        return socket_host
    if not is_trusted_proxy(socket_host, settings):
        # Header von einem Nicht-Proxy (Native/Dev oder fremdes Netz): ignorieren.
        logger.debug("X-Real-IP=%s ignoriert (kein vertrauenswuerdiger Proxy: %s)", real_ip, socket_host)
        return socket_host
    try:
        addr = ipaddress.ip_address(real_ip)
    except ValueError:
        logger.warning("Ungueltige X-Real-IP %r vom Proxy %s ignoriert", real_ip, socket_host)
        return socket_host
    _warn_if_source_ip_lost(addr, settings)
    return str(addr)


# AUTH-04: Docker Desktop (Windows/macOS) ersetzt bei veroeffentlichten Ports die
# Client-Adresse je nach Netzwerkmodus durch seine Gateway-Adresse. Dann teilen
# sich ALLE Geraete einen Lockout-/Rate-Limit-Schluessel (ein Gast mit falschem
# PIN sperrt alle aus). Nicht sicher erkennbar — aber eine "Client"-IP aus den
# Docker-eigenen Netzen ist ein starkes Indiz; einmalig warnen, damit es auf
# einer echten Installation auffaellt.
DOCKER_DESKTOP_NETWORKS = (ipaddress.ip_network("192.168.65.0/24"),)
_source_ip_warning_logged = False


def looks_like_nat_gateway(addr: ipaddress.IPv4Address | ipaddress.IPv6Address, settings: Settings) -> bool:
    networks = [*settings.trusted_proxy_networks, *DOCKER_DESKTOP_NETWORKS]
    return any(addr in net for net in networks if addr.version == net.version)


def _warn_if_source_ip_lost(addr: ipaddress.IPv4Address | ipaddress.IPv6Address, settings: Settings) -> None:
    global _source_ip_warning_logged
    if _source_ip_warning_logged or not looks_like_nat_gateway(addr, settings):
        return
    _source_ip_warning_logged = True
    logger.warning(
        "Client-IP %s stammt aus einem Docker-Netz — vermutlich ersetzt Docker Desktop die "
        "echte Geraete-Adresse durch sein Gateway. PIN-Sperre und Rate-Limit gelten dann fuer "
        "ALLE Geraete gemeinsam (eine Serie falscher PINs sperrt alle aus). Abhilfe: "
        "Native-Installation verwenden oder Docker-Netzwerkmodus mit Quell-IP-Erhalt.",
        addr,
    )


class GlobalRateLimiter:
    """Moving-Window-Limit pro Client-IP (in-memory, ein Prozess)."""

    def __init__(self, limits: str) -> None:
        self._limits = parse_many(limits)
        self._storage = MemoryStorage()
        self._strategy = MovingWindowRateLimiter(self._storage)

    @staticmethod
    def exempt(func):
        """Decorator: Endpoint vom globalen Limit ausnehmen (unter ``@router.get``)."""
        func._rate_limit_exempt = True
        return func

    @staticmethod
    def is_exempt(endpoint) -> bool:
        return bool(getattr(endpoint, "_rate_limit_exempt", False))

    def hit(self, key: str) -> int | None:
        """Zaehlt einen Request. None = erlaubt, sonst Sekunden bis zum naechsten Slot."""
        for item in self._limits:
            if not self._strategy.hit(item, key):
                reset_at = self._strategy.get_window_stats(item, key).reset_time
                return max(1, math.ceil(reset_at - time.time()))
        return None

    def reset(self) -> None:
        self._storage.reset()


limiter = GlobalRateLimiter(GLOBAL_RATE_LIMITS)


async def global_rate_limit(request: Request) -> None:
    """App-weite Dependency: 429 wenn die Client-IP das globale Limit reisst."""
    if limiter.is_exempt(request.scope.get("endpoint")):
        return
    retry_after = limiter.hit(trusted_client_ip(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Zu viele Anfragen. Bitte kurz warten und erneut versuchen.",
            headers={"Retry-After": str(retry_after)},
        )
