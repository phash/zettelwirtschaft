"""Globaler slowapi-Limiter — zentral importierbar fuer Endpoint-Decoratoren.

Wird aus main.py beim FastAPI-App-Setup als app.state.limiter registriert.
Endpoints die das globale Default-Limit (200/min) nicht treffen sollen
(z.B. Health-Probes von docker-compose) koennen `@limiter.exempt` nutzen.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_id(request) -> str:
    """IP-Identifizierung mit X-Real-IP-Praeferenz fuer den nginx-Pfad.

    H-04 / NEW-002 (SECURITY_AUDIT_v3): Wenn das Backend je direkt erreichbar
    wird, kann der Header gespoofed werden. Aktuelle Topologie (expose statt
    ports + nginx-only-Eingang) macht das nicht praktisch ausnutzbar, aber bei
    Caddy-Migration / anderem Reverse-Proxy ist `--forwarded-allow-ips` in
    uvicorn die richtige Antwort.
    """
    return request.headers.get("X-Real-IP") or get_remote_address(request)


limiter = Limiter(key_func=_client_id, default_limits=["200/minute"])
