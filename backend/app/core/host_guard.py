"""Host-/Origin-Pruefung gegen DNS-Rebinding und Cross-Site-Requests (AUTH-02).

DNS-Rebinding: Eine fremde Webseite (``attacker.example``) loest ihren Namen
nachtraeglich auf die LAN-IP des Zettelwirtschaft-Rechners auf. Fuer den Browser
ist das danach *same-origin* — CORS und SameSite-Cookies greifen nicht mehr, der
Host-Header lautet aber weiterhin ``attacker.example``. Deshalb akzeptiert das
Backend nur Host-Namen, die ein Angreifer nicht ueber oeffentliches DNS auf die
LAN-IP zeigen lassen kann:

- IP-Literale (ohne Namen gibt es kein Rebinding),
- ``localhost``/``*.localhost``, Namen ohne Punkt (Windows-PC-Name, Docker-
  Dienstname ``backend``), mDNS ``*.local`` und private Suffixe
  (``*.lan``, ``*.home.arpa``, ``*.internal``, ``*.fritz.box``),
- den eigenen Rechnernamen (``socket.gethostname``/``getfqdn``),
- zusaetzlich konfigurierte Namen aus ``ALLOWED_HOSTS`` (``*`` = aus).

Zusaetzlich (Defense-in-Depth gegen klassisches CSRF, v.a. bei abgeschaltetem
PIN): zustandsaendernde Requests mit ``Origin``-Header muessen von derselben
Adresse, einer privaten/Loopback-IP oder einem erlaubten lokalen Namen kommen.
``Origin: null`` (Sandbox-iframe, file://) wird abgelehnt. Moderne Browser
markieren Requests zudem per ``Sec-Fetch-Site``; ``cross-site``/``same-site``
wird fuer zustandsaendernde Methoden immer abgelehnt (die SPA ist same-origin).

Laeuft als aeusserste ASGI-Middleware fuer alle HTTP-Pfade (Docker: nginx reicht
``Host $host`` durch; Native: uvicorn steht direkt im LAN).
"""

import ipaddress
import json
import logging
import socket
from functools import lru_cache
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings, settings_for_app

logger = logging.getLogger(__name__)

LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa", ".internal", ".fritz.box")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MAX_LOGGED_HOSTS = 64
_logged_hosts: set[str] = set()


@lru_cache(maxsize=1)
def _machine_names() -> frozenset[str]:
    names: set[str] = set()
    for getter in (socket.gethostname, socket.getfqdn):
        try:
            name = getter().strip().rstrip(".").lower()
        except OSError:
            continue
        if name:
            names.add(name)
            names.add(f"{name.split('.')[0]}.local")
    return frozenset(names)


def _split_host(value: str) -> str:
    """Hostname aus einem Host-Header bzw. netloc (ohne Port, klein, ohne Punkt am Ende)."""
    value = value.strip().lower()
    if value.startswith("["):  # [IPv6]:port
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value.rstrip(".")


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _extra_hosts(settings: Settings) -> list[str]:
    return [h.strip().lower().rstrip(".") for h in settings.ALLOWED_HOSTS.split(",") if h.strip()]


def is_local_name(host: str, settings: Settings) -> bool:
    """True fuer Namen, die nicht per oeffentlichem DNS auf die LAN-IP zeigen koennen."""
    if not host:
        return False
    if host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES):
        return True
    if host in _machine_names():
        return True
    for extra in _extra_hosts(settings):
        if extra == "*" or extra == host:
            return True
        if extra.startswith("*.") and host.endswith(extra[1:]):
            return True
    return False


def is_allowed_host(host_header: str | None, settings: Settings) -> bool:
    if not host_header:
        # Kein Host (HTTP/1.0, direkte Tools): Browser senden ihn immer -> kein Rebinding.
        return True
    host = _split_host(host_header)
    return _ip(host) is not None or is_local_name(host, settings)


def is_allowed_origin(origin: str, host_header: str | None, settings: Settings) -> bool:
    if origin == "null":
        return False
    try:
        origin_host = _split_host(urlsplit(origin).netloc)
    except ValueError:
        return False
    if not origin_host:
        return False
    if host_header and origin_host == _split_host(host_header):
        return True  # same-origin (Port egal: nginx reicht $host ohne Port durch)
    ip = _ip(origin_host)
    if ip is not None:
        return ip.is_loopback or ip.is_private or ip.is_link_local
    return is_local_name(origin_host, settings)


class HostGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = settings_for_app(scope.get("app"))
        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        host = headers.get(b"host", b"").decode("latin-1") or None

        if not is_allowed_host(host, settings):
            _log_once(host or "", "Host")
            await _reject(send, 400, "Ungültiger Host-Header.")
            return

        unsafe = scope.get("method", "GET").upper() in UNSAFE_METHODS

        # BR-02/BR-03: Fetch-Metadata. Die SPA ruft die API immer same-origin auf
        # (nginx-Proxy, Native-StaticFiles, Vite-Dev-Proxy). Ein zustandsaendernder
        # Request, den der Browser selbst als "cross-site" (fremde Webseite,
        # no-cors-Multipart-Upload) oder "same-site" (andere Port-Origin auf
        # demselben Rechner, z.B. XSS in Home Assistant/Pi-hole nebenan — bekommt
        # das SameSite=strict-Cookie!) markiert, ist nie legitim. Tools ohne
        # Browser (curl, Skripte) senden den Header nicht und bleiben unberuehrt.
        if unsafe and headers.get(b"sec-fetch-site", b"").strip().lower() in (b"cross-site", b"same-site"):
            _log_once(headers[b"sec-fetch-site"].decode("latin-1"), "Sec-Fetch-Site")
            await _reject(send, 403, "Anfrage von fremder Herkunft abgelehnt.")
            return

        origin = headers.get(b"origin")
        if origin is not None and unsafe:
            origin_str = origin.decode("latin-1").strip()
            if not is_allowed_origin(origin_str, host, settings):
                _log_once(origin_str, "Origin")
                await _reject(send, 403, "Anfrage von fremder Herkunft abgelehnt.")
                return

        await self.app(scope, receive, send)


def _log_once(value: str, kind: str) -> None:
    key = f"{kind}:{value[:200]}"
    if key in _logged_hosts or len(_logged_hosts) >= _MAX_LOGGED_HOSTS:
        return
    _logged_hosts.add(key)
    logger.warning(
        "Request mit nicht erlaubtem %s-Header %r abgelehnt (DNS-Rebinding-/CSRF-Schutz). "
        "Eigene Domain? -> ALLOWED_HOSTS setzen.",
        kind,
        value[:200],
    )


async def _reject(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
