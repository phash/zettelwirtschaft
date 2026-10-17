"""Obergrenze fuer die Groesse von Request-Bodies (HTTP 413).

Im Docker-Betrieb begrenzt nginx (client_max_body_size) die Bodies; im
Native-Betrieb steht uvicorn direkt im LAN und wuerde beliebig grosse Uploads
annehmen und puffern. Diese ASGI-Middleware erzwingt dieselbe Grenze im
Backend selbst: zuerst ueber Content-Length, bei Chunked-Uploads waehrend des
Einlesens.

AVAIL-01: Die grosse Grenze gilt nur fuer die Upload-Route. Alle anderen
Endpunkte nehmen JSON/kleine Bodies entgegen; FastAPI parst diese komplett mit
``json.loads`` auf dem Event-Loop, BEVOR Rate-Limit-Dependency und
Schema-Pruefung greifen. Ein 55-MB-JSON an das (PIN-freie) /api/auth/login
blaehte sich auf ~1,5 GB Python-Objekte auf. Darum: kleine Standardgrenze fuer
alle Nicht-Upload-Routen, noch kleinere fuer /api/auth/*. nginx spiegelt die
Upload-/Standard-Grenze (frontend/nginx-common.conf).
"""

import json

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings_for_app

# Puffer fuer Multipart-Overhead (Boundaries, Header, Formularfelder) ueber dem
# Dateilimit MAX_UPLOAD_SIZE_MB — nginx nutzt denselben Wert (55M bei 50 MB).
MULTIPART_HEADROOM_MB = 5


# Einzige Routen mit Datei-Bodies (Multipart). Exakte Pfade, keine Praefixe —
# sonst wuerde z.B. /api/documents/upload/../auth/login die grosse Grenze erben.
UPLOAD_PATHS = frozenset({"/api/documents/upload"})

# Standardgrenze fuer JSON-/Formular-Bodies aller anderen Routen. Groesster
# legitimer Body ist ein Dokument-Update mit langer Zusammenfassung (wenige KB);
# 256 KiB lassen viel Luft und begrenzen die Parse-Amplifikation auf ~10 MB.
# nginx: ``client_max_body_size 256k`` in ``location ^~ /api/``.
DEFAULT_MAX_BODY_BYTES = 256 * 1024

# /api/auth/* ist ohne Session erreichbar und traegt nur {"pin": "..."}.
AUTH_PATH_PREFIX = "/api/auth/"
AUTH_MAX_BODY_BYTES = 4 * 1024


def max_request_body_bytes(max_upload_size_mb: int) -> int:
    return (max_upload_size_mb + MULTIPART_HEADROOM_MB) * 1024 * 1024


def _too_large_detail(max_upload_size_mb: int) -> str:
    return f"Anfrage zu groß. Erlaubt sind Dateien bis {max_upload_size_mb} MB."


_SMALL_BODY_DETAIL = "Anfrage zu groß."


def body_limit_for_path(path: str, max_upload_size_mb: int) -> tuple[int, str]:
    """(Grenze in Bytes, Fehlertext) fuer den Request-Pfad."""
    if path in UPLOAD_PATHS:
        return max_request_body_bytes(max_upload_size_mb), _too_large_detail(max_upload_size_mb)
    if path.startswith(AUTH_PATH_PREFIX) or path == AUTH_PATH_PREFIX.rstrip("/"):
        return AUTH_MAX_BODY_BYTES, _SMALL_BODY_DETAIL
    return DEFAULT_MAX_BODY_BYTES, _SMALL_BODY_DETAIL


class RequestBodyLimitMiddleware:
    """413 fuer Requests, deren Body die Grenze der Route uebersteigt
    (Upload: ``MAX_UPLOAD_SIZE_MB`` + Puffer, sonst ``DEFAULT_MAX_BODY_BYTES``)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_mb = settings_for_app(scope.get("app")).MAX_UPLOAD_SIZE_MB
        limit, detail = body_limit_for_path(scope.get("path", ""), max_mb)

        content_length = dict(scope.get("headers") or []).get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > limit:
                await _send_413(send, detail)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # HTTPException statt eigener Exception: FastAPI reicht sie
                    # beim Body-Parsen unveraendert durch (andere Fehler wuerden
                    # zu "400 error parsing the body"), der Exception-Handler
                    # macht daraus die JSON-413-Antwort.
                    raise HTTPException(status_code=413, detail=detail)
            return message

        await self.app(scope, limited_receive, send)


async def _send_413(send: Send, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
