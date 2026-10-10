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

from activity_store import ActivityStoreError, SupabaseActivityStore
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
# Pause between per-activity Garmin requests during sync, to stay well clear of rate limits.
SYNC_REQUEST_DELAY_SECONDS = float(os.getenv("SYNC_REQUEST_DELAY_SECONDS", "1"))

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


def get_activity_store() -> SupabaseActivityStore:
    store = SupabaseActivityStore.from_env()
    if store is None:
        raise HTTPException(status_code=503, detail="Supabase is not configured; activities cannot be stored.")
    return store


def sync_details(client: Garmin, store: SupabaseActivityStore, max_activities: int) -> dict[str, int]:
    """Fetch full details and laps for the newest activities that lack them."""
    activity_ids, missing = store.missing_details(max_activities)
    for index, activity_id in enumerate(activity_ids):
        if index:
            time.sleep(SYNC_REQUEST_DELAY_SECONDS)
        details = client.get_activity(str(activity_id))
        time.sleep(SYNC_REQUEST_DELAY_SECONDS)
        splits = client.get_activity_splits(str(activity_id))
        store.save_details(activity_id, details, splits)
    return {"details_synced": len(activity_ids), "details_remaining": missing - len(activity_ids)}


def run_sync(work) -> dict[str, Any]:
    """Run a sync step with shared error handling; responses contain counts only."""
    store = get_activity_store()
    try:
        return work(get_client(), store)
    except HTTPException:
        raise
    except ActivityStoreError as exc:
        logger.error("Activity storage failed: %s", exc)
        raise HTTPException(status_code=503, detail="Activity storage failed; check service logs.") from exc
    except Exception as exc:
        logger.error("Garmin sync request failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Garmin sync request failed; check service logs.") from exc
    finally:
        persist_tokens()


@app.post("/sync", dependencies=[Depends(require_api_key)])
def sync(days: int = 3, max_details: int = 10) -> dict[str, Any]:
    """Daily sync: store the last few days' activities and fill in missing details."""
    if days < 1 or days > 30:
        raise HTTPException(status_code=400, detail="days must be between 1 and 30")
    if max_details < 0 or max_details > 50:
        raise HTTPException(status_code=400, detail="max_details must be between 0 and 50")

    def work(client: Garmin, store: SupabaseActivityStore) -> dict[str, Any]:
        end = date.today()
        start = end - timedelta(days=days - 1)
        saved = store.upsert_summaries(client.get_activities_by_date(start.isoformat(), end.isoformat()))
        return {"activities_saved": saved, **sync_details(client, store, max_details)}

    return run_sync(work)


@app.post("/sync/backfill", dependencies=[Depends(require_api_key)])
def sync_backfill(start: int = 0, limit: int = 50) -> dict[str, Any]:
    """Store one page of the full activity history (newest first). Call repeatedly with next_start."""
    if start < 0:
        raise HTTPException(status_code=400, detail="start must be 0 or more")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")

    def work(client: Garmin, store: SupabaseActivityStore) -> dict[str, Any]:
        page = client.get_activities(start, limit) or []
        saved = store.upsert_summaries(page)
        return {"activities_saved": saved, "next_start": start + len(page), "done": len(page) < limit}

    return run_sync(work)


@app.post("/sync/details", dependencies=[Depends(require_api_key)])
def sync_details_endpoint(max_details: int = 15) -> dict[str, Any]:
    """Fill in details and laps for stored activities that lack them."""
    if max_details < 1 or max_details > 50:
        raise HTTPException(status_code=400, detail="max_details must be between 1 and 50")
    return run_sync(lambda client, store: sync_details(client, store, max_details))
