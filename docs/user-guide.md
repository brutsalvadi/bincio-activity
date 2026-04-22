# User Guide

This guide covers everything you can do as a BincioActivity user.

## Getting Your Account

Your instance administrator sends you a registration link:

```
https://yourdomain.com/register/?code=ABCD1234
```

Click it and create:

- **Handle** — your username in URLs (lowercase letters, numbers, `_`, `-`; 1–30 chars)
- **Password** — at least 8 characters
- **Display name** — your full name (shown on your profile page)

You're now logged in and ready to upload activities!

## Uploading Activities

Click **Upload** to add activities from files.

### Supported formats

- **GPX** — GPS Exchange format (most common)
- **FIT** — Garmin's native format
- **TCX** — Training Center XML
- **Compressed files** — `.gz` variants of any format above

### Using Strava export

If you exported activities from Strava, you likely have a folder like:

```
activities/
  ├── 2026-03-15_morning_run.gpx
  ├── 2026-03-14_evening_ride.fit
  └── ...
```

Just drag the whole `activities/` folder into the upload box, or select multiple files at once.

### Upload options

- **Store original files** — keep the source GPX/FIT/TCX file on the server (checked by default; you can uncheck per upload)
- **Skip duplicates** — the system detects exact duplicates automatically

After upload, the server extracts GPS tracks, calculates distance/elevation/time, and generates your activity feed. You can keep uploading — the system deduplicates by file hash.

## Syncing from Strava

If your instance supports Strava sync, click **Sync from Strava** in the upload modal.

1. Authorize BincioActivity to read your Strava data
2. Select which activities to import
3. The server fetches GPS and metrics from Strava and stores them

Your OAuth token is stored securely on the server. You can revoke access at any time in [Strava Settings](https://www.strava.com/settings/apps).

## Editing Activities

Click **Edit** on any activity to:

- **Change the title** — rename the activity
- **Add a description** — write notes or a story (supports markdown and embedded images)
- **Upload photos** — add photos taken during the activity
- **Choose sport type** — cycling, running, hiking, etc.
- **Assign gear** — tag the bike/shoes/watch used
- **Set privacy** — hide the activity from the public feed
- **Highlight** — mark your favorite activities

Changes save instantly. The site rebuilds in the background.

### Recalculating elevation

If an activity shows an unrealistic elevation gain, the edit drawer has two buttons:

**📐 Recalculate (hysteresis)** — recomputes gain and loss from the original recorded
elevation using a noise-filtering dead-band algorithm. Fast and offline — no network
call. Best for devices with a barometric altimeter (Garmin, Karoo, Wahoo) whose
elevation data is accurate but was extracted before the noise-filtering was improved.

**⛰ Recalculate (DEM)** — replaces the recorded GPS altitude with SRTM terrain data
from the [Open-Elevation API](https://open-elevation.com) and recomputes gain and
loss. The elevation chart and summary stats both update. Best for GPS-only devices
(no barometric sensor) where the recorded altitude is noisy.

> **Note:** Both corrections require a GPS track (activities marked *No GPS* cannot be
> corrected). The DEM option uses ~30 m resolution terrain data; very short or indoor
> activities see little improvement from DEM correction.

### Photo gallery

Upload photos for an activity. They appear in a lightbox on the activity detail page. The server stores them in your data directory.

### Markdown in descriptions

Descriptions support basic markdown:

```markdown
# Title
**bold** _italic_ `code`

- bullet list
- another item

[link](https://example.com)

![image name](image.jpg)
```

Images are stored in `edits/images/{id}/` and paths are rewritten automatically.

## Privacy Control

Each activity has a privacy setting:

- **Public** (`public: true`) — visible to all logged-in users in the feed
- **Unlisted** (`private: true`) — not shown in the feed, but accessible by direct URL (for sharing)
- **No GPS** (remove GPS track) — hides the map but keeps distance/time stats

Your instance admin can also make the whole instance public or private.

### Deleting an activity

You can't delete an activity directly, but you can:

- Mark it **private** to hide it from the feed
- Edit the sidecar manually in `{data-root}/edits/{id}.md` and delete the file

## Your Profile

Click your name in the top-right to view your profile at `/u/{handle}/`. It shows:

- Your display name
- All your public activities (organized by year)
- Summary stats (total distance, time, elevation)

## Account Settings

Click your name → **Settings** to:

- **Change password** — update your account password
- **View your handle** — the username used in URLs
- **See your data** — information about what's stored on the server

If you forget your password, ask your instance administrator to generate a reset code.

## Feedback

Found a bug or want to suggest a feature? Click **Feedback** at the bottom of any page to submit a message and optional screenshots. The admin team can see all feedback submissions.

## Local Activity Conversion

If your instance has the `/convert/` page enabled, you can:

1. Upload a GPX/FIT/TCX file **locally in your browser** (no server upload)
2. The file is processed in JavaScript (powered by Pyodide, Python in the browser)
3. You see the activity preview immediately
4. You can then save it to your local browser storage (IndexedDB) or upload it to the server

This is useful for testing or converting files without uploading them first.

## Offline Activity Storage (experimental)

Activities converted locally are stored in your browser's **IndexedDB** (local storage). They:

- Don't upload to the server
- Persist across browser sessions
- Can be deleted from settings

This is useful for activities you don't want to publish yet, or for testing before uploading.

## Frequently Asked Questions

**Can I download my data?**  
Your instance's complete activity feed is at `/u/{handle}/index.json` (the BAS format). You can also ask the admin to copy your data directory directly.

**Can I transfer activities between instances?**  
Yes! Copy the `{handle}/activities/` and `{handle}/edits/` directories to another instance. The system uses content hashing, so you can merge multiple instances.

**What formats does my activity support?**  
BincioActivity extracts GPS tracks, distance, elevation, moving time, average speed, heart rate, power, cadence, and temperature (if available in the source file).

**Can I share my activities with someone outside my instance?**  
Mark activities as **unlisted** (`private: true`). Anyone with the direct URL can view them, even if they're not logged in.

**How do I delete my account?**  
Ask your instance administrator. They can delete your user record from `instance.db`, which removes you from the login system. Your activity data remains for audit, but can be deleted from disk if you request it.

## See also

- [Getting Started](getting-started.md) — initial setup
- [API Reference](reference/api.md) — technical details about how data flows
- [BAS Schema](schema.md) — the activity JSON format
- [Admin Guide](admin-guide.md) — for instance admins
