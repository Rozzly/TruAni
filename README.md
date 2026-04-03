# TruAni

Seasonal anime manager for home media servers. TruAni bridges AniList and Sonarr to automatically discover, map, and import seasonal anime series.

## What it does

- Fetches current and upcoming season anime from AniList (TV and ONA formats, Japanese origin, filtered by popularity)
- Resolves TVDB IDs automatically via Sonarr lookups with intelligent title matching
- Syncs matched series directly to Sonarr with configurable quality profiles, root folders, and tags
- Tracks mapping status, episode counts, and Sonarr sync state per series
- Supports manual TVDB ID overrides for titles that don't auto-match
- Scheduled background refreshes (configurable: every 6h, 12h, daily, or weekly)
- Ignore list for filtering out unwanted titles
- In-app update system with weekly update checks

## Requirements

- A running Sonarr instance (v3 or v4)
- One of the following deployment targets:
  - Docker and Docker Compose
  - Proxmox VE (LXC container)
  - Any Debian/Ubuntu system

## Quick start

### Option 1: Proxmox LXC (one-liner)

Run on your Proxmox VE host:

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Rozzly/TruAni/main/scripts/install-lxc.sh)"
```

This creates a Debian 13 LXC container, installs TruAni, and starts the service. You will be prompted for container settings (ID, hostname, resources) with sensible defaults.

### Option 2: Any Debian/Ubuntu system

Run inside an existing LXC container, VM, or bare metal server:

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Rozzly/TruAni/main/scripts/install.sh)"
```

This installs TruAni as a systemd service at `/opt/truani`.

### Option 3: Docker

1. Clone the repository:

```
git clone https://github.com/Rozzly/TruAni.git
cd TruAni
```

2. Copy the example environment file:

```
cp .env.example .env
```

3. Build and start:

```
docker compose up -d
```

### After installation

1. Open `http://<your-host>:5656` in your browser.

2. Log in with the default credentials (`truani` / `truani`). You will be prompted to change these on first login.

3. Configure your Sonarr connection in the setup wizard.

## Configuration

### Environment variables

All Sonarr settings can also be configured through the web UI. Environment variables serve as initial defaults.

| Variable | Default | Description |
|---|---|---|
| `SONARR_URL` | `http://localhost:8989` | Sonarr instance URL |
| `SONARR_API_KEY` | (none) | Sonarr API key |
| `SONARR_ROOT_FOLDER` | `/tv/anime` | Root folder for imported series |
| `SONARR_QUALITY_PROFILE` | `HD-1080p` | Quality profile name |
| `SONARR_SERIES_TYPE` | `anime` | Series type (anime, standard, daily) |
| `SONARR_MONITOR` | `all` | Monitor option (all, future, missing, etc.) |
| `SONARR_SEASON_FOLDER` | `true` | Use season folders |
| `SONARR_SEARCH_ON_ADD` | `false` | Search for episodes when adding |
| `SONARR_TAGS` | (none) | Comma-separated tags to apply |
| `FLASK_PORT` | `5656` | Web server port |

### Reverse proxy

TruAni is designed for internal network use only and should not be exposed directly to the internet. If you run it behind a reverse proxy, point it at `http://truani:5656` (or wherever the container is reachable).

## Updating

TruAni checks for updates once per week (Sunday at 2 AM local time). When a new version is available, a dismissable banner appears on the dashboard.

**In-app update:** Go to Settings > Updates and click "Update Now". The app pulls the latest code, installs any new dependencies, and restarts itself.

**LXC manual update:** From the Proxmox host or LXC console:

```
pct exec <CTID> -- update
```

**Docker update:**

```
git pull origin main
docker compose up --build -d
```

## How it works

### Anime discovery

TruAni queries the AniList GraphQL API for TV and ONA anime in the current and next seasons, filtered to Japanese origin and excluding shorts (under 15 minutes). Results are sorted by popularity.

### TVDB ID mapping

For each anime, TruAni resolves a TVDB ID through a multi-step process:

1. Check the local database for a previously resolved or manually set mapping
2. Search Sonarr's lookup API using the anime's English title, romaji title, and synonyms
3. Validate results by checking genre (must be anime/Japanese), year range, and title similarity

Season suffixes are stripped for better matching (e.g., "Bleach: Thousand-Year Blood War" searches for "Bleach"). Manual overrides always take priority.

### Sonarr sync

When you sync (manually or for the full season), TruAni pushes each matched series to Sonarr via its API with your configured quality profile, root folder, tags, and monitoring options. Series already in Sonarr are detected and skipped.

## Data storage

TruAni uses a single SQLite database stored at `data/truani.db`. All data (anime metadata, TVDB mappings, Sonarr status) can be re-fetched from external APIs at any time. The database is not critical and can be deleted to start fresh.

User passwords are hashed with bcrypt. API keys are stored as plaintext in the database, which is only accessible from within the container or host filesystem.

## Security

TruAni is designed for deployment on home networks and local infrastructure. It should not be exposed to the public internet.

- All routes require authentication (session-based with bcrypt password hashing)
- Login rate limiting (10 failed attempts per IP triggers a 15-minute lockout)
- Sessions expire after 7 days
- Security headers: X-Content-Type-Options, X-Frame-Options, Referrer-Policy
- Session cookies: HttpOnly, SameSite=Lax
- Served by Waitress (production WSGI server), not Flask's development server
- Container runs as a non-root user
- First login forces a credential change from the default password
- SSRF protection on Sonarr URL configuration (HTTP/HTTPS only)

## Tech stack

- **Backend**: Python 3.13, Flask, Waitress
- **Database**: SQLite with WAL mode
- **Frontend**: Vanilla HTML/CSS/JavaScript with Jinja2 templates
- **Scheduling**: APScheduler
- **Auth**: bcrypt
- **Container**: Docker (python:3.13-slim)
- **APIs**: AniList GraphQL, Sonarr v3/v4

## License

MIT
