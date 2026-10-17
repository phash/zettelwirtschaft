"""Obergrenze fuer die Groesse von Request-Bodies (HTTP 413).

Im Docker-Betrieb begrenzt nginx (client_max_body_size) die Bodies; im
Native-Betrieb steht uvicorn direkt im LAN und wuerde beliebig grosse Uploads
annehmen und puffern. Diese ASGI-Middleware erzwingt dieselbe Grenze im
Backend selbst: zuerst ueber Content-Length, bei Chunked-Uploads waehrend des
Einlesens.
"""

import json

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings_for_app

# Puffer fuer Multipart-Overhead (Boundaries, Header, Formularfelder) ueber dem
# Dateilimit MAX_UPLOAD_SIZE_MB — nginx nutzt denselben Wert (55M bei 50 MB).
MULTIPART_HEADROOM_MB = 5


def max_request_body_bytes(max_upload_size_mb: int) -> int:
    return (max_upload_size_mb + MULTIPART_HEADROOM_MB) * 1024 * 1024


def _too_large_detail(max_upload_size_mb: int) -> str:
    return f"Anfrage zu groß. Erlaubt sind Dateien bis {max_upload_size_mb} MB."


class RequestBodyLimitMiddleware:
    """413 fuer Requests, deren Body ``MAX_UPLOAD_SIZE_MB`` + Puffer uebersteigt."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_mb = settings_for_app(scope.get("app")).MAX_UPLOAD_SIZE_MB
        limit = max_request_body_bytes(max_mb)

        content_length = dict(scope.get("headers") or []).get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > limit:
                await _send_413(send, _too_large_detail(max_mb))
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
                    raise HTTPException(status_code=413, detail=_too_large_detail(max_mb))
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
