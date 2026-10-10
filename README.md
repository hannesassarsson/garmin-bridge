# Garmin Bridge

A small, read-only FastAPI bridge for Garmin Connect.

## Endpoints

- `GET /` — public service health check
- `GET /health/today` — today's Garmin summary (requires API key)
- `GET /activities?days=7` — activities from the last 1–30 days (requires API key)

For the protected endpoints, send this HTTP header:

```http
Authorization: Bearer YOUR_BRIDGE_API_KEY
```

## Render environment variables

Configure these in the Render service's **Environment** settings:

- `GARMIN_EMAIL` — Garmin Connect email
- `GARMIN_PASSWORD` — Garmin Connect password
- `BRIDGE_API_KEY` — a long, random secret used to protect the bridge
- `GARMIN_TOKENSTORE` — optional directory for Garmin authentication tokens; set this to the mount path of a persistent disk if you have one (for example, `/data/garminconnect` when a disk is mounted at `/data`).

**Important:** Render Free web services have an ephemeral filesystem and do not support persistent disks. Without persistent storage, saved Garmin tokens can be lost when the service restarts or spins down, and a fresh login may be needed. Repeated logins can trigger Garmin rate limits (HTTP 429). This code deliberately does not automatically retry a failed login. Do not repeatedly test the protected endpoints after an authentication failure.

## Local run

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

This project uses the community `garminconnect` Python library. It is an unofficial integration and Garmin authentication/API behavior can change. Never commit Garmin credentials, API keys, or Garmin token files to GitHub.
