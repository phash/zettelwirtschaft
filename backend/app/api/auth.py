import ipaddress
import logging
import secrets
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Response
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.core.rate_limit import is_trusted_proxy, looks_like_nat_gateway, trusted_client_ip
from app.database import get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory session store: token -> expiry
_sessions: dict[str, datetime] = {}

# Rate limiting: IP -> (fail_count, lockout_until, last_seen)
_login_attempts: dict[str, tuple[int, datetime | None, datetime]] = {}
# AUTH-01: Zeitpunkte der letzten Fehlversuche ueber alle IPs (global budget).
_global_failures: deque[datetime] = deque()
# Ob fuer die aktuelle globale Sperr-Episode schon gewarnt wurde.
_global_lock_notified = False
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 30
# S-MED1: Deckel fuer den exponentiellen Lockout-Backoff.
MAX_LOCKOUT_SECONDS = 3600
# Idle-Fenster, nach dem ein Attempt-Counter ganz vergessen wird. AUTH-01: Es
# zaehlt ab dem ENDE des letzten Lockouts (nicht ab dem letzten Fehlversuch) und
# ist deutlich laenger als MAX_LOCKOUT_SECONDS — frueher waren beide 1h, sodass
# der Counter genau beim Ablauf des maximalen Lockouts geloescht wurde und der
# Backoff alle ~2h wieder bei 0 begann (144 Versuche/Tag pro IP).
ATTEMPT_FORGIVE_SECONDS = 24 * 3600
# AUTH-01: Globales Fehlversuchs-Budget ueber ALLE Client-IPs. Der Per-IP-
# Lockout allein laesst sich mit vielen Adressen (IPv4-Aliase im LAN) beliebig
# parallelisieren. Ab GLOBAL_MAX_FAILURES Fehlversuchen innerhalb von
# GLOBAL_FAILURE_WINDOW_SECONDS werden Logins von Nicht-Loopback-Adressen
# gesperrt, bis das Fenster wieder unter die Schwelle faellt (Durchsatz-Deckel
# ~720 Versuche/Tag fuer alle IPs zusammen). Loopback (Native: Browser am
# Server-PC selbst) bleibt ausgenommen, damit der Besitzer sich waehrend eines
# LAN-Angriffs noch anmelden kann; fuer Loopback gilt weiter der Per-IP-Lockout.
GLOBAL_MAX_FAILURES = 30
GLOBAL_FAILURE_WINDOW_SECONDS = 3600
# Obergrenze fuer _login_attempts (Speicher). Volle Tabelle -> der am laengsten
# inaktive, NICHT gesperrte Eintrag wird verdraengt; gesperrte bleiben. AVAIL-07:
# Ist JEDER Eintrag gesperrt, wird nichts verdraengt (sonst liesse sich die
# eigene Sperre per Adress-Flut loeschen) — stattdessen bekommen unbekannte
# Adressen fail-closed 429, bis wieder Platz ist; die Tabelle waechst nie ueber
# die Grenze.
MAX_TRACKED_CLIENTS = 1024
# AVAIL-04: Sieht der Schluessel wie ein NAT-Gateway aus (Docker Desktop ersetzt
# die Geraete-Adresse durch sein Gateway, s. rate_limit.looks_like_nat_gateway),
# teilen sich ALLE Geraete eine Sperre — ein Gast mit falschen PINs sperrte bis
# zu 1 h den ganzen Haushalt aus. Fuer solche geteilten Schluessel wird der
# Per-Key-Backoff kurz gedeckelt. Brute-Force bleibt trotzdem begrenzt: das
# globale Budget (GLOBAL_MAX_FAILURES/h) gilt unabhaengig vom Schluessel.
SHARED_KEY_MAX_LOCKOUT_SECONDS = 60
# AVAIL-07: Session-/Attempt-Aufraeumen nicht bei jedem authentifizierten
# Request (O(n) ueber alle Eintraege), sondern hoechstens alle N Sekunden.
CLEANUP_INTERVAL_SECONDS = 10.0
_last_cleanup: float | None = None

SESSION_COOKIE = "zw_session"


class PinRequest(BaseModel):
    pin: str = Field(min_length=1, max_length=64)


def _pin_matches(candidate: str, expected: str) -> bool:
    # Bytes statt str: compare_digest(str, str) wirft TypeError bei Nicht-ASCII
    # (z.B. "ä" oder ein eingefuegtes geschuetztes Leerzeichen) -> HTTP 500
    # statt 401, und der Versuch wuerde nicht gezaehlt.
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _max_lockout_seconds(client_ip: str, settings: Settings | None) -> int:
    """Backoff-Deckel fuer ``client_ip`` — kurz bei geteiltem NAT-Schluessel."""
    if settings is None:
        return MAX_LOCKOUT_SECONDS
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return MAX_LOCKOUT_SECONDS
    if looks_like_nat_gateway(addr, settings):
        return SHARED_KEY_MAX_LOCKOUT_SECONDS
    return MAX_LOCKOUT_SECONDS


