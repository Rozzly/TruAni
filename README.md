<p align="center">
  <img src="docs/screenshots/logo.png" alt="TruAni" width="150">
</p>

<h1 align="center">TruAni</h1>
<p align="center">Seasonal Anime Manager for Sonarr</p>

---

TruAni keeps your Sonarr instance up to date with the continuous release cycles of anime on a seasonal basis. It pulls the latest title list from AniList, matches them against TVDB, lets you selectively ignore titles you don't want, and seamlessly imports the rest into Sonarr for management.

## Why use TruAni?

Sonarr supports import lists from popular sites, along with custom lists, however this relies on third-party accounts for API access and potential paywalls — *cough* Trakt *cough*. Alternatively, you can try to find existing lists in the correct format for Sonarr import, but that can be inconsistent and time consuming. TruAni is an easily deployed local app that provides a streamlined interface and automatic imports to make ingestion and management easy on a seasonal basis. What's another container in the stack?

> **Note:** This is a personal project that solves a real problem, built primarily as a learning experience. I run it in my own lab and fix issues as they come up, but I make no commitments to adding new features, functionality, or services that don't fit my use case. If it's useful to you too, great!

## Screenshots

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Dashboard"><br>
  <em>Dashboard — browse and manage seasonal titles</em>
</p>

<p align="center">
  <img src="docs/screenshots/scan.png" alt="Scanning"><br>
  <em>Scanning — real-time progress as titles are discovered and matched</em>
</p>

<p align="center">
  <img src="docs/screenshots/updates.png" alt="Updates"><br>
  <em>Updates — check for and install updates from within the app</em>
</p>

## Features

- **Automatic discovery** — Fetches current and upcoming titles from AniList
- **Smart matching** — Resolves TVDB IDs through Sonarr's lookup API with intelligent title matching
- **One-click imports** — Select the shows you want and push them straight to Sonarr with your preferred quality profile, root folder, and tags
- **Manual overrides** — If a title doesn't auto-match, you can set the TVDB ID yourself
- **Scheduled refreshes** — Runs in the background on your schedule (every 6h, 12h, daily, or weekly)
- **Ignore list** — Selectively ignore titles that you don't want imported into Sonarr
- **In-app updates** — Update to the latest version directly from the settings page
- **Light and dark themes** — Follows your system preference or toggle manually

## Getting Started

TruAni runs anywhere with network access to your Sonarr instance — pick whichever method fits your setup.

### Proxmox LXC (recommended for Proxmox users)

Run this in your Proxmox host shell and follow the prompts:

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Rozzly/TruAni/main/scripts/install-lxc.sh)"
```

This creates a Debian container, installs everything, and starts the service. You'll be prompted for container settings with suggested defaults.

### Any Debian/Ubuntu system

Run inside an existing VM or container:

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Rozzly/TruAni/main/scripts/install.sh)"
```

### Docker

```
git clone https://github.com/Rozzly/TruAni.git
cd TruAni
cp .env.example .env
docker compose up -d
```

### First login

Open `http://<your-host>:5656` in your browser. The default credentials are `truani` / `truani` — you'll be asked to change these immediately. After that, connect your Sonarr instance and you're ready to go.

## Updating

TruAni checks for updates weekly. When a new version is available, a banner appears on the dashboard.

**From the web UI:** Go to Settings > Updates and click "Update Now". The app pulls the latest code, installs any new dependencies, and restarts itself. That's it.

**From the command line (LXC):**

```
pct exec <CTID> -- update
```

**Docker:**

```
git pull origin main
docker compose up --build -d
```

## How it works

Each scan pulls the current anime season from AniList, then attempts to match every title to a TVDB ID using Sonarr's search API. It tries English titles, romaji titles, and known synonyms, strips season suffixes for broader matches, and validates results by genre and year. Anything it can't match automatically gets flagged so you can resolve it manually.

When you import, TruAni pushes each matched series to Sonarr with your configured settings. Series already in Sonarr are detected and skipped.

All data is stored in a single SQLite database. Everything can be re-fetched from AniList and Sonarr at any time, so there's nothing critical to back up.

## Security

TruAni is built for home networks and is not intended to be public facing, however basic security functionality is still included.

- All pages require login (session-based with bcrypt password hashing)
- Login rate limiting with automatic lockout after failed attempts
- Default credentials must be changed on first login
- Security headers on all responses
- Runs as a non-root user inside the container

## Requirements

- A running Sonarr instance (v3 or v4)
- Docker, a Debian/Ubuntu system, or Proxmox VE

## License

[MIT](LICENSE)
