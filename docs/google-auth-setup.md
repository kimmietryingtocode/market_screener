# Google sign-in setup

Signalist uses Google Identity Services for authentication. It does not request
access to Gmail, Drive, Calendar, or other Google APIs, so the OAuth client
secret is not used.

## Google Cloud

Create an OAuth 2.0 client with application type **Web application** and add:

```text
http://localhost
http://localhost:4173
```

to **Authorized JavaScript origins**. The current JavaScript callback flow does
not need an authorized redirect URI.

While the OAuth app is in testing mode, add each Google account that should be
able to sign in as a test user.

## Local configuration

Paste the public client ID into the root `.env`:

```env
GOOGLE_CLIENT_ID=123456789-example.apps.googleusercontent.com
```

Do not paste the OAuth client secret into `frontend/`, `.env`, or source
control. This flow never exchanges an authorization code and therefore has no
legitimate use for that secret.

FastAPI publishes the public client ID at:

```text
GET http://localhost:8081/api/v1/auth/google/config
```

The frontend reads that endpoint automatically. After editing `.env`, restart
FastAPI, then start the frontend with:

```bash
cd frontend
npm start
```

Open `http://localhost:4173`. The included static server sends:

```text
Referrer-Policy: no-referrer-when-downgrade
Cross-Origin-Opener-Policy: same-origin-allow-popups
```

Both `http://localhost` and `http://localhost:4173` must be present exactly in
the Google client's Authorized JavaScript origins. If Google reports an origin
error, update the Google Cloud client and allow a few minutes for the change to
propagate.

The first database-backed run also needs migration `007_google_auth.sql`
applied with `python backend/migrate.py`.
