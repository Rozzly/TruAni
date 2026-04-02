"""Sonarr API client — adds series directly via the Sonarr v3/v4 API."""

import requests
import config
import db

_session = requests.Session()


def _headers():
    return {"X-Api-Key": config.sonarr_api_key()}


def _url(path):
    return f"{config.sonarr_url()}/api/v3{path}"


_conn_cache = {"result": None, "expires": 0}


def test_connection(url=None, api_key=None):
    """Test Sonarr connectivity. Optionally override URL/key (for testing unsaved form values).
    Cached for 60s when using saved credentials to avoid hitting Sonarr on every page load."""
    import time as _time
    custom = url or api_key
    url = (url or config.sonarr_url()).rstrip("/")
    api_key = api_key or config.sonarr_api_key()
    if not custom and not config.has_sonarr():
        return False, "Not configured"
    elif custom and (not api_key or api_key == "your_sonarr_api_key_here"):
        return False, "Not configured"
    if not url:
        return False, "Not configured"

    # Return cached result for default credentials (not custom test values)
    if not custom and _conn_cache["result"] and _time.monotonic() < _conn_cache["expires"]:
        return _conn_cache["result"]

    try:
        resp = _session.get(
            f"{url}/api/v3/system/status",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        version = resp.json().get("version", "unknown")
        result = True, f"Connected (v{version})"
    except requests.exceptions.ConnectionError:
        result = False, "Connection refused — check Sonarr URL"
    except requests.exceptions.Timeout:
        result = False, "Connection timed out"
    except Exception as e:
        result = False, str(e)

    if not custom:
        _conn_cache["result"] = result
        _conn_cache["expires"] = _time.monotonic() + 60
    return result


def lookup_series(tvdb_id):
    """Look up a series in Sonarr by TVDB ID. Returns title and basic info, or None."""
    if not config.sonarr_api_key():
        return None
    try:
        resp = _session.get(
            _url("/series/lookup"),
            headers=_headers(),
            params={"term": f"tvdb:{tvdb_id}"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        s = results[0]
        return _parse_series(s)
    except Exception:
        return None


def search_series(title):
    """Search Sonarr for a series by title. Returns list of matches with TVDB info."""
    if not config.sonarr_api_key():
        return []
    try:
        resp = _session.get(
            _url("/series/lookup"),
            headers=_headers(),
            params={"term": title},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
        seen = set()
        out = []
        for s in results:
            tvdb_id = s.get("tvdbId")
            if not tvdb_id or tvdb_id in seen:
                continue
            seen.add(tvdb_id)
            out.append(_parse_series(s))
            if len(out) >= 8:
                break
        return out
    except Exception:
        return []


def _parse_series(s):
    """Extract standard fields from a Sonarr series lookup result."""
    genres = [g.lower() for g in (s.get("genres") or [])]
    lang = s.get("originalLanguage") or {}

    return {
        "title": s.get("title"),
        "year": s.get("year"),
        "tvdbId": s.get("tvdbId"),
        "overview": (s.get("overview") or "")[:300],
        "network": s.get("network"),
        "status": s.get("status"),
        "lastAired": s.get("lastAired"),
        "isAnime": "animation" in genres or "anime" in genres,
        "isJapanese": lang.get("name", "").lower() == "japanese",
        "genres": genres,
    }


def get_quality_profiles():
    """Return list of quality profiles from Sonarr."""
    resp = _session.get(_url("/qualityprofile"), headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_root_folders():
    """Return list of root folders from Sonarr."""
    resp = _session.get(_url("/rootfolder"), headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_quality_profile_id():
    """Resolve quality profile name to ID."""
    profiles = get_quality_profiles()
    target = config.sonarr_quality_profile().lower()
    for profile in profiles:
        if profile["name"].lower() == target:
            return profile["id"]
    if profiles:
        return profiles[0]["id"]
    raise Exception("No quality profiles found in Sonarr")


def _fetch_all_series():
    """Fetch full series list from Sonarr. Cached for 5 minutes."""
    cached = db.get_cache("sonarr_all_series")
    if cached is not None:
        return cached
    resp = _session.get(_url("/series"), headers=_headers(), timeout=30)
    resp.raise_for_status()
    all_series = resp.json()
    db.set_cache("sonarr_all_series", all_series, ttl_seconds=300)
    return all_series


def get_existing_series():
    """Get set of TVDB IDs already in Sonarr."""
    return {s["tvdbId"] for s in _fetch_all_series()}


def get_tag_ids(tag_names):
    """Resolve tag names to IDs, creating tags if needed."""
    if not tag_names:
        return []

    resp = _session.get(_url("/tag"), headers=_headers(), timeout=10)
    resp.raise_for_status()
    existing = {t["label"].lower(): t["id"] for t in resp.json()}

    tag_ids = []
    for name in tag_names:
        name_lower = name.lower()
        if name_lower in existing:
            tag_ids.append(existing[name_lower])
        else:
            create_resp = _session.post(
                _url("/tag"),
                headers=_headers(),
                json={"label": name},
                timeout=10,
            )
            create_resp.raise_for_status()
            tag_ids.append(create_resp.json()["id"])

    return tag_ids


def _get_episode_count(sonarr_series):
    """Extract the latest season's episode count from a Sonarr series object."""
    seasons = sonarr_series.get("seasons") or []
    real_seasons = [s for s in seasons if s.get("seasonNumber", 0) > 0]
    if not real_seasons:
        return None
    latest = max(real_seasons, key=lambda s: s["seasonNumber"])
    stats = latest.get("statistics") or {}
    count = stats.get("totalEpisodeCount", 0)
    return count if count > 0 else None


def sync_all(anime_list):
    """Sync a list of anime (with tvdb_id) to Sonarr.
    Returns list of (anilist_id, status, message, episode_count)."""
    results = []

    # Reuse cached series list — avoids redundant /api/v3/series call
    try:
        all_series = _fetch_all_series()
    except Exception:
        all_series = []
    series_by_tvdb = {s["tvdbId"]: s for s in all_series}
    existing = set(series_by_tvdb.keys())

    quality_profile_id = get_quality_profile_id()
    tag_ids = get_tag_ids(config.sonarr_tags())

    for anime in anime_list:
        tvdb_id = anime.get("tvdb_id")
        if not tvdb_id:
            results.append((anime["anilist_id"], "skipped", "No TVDB ID", None))
            continue

        if tvdb_id in existing:
            ep_count = _get_episode_count(series_by_tvdb[tvdb_id])
            results.append((anime["anilist_id"], "exists", "Already in Sonarr", ep_count))
            continue

        title = anime.get("title_english") or anime.get("title_romaji") or "Unknown"

        try:
            resp = _session.get(
                _url("/series/lookup"),
                headers=_headers(),
                params={"term": f"tvdb:{tvdb_id}"},
                timeout=15,
            )
            resp.raise_for_status()
            lookup_results = resp.json()
        except Exception as e:
            results.append((anime["anilist_id"], "error", f"Lookup failed: {e}", None))
            continue

        if not lookup_results:
            results.append((anime["anilist_id"], "not_found", "Not found in Sonarr lookup", None))
            continue

        series_data = lookup_results[0]
        payload = {
            "tvdbId": tvdb_id,
            "title": series_data.get("title", title),
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": config.sonarr_root_folder(),
            "seriesType": config.sonarr_series_type(),
            "seasonFolder": config.sonarr_season_folder(),
            "monitored": True,
            "tags": tag_ids,
            "addOptions": {
                "monitor": config.sonarr_monitor(),
                "searchForMissingEpisodes": config.sonarr_search_on_add(),
                "searchForCutoffUnmetEpisodes": False,
            },
        }

        try:
            resp = _session.post(_url("/series"), headers=_headers(), json=payload, timeout=15)
            if resp.status_code == 400:
                body = resp.json()
                if any("already been added" in str(e) for e in body):
                    results.append((anime["anilist_id"], "exists", "Already in Sonarr", None))
                    existing.add(tvdb_id)
                    continue
                results.append((anime["anilist_id"], "error", str(body), None))
                continue
            resp.raise_for_status()
            # Fetch episode count from the newly added series
            added_series = resp.json()
            ep_count = _get_episode_count(added_series)
            results.append((anime["anilist_id"], "added", "Added to Sonarr", ep_count))
            existing.add(tvdb_id)
        except Exception as e:
            results.append((anime["anilist_id"], "error", str(e), None))

    # Invalidate cached series list if any were added
    if any(s == "added" for _, s, _, __ in results):
        db.set_cache("sonarr_all_series", None, ttl_seconds=0)

    return results
