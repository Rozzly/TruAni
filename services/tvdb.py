"""
TheTVDB v4 API client.
API docs: https://thetvdb.github.io/v4-api/
Requires a free project API key from https://thetvdb.com/dashboard/account/apikey
"""

import time
import requests
import db
import config

TVDB_BASE = "https://api4.thetvdb.com/v4"
_session = requests.Session()


def _get_token():
    """Authenticate and cache a TVDB bearer token (valid 30 days, we cache 24h)."""
    cached = db.get_cache("tvdb_token")
    if cached:
        return cached

    api_key = config.tvdb_api_key()
    if not api_key:
        return None

    try:
        resp = _session.post(
            f"{TVDB_BASE}/login",
            json={"apikey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        token = resp.json().get("data", {}).get("token")
        if token:
            db.set_cache("tvdb_token", token, ttl_seconds=86400)
            return token
    except Exception as e:
        print(f"[TVDB] Auth failed: {e}")

    return None


def _headers():
    token = _get_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def search_series(title, year=None):
    """Search TVDB for a series by title. Returns (tvdb_id, title) or None."""
    headers = _headers()
    if not headers:
        return None

    params = {"query": title, "type": "series"}
    if year:
        params["year"] = year

    try:
        resp = _session.get(
            f"{TVDB_BASE}/search",
            headers=headers,
            params=params,
            timeout=15,
        )
        if resp.status_code == 401:
            # Token expired, clear cache and retry once
            db.set_cache("tvdb_token", None, ttl_seconds=0)
            headers = _headers()
            if not headers:
                return None
            resp = _session.get(f"{TVDB_BASE}/search", headers=headers, params=params, timeout=15)

        if resp.status_code == 429:
            time.sleep(2)
            return None

        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        print(f"[TVDB] Search error for '{title}': {e}")
        return None

    if not data:
        return None

    # Filter for anime-like results: prefer JP country, type=series
    for result in data:
        country = result.get("country", "")
        primary_type = result.get("primary_type", "")
        if country and country.lower() in ("jpn", "jp"):
            tvdb_id = result.get("tvdb_id") or result.get("id")
            if tvdb_id:
                # TVDB search returns IDs as strings like "series-12345" or just the number
                tvdb_id = str(tvdb_id).replace("series-", "")
                try:
                    return int(tvdb_id), result.get("name", title)
                except ValueError:
                    continue

    # Fallback: return first series result if no JP match
    for result in data:
        tvdb_id = result.get("tvdb_id") or result.get("id")
        if tvdb_id:
            tvdb_id = str(tvdb_id).replace("series-", "")
            try:
                return int(tvdb_id), result.get("name", title)
            except ValueError:
                continue

    return None


def get_series(tvdb_id):
    """Look up a series by TVDB ID. Returns dict with title, year, overview, etc. or None."""
    headers = _headers()
    if not headers:
        return None

    try:
        resp = _session.get(
            f"{TVDB_BASE}/series/{tvdb_id}",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 401:
            db.set_cache("tvdb_token", None, ttl_seconds=0)
            headers = _headers()
            if not headers:
                return None
            resp = _session.get(f"{TVDB_BASE}/series/{tvdb_id}", headers=headers, timeout=15)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json().get("data", {})
        if not data:
            return None

        return {
            "title": data.get("name"),
            "year": data.get("year"),
            "tvdbId": data.get("id"),
            "overview": (data.get("overview") or "")[:250],
            "status": data.get("status", {}).get("name") if isinstance(data.get("status"), dict) else data.get("status"),
            "country": data.get("country"),
            "image": data.get("image"),
        }
    except Exception as e:
        print(f"[TVDB] Lookup error for id={tvdb_id}: {e}")
        return None
