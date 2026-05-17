import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Response
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory session store: token -> expiry
_sessions: dict[str, datetime] = {}

# Rate limiting: IP -> (fail_count, lockout_until)
_login_attempts: dict[str, tuple[int, datetime | None]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 30

SESSION_COOKIE = "zw_session"


class PinRequest(BaseModel):
    pin: str


def _cleanup_expired() -> None:
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _sessions.items() if v <= now]
    for k in expired:
        del _sessions[k]
    # Login-Attempts: abgelaufene Lockouts entfernen.
    # NEW-B (Re-Review): Wenn fail_count >= MAX aber lockout=None (Race bei
    # paralleler Last), wurde nach altem Code der Eintrag nie aufgeraeumt.
    # Jetzt cleanen wir auch solche "verwaisten" Counter nach 5min Idle.
    stale = []
    for ip, (count, lockout) in _login_attempts.items():
        if lockout and lockout <= now:
            stale.append(ip)
        elif count >= MAX_LOGIN_ATTEMPTS and lockout is None:
            # Counter haengt ohne Lockout — als Self-DoS-Schutz aufraeumen
            stale.append(ip)
    for ip in stale:
        del _login_attempts[ip]


def is_session_valid(token: str | None) -> bool:
    if not token:
        return False
    _cleanup_expired()
    return token in _sessions


def clear_all_sessions() -> None:
    _sessions.clear()


@router.get("/status")
async def auth_status(request: Request, settings: Settings = Depends(get_settings)):
    # pin_warning signalisiert dem Frontend, dass das System ohne Auth laeuft.
    # Verschoben aus dem reinen Startup-Log (sieht nur wer `docker compose logs`
    # aufruft) ins UI, damit Heim-User die Lage erkennen.
    if not settings.PIN_ENABLED:
        return {
            "pin_enabled": False,
            "authenticated": True,
            "pin_warning": True,
        }

    token = request.cookies.get(SESSION_COOKIE)
    return {
        "pin_enabled": True,
        "authenticated": is_session_valid(token),
        "pin_warning": False,
    }


@router.post("/login")
async def auth_login(
    body: PinRequest, request: Request, response: Response, settings: Settings = Depends(get_settings)
):
    if not settings.PIN_ENABLED:
        return {"success": True}

    _cleanup_expired()

    # Rate Limiting — gleicher Trust-Filter wie slowapi (NEW-002).
    from app.core.rate_limit import trusted_client_ip
    client_ip = trusted_client_ip(request)
    now = datetime.now(timezone.utc)
    fail_count, lockout_until = _login_attempts.get(client_ip, (0, None))
    if lockout_until and now < lockout_until:
        remaining = int((lockout_until - now).total_seconds())
        return JSONResponse(
            status_code=429,
            content={"success": False, "detail": f"Zu viele Fehlversuche. Bitte {remaining}s warten."},
        )

    if secrets.compare_digest(body.pin, settings.PIN_CODE):
        # Erfolg: Attempts zuruecksetzen
        _login_attempts.pop(client_ip, None)
        token = uuid.uuid4().hex
        expiry = now + timedelta(minutes=settings.PIN_SESSION_TIMEOUT_MINUTES)
        _sessions[token] = expiry
        # F-05: Secure-Flag dynamisch — bei TLS-Setup wird er aktiv, ohne
        # bricht der HTTP-Default nicht. SameSite=strict, weil kein externer
        # Cross-Site-POST-Workflow erwartet wird (internes Tool).
        # R-07 (Re-Review): X-Forwarded-Proto nur akzeptieren wenn die
        # TCP-Verbindung aus einem trusted Proxy-Netz kommt — sonst kann
        # ein LAN-Angreifer den Header faken und Secure=true erzwingen, was
        # bei HTTP-Setup das Cookie unbenutzbar macht (Login-DoS).
        from app.core.rate_limit import _is_trusted_proxy
        client_host = request.client.host if request.client else None
        is_trusted_proxy = client_host is not None and _is_trusted_proxy(client_host)
        proto_header = request.headers.get("x-forwarded-proto") if is_trusted_proxy else None
        is_https = request.url.scheme == "https" or proto_header == "https"
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="strict",
            secure=is_https,
            max_age=settings.PIN_SESSION_TIMEOUT_MINUTES * 60,
        )
        return {"success": True}

    # Fehlversuch zaehlen
    fail_count += 1
    lockout = now + timedelta(seconds=LOCKOUT_SECONDS) if fail_count >= MAX_LOGIN_ATTEMPTS else None
    _login_attempts[client_ip] = (fail_count, lockout)
    return JSONResponse(status_code=401, content={"success": False, "detail": "Falscher PIN"})


@router.post("/logout")
async def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in _sessions:
        del _sessions[token]
    response.delete_cookie(key=SESSION_COOKIE)
    return {"success": True}
