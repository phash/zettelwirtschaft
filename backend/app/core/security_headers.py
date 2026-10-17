"""Security-Header fuer den Native-Betrieb (BR-04).

Im Docker-Betrieb setzt nginx diese Header (``frontend/nginx-common.conf``). Im
Native-Betrieb liefert uvicorn SPA, API und Dokument-Dateien direkt aus — ohne
diese Middleware gaebe es dort weder CSP noch Clickjacking-Schutz noch nosniff.

Die Werte MUESSEN mit nginx-common.conf uebereinstimmen; der Drift-Test
``tests/core/test_security_headers.py`` vergleicht beide.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings_for_app

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
    "frame-src 'self' blob:; object-src 'none'; base-uri 'self'"
)

SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}

_ENCODED = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in SECURITY_HEADERS.items()]
_NAMES = {k for k, _ in _ENCODED}


class SecurityHeadersMiddleware:
    """Setzt die Security-Header, wenn das Backend selbst der Edge-Server ist
    (``deployment_mode == "native"``). Hinter nginx (Docker) und im Dev-Modus
    (Vite-Proxy, optional Swagger-UI) bleibt alles unveraendert — nginx setzt die
    Header dort selbst, doppelte Header waeren nur Rauschen."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or settings_for_app(scope.get("app")).deployment_mode != "native":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in _NAMES]
                headers.extend(_ENCODED)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
