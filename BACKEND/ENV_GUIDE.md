# Environment & database guide

## 1. How the database is chosen

The code does **not** hard-code a database. It reads one line from `BACKEND/.env`:

    DATABASE_URL=...

| You want | Set `DATABASE_URL` to |
|---|---|
| **Local development** (nothing to install) | `sqlite:///./bloodconnect.db` |
| **Production** (Render + Supabase/Postgres) | `postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME` |

If the line is missing, the built-in default is now SQLite (`sqlite:///./bloodconnect.db`).
Tables are created automatically on startup, and new columns are added automatically
(`app/migrate.py`), so you never run SQL by hand.

SQLite notes
- The path is relative to the folder you start the server from. Always start it from `BACKEND/`.
- The file `bloodconnect.db` is created on first start. Delete it to reset all data.
- Do **not** use SQLite on Render's free tier: its disk is wiped on every deploy. Use Postgres there.

Postgres notes
- If the password contains special characters, URL-encode them (`@` -> `%40`, `#` -> `%23`, `/` -> `%2F`).
  A raw `@` in the password breaks the URL.

## 2. Local development `.env` (copy this)

    DATABASE_URL=sqlite:///./bloodconnect.db
    SECRET_KEY=<paste a fresh random value, see step 3>
    ALGORITHM=HS256
    ACCESS_TOKEN_EXPIRE_MINUTES=10080
    CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://localhost:5500,http://127.0.0.1:5500
    DEFAULT_GEOFENCE_KM=25
    ADMIN_PHONES=<your own 10-digit number>
    DEMO_MODE=true
    DEMO_OTP_CODE=123456
    SMS_PROVIDER=none

With `DEMO_MODE=true` the login code is always `123456` and no SMS is sent.
**`DEMO_MODE=false` needs real SMS** (`SMS_PROVIDER=msg91` + keys). Without them the API answers
"SMS provider is not configured" and nobody can log in. That is intentional: it stops the OTP
leaking in the API response.

## 3. Step by step

1. Open `BACKEND/.env` (create it by copying `.env.example` if it does not exist).
2. **Make a new secret key** and paste it as `SECRET_KEY`:

       python -c "import secrets; print(secrets.token_urlsafe(48))"

3. Set `DATABASE_URL=sqlite:///./bloodconnect.db` for local work.
4. Set `ADMIN_PHONES` to your own number. Only numbers listed here can log in as admin.
5. Set `DEMO_MODE=true` locally. Leave `SMS_PROVIDER=none`.
6. Start the backend **from the BACKEND folder**:

       cd BACKEND
       python -m venv .venv && .venv\Scripts\activate      (Windows)   or   source .venv/bin/activate
       pip install -r requirements.txt
       uvicorn app.main:app --reload --port 8000

7. Check `http://127.0.0.1:8000/health` shows `{"status":"ok"}`.
8. Open the frontend with your local API once, e.g.
   `http://localhost:5500/HTML/login.html?api_base=http://127.0.0.1:8000/v1`
   The browser remembers it. Without this the pages talk to the **deployed** Render backend.
9. Log in with your phone number and code `123456`. To become admin, choose the admin login with
   your `ADMIN_PHONES` number.

## 4. Production `.env` (set these in the Render dashboard, never commit them)

    DATABASE_URL=postgresql+psycopg2://USER:URL_ENCODED_PASSWORD@HOST:5432/postgres
    SECRET_KEY=<a different long random value>
    CORS_ORIGINS=https://<your-vercel-domain>.vercel.app
    ADMIN_PHONES=<admin numbers>
    DEMO_MODE=false
    SMS_PROVIDER=msg91
    MSG91_AUTHKEY=...
    MSG91_OTP_TEMPLATE_ID=...
    MSG91_DONOR_ALERT_TEMPLATE_ID=...
    MSG91_HOSPITAL_ACCEPT_TEMPLATE_ID=...
    MSG91_BROADCAST_TEMPLATE_ID=...
    REQUIRE_VERIFIED_HOSPITALS=false

With `DEMO_MODE=false` the server refuses to start if `SECRET_KEY` is still a default value.
After changing variables on Render, redeploy. Then set `DEFAULT_API_BASE` in `FRONTEND/JS/auth.js`
if your Render URL changes.

## 5. Variable reference

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | SQLite file (local) or PostgreSQL (production) |
| `SECRET_KEY` | Signs login tokens. Change it if it ever leaks (this logs everyone out) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Login lifetime (10080 = 7 days) |
| `CORS_ORIGINS` | Websites allowed to call the API (comma separated, no trailing slash) |
| `DEFAULT_GEOFENCE_KM` | Default search radius for a new request |
| `ADMIN_PHONES` | Phones allowed to log in as admin |
| `DEMO_MODE` | `true` = fixed OTP, no SMS (demo only). `false` = real SMS |
| `DEMO_OTP_CODE` | The fixed code used in demo mode |
| `SMS_PROVIDER` | `none` or `msg91` |
| `MSG91_*` | MSG91 key and template ids (see `SMS_SETUP.md`) |
| `REQUIRE_VERIFIED_HOSPITALS` | `true` = only admin-verified hospitals can raise requests |
