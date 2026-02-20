import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Response
from fastapi.requests import Request
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory session store: token -> expiry
_sessions: dict[str, datetime] = {}

SESSION_COOKIE = "zw_session"


class PinRequest(BaseModel):
    pin: str


def _cleanup_expired() -> None:
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _sessions.items() if v <= now]
    for k in expired:
        del _sessions[k]


def is_session_valid(token: str | None) -> bool:
    if not token:
        return False
    _cleanup_expired()
    return token in _sessions


def clear_all_sessions() -> None:
    _sessions.clear()


@router.get("/status")
async def auth_status(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.PIN_ENABLED:
        return {"pin_enabled": False, "authenticated": True}

    token = request.cookies.get(SESSION_COOKIE)
    return {
        "pin_enabled": True,
        "authenticated": is_session_valid(token),
    }


@router.post("/login")
async def auth_login(
    body: PinRequest, response: Response, settings: Settings = Depends(get_settings)
):
    if not settings.PIN_ENABLED:
        return {"success": True}

    if body.pin == settings.PIN_CODE:
        token = uuid.uuid4().hex
        expiry = datetime.now(timezone.utc) + timedelta(
            minutes=settings.PIN_SESSION_TIMEOUT_MINUTES
        )
        _sessions[token] = expiry
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=settings.PIN_SESSION_TIMEOUT_MINUTES * 60,
        )
        return {"success": True}

    return {"success": False, "detail": "Falscher PIN"}


@router.post("/logout")
async def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in _sessions:
        del _sessions[token]
    response.delete_cookie(key=SESSION_COOKIE)
    return {"success": True}