def _make_room_for(client_ip: str, now: datetime) -> bool:
    """Platz fuer einen neuen Eintrag schaffen. False = Tabelle voll mit
    gesperrten Eintraegen (AVAIL-07: dann NICHT einfuegen)."""
    if client_ip in _login_attempts or len(_login_attempts) < MAX_TRACKED_CLIENTS:
        return True
    evictable = [
        (last_seen, ip)
        for ip, (_count, until, last_seen) in _login_attempts.items()
        if until is None or until <= now
    ]
    if not evictable:
        return False
    del _login_attempts[min(evictable)[1]]
    return True


def _record_failed_attempt(
    client_ip: str, fail_count: int, now: datetime, settings: Settings | None = None
) -> bool:
    """Fehlversuch zaehlen. False = Per-IP-Eintrag nicht gespeichert (Tabelle
    voll mit gesperrten Adressen); das globale Budget zaehlt immer."""
    lockout = None
    if fail_count >= MAX_LOGIN_ATTEMPTS:
        overflow = fail_count - MAX_LOGIN_ATTEMPTS
        lockout_seconds = min(LOCKOUT_SECONDS * (2 ** overflow), _max_lockout_seconds(client_ip, settings))
        lockout = now + timedelta(seconds=lockout_seconds)
    tracked = _make_room_for(client_ip, now)
    if tracked:
        _login_attempts[client_ip] = (fail_count, lockout, now)
    _global_failures.append(now)
    _prune_global_failures(now)
    return tracked


def _prune_global_failures(now: datetime) -> None:
    global _global_lock_notified
    cutoff = now - timedelta(seconds=GLOBAL_FAILURE_WINDOW_SECONDS)
    while _global_failures and _global_failures[0] <= cutoff:
        _global_failures.popleft()
    # Nur die letzten GLOBAL_MAX_FAILURES Zeitpunkte sind fuer die Entscheidung
    # relevant -> Speicher begrenzt.
    while len(_global_failures) > GLOBAL_MAX_FAILURES:
        _global_failures.popleft()
    if len(_global_failures) < GLOBAL_MAX_FAILURES:
        _global_lock_notified = False


def _global_lockout_remaining(now: datetime) -> int | None:
    """Sekunden bis zum Ende der globalen Sperre, None = keine Sperre."""
    _prune_global_failures(now)
    if len(_global_failures) < GLOBAL_MAX_FAILURES:
        return None
    unlock_at = _global_failures[0] + timedelta(seconds=GLOBAL_FAILURE_WINDOW_SECONDS)
    return max(1, int((unlock_at - now).total_seconds()))


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


