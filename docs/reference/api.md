# API reference

`bincio serve` exposes a JSON API on `/api/*`. In production, nginx proxies these routes from the public domain. In local development, Vite proxies them from `astro dev`.

All request and response bodies are `application/json`. Authentication uses an httpOnly session cookie (`bincio_session`).

---

## Authentication

### `GET /api/me`

Returns the currently authenticated user, or 404 if not logged in.

**Response 200**
```json
{
  "handle": "dave",
  "display_name": "Dave",
  "is_admin": true
}
```

**Response 404** — not authenticated

---

### `POST /api/auth/login`

Rate-limited: 10 attempts per 15 minutes per IP.

**Request**
```json
{ "handle": "dave", "password": "your-password" }
```

**Response 200** — sets `bincio_session` cookie (httpOnly, SameSite=Lax, 30-day max-age)
```json
{ "ok": true, "handle": "dave", "display_name": "Dave" }
```

**Response 401** — invalid credentials  
**Response 429** — rate limit exceeded

---

### `POST /api/auth/logout`

Deletes the session from the database and clears the cookie.

**Response 200**
```json
{ "ok": true }
```

---

## Registration

### `POST /api/register`

Creates a new user account using a valid invite code.

**Request**
```json
{
  "code": "ABCD1234",
  "handle": "alice",
  "password": "my-password",
  "display_name": "Alice"
}
```

Handle rules: lowercase letters, numbers, `_`, `-`; 1–30 characters.  
Password: minimum 8 characters.

**Response 200** — sets session cookie, logs in immediately
```json
{ "ok": true, "handle": "alice" }
```

**Response 400** — invalid handle, password too short, or invalid/used invite code  
**Response 409** — handle already taken

---

## Invites

All invite endpoints require authentication.

### `GET /api/invites`

Lists invite codes created by the current user.

**Response 200**
```json
[
  {
    "code": "ABCD1234",
    "used": false,
    "used_by": null,
    "created_at": "2026-04-01T10:00:00Z",
    "used_at": null
  }
]
```

---

### `POST /api/invites`

Generates a new invite code for the current user. Regular users are limited to 3 invites; admins are unlimited.

**Response 200**
```json
{ "ok": true, "code": "EFGH5678" }
```

**Response 400** — invite limit reached

---

## Admin

### `GET /api/admin/users`

Lists all users. Admin only.

**Response 200**
```json
[
  {
    "handle": "dave",
    "display_name": "Dave",
    "is_admin": true,
    "created_at": "2026-03-01T00:00:00Z"
  }
]
```

**Response 403** — not an admin

---

## Write API

All write endpoints require authentication. Users can only read/write their own activities.

### `GET /api/activity/{activity_id}`

Returns the full activity JSON for an activity owned by the current user.

**Response 200** — BAS activity detail object  
**Response 404** — activity not found or not owned by user

---

### `POST /api/activity/{activity_id}`

Writes a sidecar edit for an activity. Triggers an incremental shard rebuild if `--site-dir` was passed to `bincio serve`.

**Request**
```json
{
  "title": "Epic climb",
  "description": "Rode with friends.",
  "sport": "cycling",
  "private": false,
  "highlight": false,
  "gear": "Trek Domane"
}
```

All fields are optional. Only provided fields are written to the sidecar.

**Response 200**
```json
{ "ok": true }
```

---

### `POST /api/strava/sync`

Triggers a Strava sync for the current user's data directory. Uses the stored OAuth token in `{handle}/strava_token.json`.

**Response 200**
```json
{ "new_count": 3, "error_count": 0 }
```

---

## Error format

All errors follow FastAPI's default format:

```json
{ "detail": "Invalid credentials" }
```

---

## Notes

- The session cookie is `SameSite=Lax`. The server sets `secure=False` because TLS termination is handled by nginx/caddy. If you serve `bincio serve` directly on HTTPS (not recommended), set `secure=True` in `server.py`.
- There is no CSRF protection — the API relies on the same-origin constraint enforced by `SameSite=Lax` cookies.
- The CORS policy allows `localhost:*` origins for local development only. Cross-origin requests from production domains are blocked — all traffic must go through the nginx proxy.
