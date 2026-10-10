# Garmin Bridge

A small, read-only FastAPI bridge for Garmin Connect.

## Endpoints

- `GET /` — public service health check
- `GET /health/today` — today's Garmin summary (requires API key)
- `GET /activities?days=7` — activities from the last 1–30 days (requires API key)
- `GET /auth/status` — token storage status; never contacts Garmin, safe to call any time (requires API key)

For the protected endpoints, send this HTTP header:

```http
Authorization: Bearer YOUR_BRIDGE_API_KEY
```

## Render environment variables

Configure these in the Render service's **Environment** settings:

- `GARMIN_EMAIL` — Garmin Connect email
- `GARMIN_PASSWORD` — Garmin Connect password
- `BRIDGE_API_KEY` — a long, random secret used to protect the bridge
- `SUPABASE_URL` — Supabase project URL, e.g. `https://<project-ref>.supabase.co`
- `SUPABASE_SECRET_KEY` — a Supabase **secret** key (`sb_secret_...`), backend only
- `TOKEN_ENCRYPTION_KEY` — a long random value (use Render's **Generate** button); tokens are encrypted with it before they are sent to Supabase. Changing it makes stored tokens unreadable (one new Garmin login is then needed).
- `GARMIN_TOKENSTORE` — only used when the three Supabase variables are not set: optional directory for Garmin authentication tokens; set this to the mount path of a persistent disk if you have one (for example, `/data/garminconnect` when a disk is mounted at `/data`).

## Token storage in Supabase

Render Free has no persistent disk, so Garmin session tokens are stored in a private Supabase table (`supabase/garmin_tokens.sql`). The bridge encrypts the tokens (AES-256-GCM) before sending them, so Supabase only holds ciphertext. On start-up the bridge loads the stored tokens instead of logging in; refreshed tokens are saved back automatically. A credential login happens only when no usable tokens are stored. If Supabase cannot be reached, the bridge returns 503 and does **not** fall back to a password login. After a failed Garmin login, new login attempts are paused for 30 minutes (`GARMIN_LOGIN_COOLDOWN_SECONDS`).

Free Supabase projects are paused after a week without activity; the bridge then returns 503 until the project is restored in the Supabase dashboard.

**Important:** Render Free web services have an ephemeral filesystem and do not support persistent disks. Without persistent storage, saved Garmin tokens can be lost when the service restarts or spins down, and a fresh login may be needed. Repeated logins can trigger Garmin rate limits (HTTP 429). This code deliberately does not automatically retry a failed login. Do not repeatedly test the protected endpoints after an authentication failure.

## Tests

Offline tests (no Garmin or Supabase traffic):

```bash
pip install -r requirements.txt pytest httpx
pytest
```

## Local run

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

This project uses the community `garminconnect` Python library. It is an unofficial integration and Garmin authentication/API behavior can change. Never commit Garmin credentials, API keys, or Garmin token files to GitHub.
