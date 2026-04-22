# BincioActivity Documentation

Welcome to BincioActivity — a federated, self-hosted activity stats platform. This documentation is organized by audience:

## For Users

**[Getting Started](getting-started.md)** — Extract your activities from Strava/Garmin, set up a local site, and deploy it.

**[User Guide](user-guide.md)** — Upload activities, sync from Strava, edit titles/descriptions, manage photos, control privacy, configure your profile.

## For Administrators

**[Admin Guide](admin-guide.md)** — Deploy a multi-user instance, manage users, reset passwords, monitor rebuild status.

**[Multi-user Deployment](deployment/multi-user.md)** — Step-by-step setup with nginx, systemd, and multi-user architecture.

**[Single-user Deployment](deployment/single-user.md)** — Deploy as a read-only static site or with a local edit server.

## For Developers

**[Developer Guide](developer-guide.md)** — Local setup, how to run tests, architecture overview, how to contribute.

**[Architecture](architecture.md)** — BAS data format, shard model, federation protocol, federation design.

**[API Reference](reference/api.md)** — HTTP endpoints, request/response formats, authentication, rate limits.

**[CLI Reference](reference/cli.md)** — All bincio commands and options.

## Quick Links

- [GitHub repo](https://github.com/brutsalvadi/bincio-activity)
- [BAS Schema](schema.md) — The data format specification
- [Architecture diagram](architecture.mmd) (Mermaid diagram)
- Live Swagger UI at `/api/docs` (when server is running)

---

**Status:** This is early-stage, self-hosted software. See the [GitHub repo](https://github.com/brutsalvadi/bincio-activity) for known issues and planned features.