async def _notify_global_lockout(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Einmal pro Sperr-Episode: Warnung ins Log + Glocken-Benachrichtigung."""
    global _global_lock_notified
    if _global_lock_notified:
        return
    _global_lock_notified = True
    logger.warning(
        "PIN-Anmeldung global gesperrt: %d Fehlversuche innerhalb von %ds (alle Geraete) — "
        "moeglicher Brute-Force-Angriff im Netzwerk",
        GLOBAL_MAX_FAILURES,
        GLOBAL_FAILURE_WINDOW_SECONDS,
    )
    try:
        from app.services.notification_service import notify_system

        async with session_factory() as session:
            notify_system(
                session,
                "Viele falsche PIN-Eingaben",
                f"Innerhalb einer Stunde wurde {GLOBAL_MAX_FAILURES}-mal ein falscher PIN "
                "eingegeben. Die Anmeldung von anderen Geräten ist vorübergehend gesperrt. "
                "Falls Sie das nicht waren, versucht möglicherweise jemand in Ihrem Netzwerk, "
                "den PIN zu erraten — ändern Sie den PIN und prüfen Sie die Geräte im WLAN.",
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — Warnung darf den Login-Pfad nie brechen
        logger.exception("Benachrichtigung ueber globale PIN-Sperre fehlgeschlagen")


def reset_login_state() -> None:
    """Login-Zaehler komplett zuruecksetzen (Tests)."""
    global _global_lock_notified, _last_cleanup
    _login_attempts.clear()
    _global_failures.clear()
    _global_lock_notified = False
    _last_cleanup = None


def _cleanup_expired(force: bool = True) -> None:
    """Abgelaufene Sessions und vergebene Login-Zaehler entfernen.

    ``force=False`` (pro authentifiziertem Request): hoechstens alle
    ``CLEANUP_INTERVAL_SECONDS`` — die Gueltigkeit einer Session prueft
    ``is_session_valid`` ohnehin selbst."""
    global _last_cleanup
    mono = time.monotonic()
    if not force and _last_cleanup is not None and mono - _last_cleanup < CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup = mono
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _sessions.items() if v <= now]
    for k in expired:
        del _sessions[k]
    # Login-Attempts: S-MED1 — Counter NICHT mehr beim Ablauf des Lockouts
    # loeschen. Frueher setzte das fail_count auf 0 zurueck, sodass ein Angreifer
    # alle LOCKOUT_SECONDS frische MAX_LOGIN_ATTEMPTS Versuche bekam (kein
    # Eskalationseffekt). Jetzt: abgelaufenen Lockout nur entsperren (fail_count
    # bleibt fuer den exponentiellen Backoff erhalten) und Eintraege erst nach
    # laengerer Inaktivitaet (ATTEMPT_FORGIVE_SECONDS) ganz vergessen — das
    # begrenzt zugleich das Memory-Wachstum.
    # AUTH-01: Inaktivitaet zaehlt ab dem Ende des letzten Lockouts. Der
    # Zeitstempel in last_seen wird deshalb beim Entsperren auf das Lockout-Ende
    # vorgezogen (frueher: Zeitpunkt des Fehlversuchs -> bei Lockout >= Fenster
    # wurde der Counter genau beim Entsperren geloescht).
    forgiven = []
    for ip, (count, lockout, last_seen) in list(_login_attempts.items()):
        if lockout is not None and now < lockout:
            continue  # noch gesperrt
        idle_since = max(last_seen, lockout) if lockout is not None else last_seen
        if (now - idle_since).total_seconds() >= ATTEMPT_FORGIVE_SECONDS:
            forgiven.append(ip)
        elif lockout is not None:
            # Lockout abgelaufen -> entsperren, fail_count behalten; Idle-Uhr
            # startet am Lockout-Ende.
            _login_attempts[ip] = (count, None, idle_since)
    for ip in forgiven:
        del _login_attempts[ip]


def is_session_valid(token: str | None) -> bool:
    if not token:
        return False
    _cleanup_expired(force=False)
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    if expiry <= datetime.now(timezone.utc):
        _sessions.pop(token, None)
        return False
    return True


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
    body: PinRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    if not settings.PIN_ENABLED:
        return {"success": True}

    _cleanup_expired()

    # Rate Limiting — gleicher Trust-Filter wie das globale Limit (NEW-002).
    client_ip = trusted_client_ip(request, settings)
    now = datetime.now(timezone.utc)
    fail_count, lockout_until, _last_seen = _login_attempts.get(client_ip, (0, None, now))
    if lockout_until and now < lockout_until:
        remaining = int((lockout_until - now).total_seconds())
        return JSONResponse(
            status_code=429,
            content={"success": False, "detail": f"Zu viele Fehlversuche. Bitte {remaining}s warten."},
        )

    # AUTH-01: globales Budget ueber alle IPs (Loopback ausgenommen, s.o.).
    global_remaining = _global_lockout_remaining(now)
    if global_remaining is not None:
        await _notify_global_lockout(session_factory)
        if not _is_loopback(client_ip):
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "detail": (
                        "Zu viele Fehlversuche im Netzwerk. Die Anmeldung ist vorübergehend "
                        f"gesperrt. Bitte {global_remaining}s warten."
                    ),
                },
            )

    if _pin_matches(body.pin, settings.PIN_CODE):
        # Erfolg: Attempts zuruecksetzen
        _login_attempts.pop(client_ip, None)
        token = uuid.uuid4().hex
        expiry = now + timedelta(minutes=settings.PIN_SESSION_TIMEOUT_MINUTES)
        _sessions[token] = expiry
        # F-05: Secure-Flag dynamisch — bei TLS-Setup wird er aktiv, ohne
        # bricht der HTTP-Default nicht. SameSite=strict, weil kein externer
        # Cross-Site-POST-Workflow erwartet wird (internes Tool).
        # R-07 (Re-Review): X-Forwarded-Proto nur akzeptieren wenn die
        # TCP-Verbindung von einem trusted Proxy kommt (nur Docker/nginx) —
        # sonst kann ein LAN-Angreifer den Header faken und Secure=true
        # erzwingen, was bei HTTP-Setup das Cookie unbenutzbar macht (Login-DoS).
        client_host = request.client.host if request.client else None
        from_proxy = is_trusted_proxy(client_host, settings)
        proto_header = request.headers.get("x-forwarded-proto") if from_proxy else None
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

    # Fehlversuch zaehlen — S-MED1: ab MAX_LOGIN_ATTEMPTS exponentieller Backoff
    # (30s, 60s, 120s, ... gedeckelt auf MAX_LOCKOUT_SECONDS), damit wiederholte
    # Lockout-Runden den Brute-Force-Durchsatz progressiv senken statt sich alle
    # LOCKOUT_SECONDS zurueckzusetzen.
    tracked = _record_failed_attempt(client_ip, fail_count + 1, now, settings)
    if not tracked:
        # AVAIL-07: Tabelle voll mit gesperrten Adressen -> nichts einfuegen
        # (Speicher bleibt begrenzt). Der Fehlversuch zaehlt trotzdem im
        # globalen Budget; fail-closed 429 statt 401.
        logger.warning("PIN-Login: Fehlversuchs-Tabelle voll (%d gesperrte Adressen)", len(_login_attempts))
        return JSONResponse(
            status_code=429,
            content={"success": False, "detail": "Zu viele Fehlversuche. Bitte später erneut versuchen."},
        )
    if _global_lockout_remaining(now) is not None:
        await _notify_global_lockout(session_factory)
    return JSONResponse(status_code=401, content={"success": False, "detail": "Falscher PIN"})


@router.post("/logout")
async def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in _sessions:
        del _sessions[token]
    response.delete_cookie(key=SESSION_COOKIE)
    return {"success": True}
