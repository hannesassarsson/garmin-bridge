import os
from datetime import date, timedelta
from threading import Lock

from fastapi import FastAPI, HTTPException
from garminconnect import Garmin

app = FastAPI(title="Garmin Bridge", version="0.1.1")

# Keep one Garmin client/session alive for the lifetime of this Render instance.
# This avoids logging in to Garmin again on every API request.
_client = None
_client_lock = Lock()


def get_client() -> Garmin:
    global _client

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise HTTPException(
            status_code=500,
            detail="GARMIN_EMAIL and GARMIN_PASSWORD are not configured",
        )

    if _client is not None:
        return _client

    with _client_lock:
        if _client is None:
            client = Garmin(email, password)
            client.login()
            _client = client

    return _client


@app.get("/")
def root():
    return {"service": "garmin-bridge", "status": "ok"}


@app.get("/health/today")
def health_today():
    today = date.today().isoformat()
    try:
        client = get_client()
        data = client.get_user_summary(today)
        return {"date": today, "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Garmin request failed: {exc}") from exc


@app.get("/activities")
def activities(days: int = 7):
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
        raise HTTPException(status_code=502, detail=f"Garmin request failed: {exc}") from exc
