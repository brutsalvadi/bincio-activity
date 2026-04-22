# Garmin Connect Sync — Disclaimer

**This feature uses an unofficial, community-maintained library to access Garmin Connect.
It is not affiliated with, endorsed by, or supported by Garmin Ltd. or its subsidiaries.**

---

## What this feature does

When you enable Garmin Connect sync, BincioActivity will:

1. Ask for your Garmin Connect **email address and password**
2. Store those credentials on the server, encrypted at rest
3. Use them to log in to Garmin Connect on your behalf and download your activity files (FIT format)
4. Import those activities into your BincioActivity account

---

## What you need to know before enabling this

### Your credentials are stored on the server

Unlike Strava (which uses OAuth — you authorize without sharing your password),
Garmin Connect has no official third-party API. This feature works by logging in
as you, using your actual email and password.

This means:

- The server operator has technical access to your stored credentials
- You are trusting both the software and the person running the server
- Only enable this on a server you control or run by someone you fully trust

### This uses an unofficial API

Garmin does not provide a public developer API for activity data.
This feature relies on a reverse-engineered interface that:

- May break without notice when Garmin changes their systems
- Is not covered by any Garmin service agreement or SLA
- May violate Garmin Connect's Terms of Service

BincioActivity takes no responsibility for account restrictions or bans
that may result from using this feature.

### Cloudflare bot protection and rate limiting

Garmin's login page (`sso.garmin.com`) is protected by Cloudflare, which
periodically blocks automated login attempts. When this happens, the sync
feature will fail at the login step with a "Login failed" error — even if
your credentials are correct.

The underlying `garth` library tries three login strategies in sequence.
A blocked session typically looks like this in the server logs:

```
mobile+cffi returned 429: Mobile login returned 429 — IP rate limited by Garmin
mobile+requests failed: Mobile login failed (non-JSON): HTTP 403
widget+cffi failed: Widget login: unexpected title 'GARMIN Authentication Application'
```

What each error means:
- **429** — Garmin is rate-limiting the server's IP address
- **403** — Cloudflare is blocking the request outright
- **unexpected title 'GARMIN Authentication Application'** — the login flow hit a
  CAPTCHA or MFA challenge page that the library cannot handle automatically

This is an upstream issue outside BincioActivity's control. The underlying
`garminconnect`/`garth` library usually releases a fix within days to weeks.
The workaround is to update those packages on the server:

```bash
uv sync --extra garmin
```

If login consistently fails despite updating, check the
[garminconnect issue tracker](https://github.com/cyberjunky/python-garminconnect/issues)
for the current status.

### Two-factor authentication (2FA)

If your Garmin account has 2FA enabled, this feature may not work or may
require additional steps. Garmin has changed their authentication flow
several times; compatibility depends on the current state of the underlying library.

### Rate limits

Garmin does not publish API rate limits. Syncing too frequently or importing
large volumes of activities may result in temporary or permanent IP blocks.
BincioActivity applies conservative limits, but cannot guarantee uninterrupted access.

---

## How to revoke access

BincioActivity does not hold an OAuth token that can be revoked from Garmin's settings.
To stop BincioActivity from accessing your Garmin account:

1. Delete your stored credentials from BincioActivity (Settings → Garmin Connect → Disconnect)
2. **Change your Garmin Connect password** — this is the only way to guarantee that
   no previously stored credentials can be used

---

## Recommendation

If you have concerns about credential storage, consider the alternative:
export your activities from Garmin Connect or Garmin Express as FIT files
and upload them directly to BincioActivity. This requires no credentials
and is always available.
