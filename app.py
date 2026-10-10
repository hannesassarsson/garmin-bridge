import os
from datetime import date, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from garminconnect import Garmin

app = FastAPI(title="Garmin Bridge", version="0.2.0")

_client: Garmin | None = None
_client_lock = Lock()


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


def get_client() -> Garmin:
    global _client

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise HTTPException(status_code=500, detail="Garmin credentials are not configured")

    if _client is not None:
        return _client

    with _client_lock:
        if _client is None:
            # Newer garminconnect releases use a resilient authentication flow and
            # can reuse saved DI tokens. Point GARMIN_TOKENSTORE at persistent storage
            # (e.g. a mounted Render disk) to survive service restarts.
            tokenstore = os.getenv("GARMIN_TOKENSTORE", "/data/garminconnect")
            Path(tokenstore).mkdir(parents=True, exist_ok=True)
            client = Garmin(email, password)
            try:
                client.login(tokenstore)
            except Exception as exc:
                # Do not retry login automatically: repeated auth attempts can trigger
                # Garmin rate limits (HTTP 429). Keep details in server logs only.
                raise HTTPException(
                    status_code=502,
                    detail="Garmin authentication failed. No automatic retry was attempted; check service logs before trying again.",
                ) from exc
            _client = client

    return _client


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "garmin-bridge", "status": "ok"}


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
