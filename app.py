import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from garminconnect import Garmin

from token_store import SupabaseTokenStore, TokenStoreError

app = FastAPI(title="Garmin Bridge", version="0.3.0")

logger = logging.getLogger("garmin_bridge")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# After a failed Garmin login, refuse new login attempts for this long so that a
# burst of requests cannot hammer Garmin's login endpoint (HTTP 429).
LOGIN_COOLDOWN_SECONDS = int(os.getenv("GARMIN_LOGIN_COOLDOWN_SECONDS", "1800"))

_client: Garmin | None = None
_client_lock = Lock()
_persist_lock = Lock()
_last_saved_tokens: str | None = None
_login_blocked_until = 0.0


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    """Protect private activity/health data. Configure BRIDGE_API_KEY in Render."""
    expected = os.getenv("BRIDGE_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bridge API is not configured yet. Set BRIDGE_API_KEY in the hosting environment.",
        )
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_token_store() -> SupabaseTokenStore | None:
    """Supabase token store if SUPABASE_* variables are set, otherwise None (file storage)."""
    try:
        return SupabaseTokenStore.from_env()
    except TokenStoreError as exc:
        logger.error("Token storage misconfigured: %s", exc)
        raise HTTPException(status_code=500, detail="Token storage is misconfigured; check service logs.") from exc


def get_client() -> Garmin:
    global _client, _last_saved_tokens, _login_blocked_until

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise HTTPException(status_code=500, detail="Garmin credentials are not configured")

    if _client is not None:
        return _client

    with _client_lock:
        if _client is None:
            remaining = int(_login_blocked_until - time.monotonic())
            if remaining > 0:
                raise HTTPException(
                    status_code=503,
                    detail=f"A recent Garmin login failed. Login is paused for another {remaining} seconds to avoid rate limits.",
                )

            store = get_token_store()
            client = Garmin(email, password)
            stored_tokens: str | None = None
            try:
                if store is not None:
                    # Never fall back to a password login when Supabase itself is
                    # unreachable: that would create new logins on every outage.
                    stored_tokens = store.load()
                    if stored_tokens:
                        logger.info("Restoring Garmin session from tokens stored in Supabase")
                    else:
                        logger.info("No Garmin tokens stored in Supabase yet; performing one credential login")
                    # garminconnect accepts inline token JSON; None means credential login.
                    client.login(stored_tokens)
                else:
                    # Without Supabase, tokens are kept on disk. Point GARMIN_TOKENSTORE
                    # at persistent storage (e.g. a mounted Render disk) to survive restarts.
                    tokenstore = os.getenv("GARMIN_TOKENSTORE", "/data/garminconnect")
                    Path(tokenstore).mkdir(parents=True, exist_ok=True)
                    client.login(tokenstore)
            except TokenStoreError as exc:
                logger.error("Could not read Garmin tokens from Supabase: %s", exc)
                raise HTTPException(
                    status_code=503,
                    detail="Token storage is unavailable; no Garmin login was attempted. Check service logs.",
                ) from exc
            except Exception as exc:
                # Do not retry login automatically: repeated auth attempts can trigger
                # Garmin rate limits (HTTP 429). Log only the error type, never details
                # that could contain credentials or tokens.
                _login_blocked_until = time.monotonic() + LOGIN_COOLDOWN_SECONDS
                logger.error(
                    "Garmin authentication failed (%s). New login attempts paused for %s seconds.",
                    type(exc).__name__,
                    LOGIN_COOLDOWN_SECONDS,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Garmin authentication failed. No automatic retry was attempted; check service logs before trying again.",
                ) from exc
            _client = client
            _last_saved_tokens = stored_tokens

    return _client


def persist_tokens() -> None:
    """Save the Garmin tokens to Supabase if they changed (new login or refresh)."""
    global _last_saved_tokens

    if _client is None:
        return
    try:
        store = SupabaseTokenStore.from_env()
    except TokenStoreError:
        return  # already reported by get_token_store()
    if store is None:
        return  # file storage: garminconnect writes the token file itself

    with _persist_lock:
        current = _client.client.dumps()
        if current == _last_saved_tokens:
            return
        if not json.loads(current).get("di_refresh_token"):
            logger.warning("Garmin session has no refresh token, so it cannot be stored for reuse")
            return
        try:
            store.save(current)
        except TokenStoreError as exc:
            logger.error("Could not save Garmin tokens to Supabase: %s", exc)
            return
        _last_saved_tokens = current
        logger.info("Saved updated Garmin tokens to Supabase")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "garmin-bridge", "status": "ok"}


@app.get("/auth/status", dependencies=[Depends(require_api_key)])
def auth_status() -> dict[str, Any]:
    """Check token storage without contacting Garmin (safe to call any time)."""
    status: dict[str, Any] = {
        "garmin_session_active": _client is not None,
        "login_paused_seconds": max(0, int(_login_blocked_until - time.monotonic())),
    }
    store = get_token_store()
    if store is None:
        status["token_storage"] = "file"
        return status

    status["token_storage"] = "supabase"
    try:
        stored = store.load()
    except TokenStoreError as exc:
        logger.error("Token status check failed: %s", exc)
        status["stored_tokens"] = "error"
        return status
    if stored is None:
        status["stored_tokens"] = "missing"
    elif json.loads(stored).get("di_refresh_token"):
        status["stored_tokens"] = "present"
    else:
        status["stored_tokens"] = "incomplete"
    return status


@app.get("/health/today", dependencies=[Depends(require_api_key)])
def health_today() -> dict[str, Any]:
    today = date.today().isoformat()
    try:
        client = get_client()
        return {"date": today, "data": client.get_user_summary(today)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Garmin health request failed; check service logs.") from exc
    finally:
        persist_tokens()


@app.get("/activities", dependencies=[Depends(require_api_key)])
def activities(days: int = 7) -> dict[str, Any]:
    if days < 1 or days > 30:
        raise HTTPException(status_code=400, detail="days must be between 1 and 30")

    end = date.today()
    start = end - timedelta(days=days - 1)
    try:
        client = get_client()
        data = client.get_activities_by_date(start.isoformat(), end.isoformat())
        return {"from": start.isoformat(), "to": end.isoformat(), "activities": data}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Garmin activity request failed; check service logs.") from exc
    finally:
        persist_tokens()
