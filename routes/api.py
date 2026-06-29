"""API routes (/api/*)."""

import functools
import json
import logging
import threading
import time as _time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request, session, Response

import config
import db
from core import (
    default_season, display_name, compute_stats,
    refresh_data, refresh_generator, sse_format,
    get_refresh_status, set_last_refresh, get_last_refresh,
    refresh_manager, RefreshBusy,
)
from routes.auth import login_required
from services.sonarr import test_connection, sync_all, lookup_series
from services.mapping import rescan_tvdb_id
from services.titleutil import display_title

log = logging.getLogger("truani")

api_bp = Blueprint("api", __name__, url_prefix="/api")


# --- Rate limiting for expensive endpoints ---

_api_rate = {}
_api_rate_lock = threading.Lock()
_API_RATE_MAX = 5
_API_RATE_WINDOW = 60


def rate_limit(key_prefix):
    """Decorator to rate-limit expensive endpoints per IP."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr
            key = f"{key_prefix}:{ip}"
            now = _time.monotonic()
            with _api_rate_lock:
                entry = _api_rate.get(key)
                if entry:
                    count, start = entry
                    if now - start > _API_RATE_WINDOW:
                        _api_rate[key] = (1, now)
                    elif count >= _API_RATE_MAX:
                        return jsonify({"status": "error", "message": "Too many requests. Please wait before trying again."}), 429
                    else:
                        _api_rate[key] = (count + 1, start)
                else:
                    _api_rate[key] = (1, now)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def _validate_url_scheme(url):
    """Return True if URL uses http or https scheme."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https")
    except (ValueError, AttributeError):
        return False


# --- Data endpoints ---

@api_bp.route("/anime")
@login_required
def api_anime():
    season, year = default_season(request.args.get("season", "").upper(), request.args.get("year", type=int))
    return jsonify(db.get_season_anime(season, year))


@api_bp.route("/season-data")
@login_required
def api_season_data():
    season, year = default_season(request.args.get("season", "").upper(), request.args.get("year", type=int))
    anime_list = db.get_season_anime(season, year)
    ignored_list = db.get_ignored_anime(season, year)
    for a in anime_list + ignored_list:
        a["display_title"] = display_title(a.get("title_english") or a.get("tvdb_title") or a.get("title_romaji") or "")
        a["display_romaji"] = display_title(a.get("title_romaji") or "")
    stats = compute_stats(anime_list, ignored_list)
    return jsonify({"anime": anime_list, "ignored": ignored_list, "stats": stats,
                     "season": season, "year": year})


@api_bp.route("/season/set-current", methods=["POST"])
@login_required
def api_set_current_season():
    """Pin a season as the current ('now') season. Persists an override that
    cascades through current_season()/next_season(), so the tab bar re-labels
    earlier seasons as past and surfaces the new 'next' tab automatically."""
    from services.anilist import SEASON_ORDER, next_season

    data = request.get_json(silent=True) or {}
    season = (data.get("season") or "").upper()
    year = data.get("year")

    if season not in SEASON_ORDER:
        return jsonify({"status": "error", "message": "Invalid season"}), 400
    try:
        year = int(year)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid year"}), 400

    db.save_setting("current_season_override", f"{season} {year}")
    config.clear_settings_cache()

    # next_season() now reads the new override; the season dropdown always offers
    # all four seasons, so the new "next" season is reachable immediately.
    nxt_season, nxt_year = next_season()
    return jsonify({
        "status": "ok",
        "message": f"{season.capitalize()} {year} is now the current season",
        "season": season,
        "year": year,
        "next": {"season": nxt_season, "year": nxt_year},
    })


# --- Refresh ---

@api_bp.route("/refresh", methods=["POST"])
@login_required
@rate_limit("refresh")
def api_refresh():
    data = request.get_json(silent=True) or {}
    season = data.get("season", "").upper() or None
    year = data.get("year") or None
    if year:
        year = int(year)
    result = refresh_data(season, year)
    return jsonify(result)


@api_bp.route("/refresh/stream")
@login_required
@rate_limit("refresh")
def api_refresh_stream():
    """SSE endpoint that streams refresh progress events."""
    season = request.args.get("season", "").upper() or None
    year = request.args.get("year", type=int) or None
    fresh = request.args.get("fresh") == "1"

    def generate():
        try:
            with refresh_manager.run():
                for sse_event in sse_format(refresh_generator(season, year, fresh=fresh, interactive=True)):
                    yield sse_event
                set_last_refresh(datetime.now(timezone.utc).isoformat())
        except RefreshBusy:
            yield 'data: {"step":"error","detail":"Refresh already in progress"}\n\n'
        except Exception as e:
            # GeneratorExit (client disconnect) is a BaseException and passes through.
            log.error("SSE refresh error: %s", e)
            yield f'data: {json.dumps({"step":"error","detail":"An unexpected error occurred"})}\n\n'

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Rescan ---

@api_bp.route("/rescan", methods=["POST"])
@login_required
@rate_limit("rescan")
def api_rescan():
    """Re-scan selected anime for TVDB ID mapping."""
    data = request.get_json(silent=True) or {}
    anilist_ids = data.get("anilist_ids", [])

    if not anilist_ids:
        return jsonify({"status": "error", "message": "No anime IDs provided"}), 400

    anime_list = db.get_anime_by_anilist_ids(anilist_ids)
    found = 0
    failed = 0
    updated = {}

    for anime in anime_list:
        tvdb_id, source, tvdb_title = rescan_tvdb_id(anime)
        if tvdb_id:
            db.set_tvdb_id(anime["anilist_id"], tvdb_id, source)
            if not tvdb_title:
                sonarr_info = lookup_series(tvdb_id)
                tvdb_title = sonarr_info["title"] if sonarr_info and sonarr_info.get("title") else None
            if tvdb_title:
                db.set_tvdb_title(anime["anilist_id"], tvdb_title)
            updated[anime["anilist_id"]] = {"tvdb_id": tvdb_id, "tvdb_title": tvdb_title, "mapping_source": source}
            found += 1
        else:
            failed += 1

    return jsonify({
        "status": "ok",
        "message": f"Rescan complete: {found} mapped, {failed} still unmapped",
        "found": found,
        "failed": failed,
        "updated": updated,
    })


# --- Ignore ---

@api_bp.route("/ignore", methods=["POST"])
@login_required
def api_ignore():
    """Ignore or unignore anime."""
    data = request.get_json(silent=True) or {}
    anilist_ids = data.get("anilist_ids", [])
    ignored = data.get("ignored", True)

    if not anilist_ids:
        return jsonify({"status": "error", "message": "No anime IDs provided"}), 400

    db.set_ignored_bulk(anilist_ids, ignored)
    action = "ignored" if ignored else "restored"
    return jsonify({"status": "ok", "message": f"{len(anilist_ids)} title(s) {action}"})


# --- TVDB ---

@api_bp.route("/tvdb/set", methods=["POST"])
@login_required
def api_tvdb_set():
    """Manually set or override a TVDB ID for an anime."""
    data = request.get_json(silent=True) or {}
    anilist_id = data.get("anilist_id")
    tvdb_id = data.get("tvdb_id")

    if not anilist_id:
        return jsonify({"status": "error", "message": "Missing anilist_id"}), 400

    anime = db.get_anime_by_anilist_id(anilist_id)
    if not anime:
        return jsonify({"status": "error", "message": "Anime not found"}), 404

    if not tvdb_id:
        db.set_tvdb_override(anilist_id, None, None)
        return jsonify({"status": "ok", "message": "TVDB mapping cleared"})

    try:
        tvdb_id = int(tvdb_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid TVDB ID"}), 400

    if anime.get("tvdb_id") == tvdb_id:
        return jsonify({
            "status": "ok",
            "message": f"TVDB ID unchanged ({tvdb_id})",
            "tvdb_id": tvdb_id,
            "tvdb_title": anime.get("tvdb_title"),
        })

    sonarr_info = lookup_series(tvdb_id)
    tvdb_title = sonarr_info["title"] if sonarr_info else None

    db.set_tvdb_override(anilist_id, tvdb_id, tvdb_title)

    return jsonify({
        "status": "ok",
        "message": f"TVDB ID set to {tvdb_id}" + (f" ({tvdb_title})" if tvdb_title else ""),
        "tvdb_id": tvdb_id,
        "tvdb_title": tvdb_title,
    })


@api_bp.route("/tvdb/verify", methods=["POST"])
@login_required
def api_tvdb_verify():
    """Verify a TVDB ID by looking it up via Sonarr."""
    data = request.get_json(silent=True) or {}
    tvdb_id = data.get("tvdb_id")

    if not tvdb_id:
        return jsonify({"status": "error", "message": "Missing tvdb_id"}), 400

    if not config.has_sonarr():
        return jsonify({"status": "not_configured", "message": "Sonarr not configured. Set up Sonarr in Settings to verify IDs."})

    try:
        tvdb_id = int(tvdb_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid TVDB ID"}), 400

    info = lookup_series(tvdb_id)
    if not info:
        return jsonify({"status": "error", "message": "TVDB ID not found"}), 404

    info["source"] = "sonarr"
    return jsonify({"status": "ok", "series": info})


# --- Sync ---

@api_bp.route("/sync", methods=["POST"])
@login_required
@rate_limit("sync")
def api_sync():
    """Sync selected anime to Sonarr."""
    data = request.get_json(silent=True) or {}
    selected_ids = data.get("anilist_ids")

    if selected_ids:
        anime_list = db.get_anime_by_anilist_ids(selected_ids)
    else:
        season, year = default_season(
            (data.get("season") or "").upper() or None,
            int(data["year"]) if data.get("year") else None,
        )
        anime_list = db.get_season_anime(season, year)

    syncable = [a for a in anime_list if a.get("tvdb_id")]

    if not syncable:
        return jsonify({"status": "ok", "message": "Nothing to sync (no TVDB IDs)", "summary": {}})

    try:
        results = sync_all(syncable)
    except Exception as e:
        log.error("Sync error: %s", e)
        return jsonify({"status": "error", "message": "An unexpected error occurred during sync"}), 500

    statuses = {}
    details = []
    title_map = {a["anilist_id"]: display_name(a) for a in syncable}
    for anilist_id, status, message, ep_count in results:
        db.update_sonarr_status(anilist_id, status)
        if ep_count:
            db.update_episode_count(anilist_id, ep_count)
        statuses[anilist_id] = status
        details.append({"anilist_id": anilist_id, "title": title_map.get(anilist_id, "?"), "status": status, "message": message})

    summary = {
        "added": sum(1 for _, s, _, __ in results if s == "added"),
        "exists": sum(1 for _, s, _, __ in results if s == "exists"),
        "errors": sum(1 for _, s, _, __ in results if s == "error"),
        "skipped": sum(1 for _, s, _, __ in results if s == "skipped"),
        "not_found": sum(1 for _, s, _, __ in results if s == "not_found"),
    }

    return jsonify({"status": "ok", "message": "Sync complete", "summary": summary, "statuses": statuses, "details": details})


# --- Settings ---

@api_bp.route("/settings", methods=["POST"])
@login_required
def api_settings():
    data = request.get_json(silent=True) or {}

    allowed_keys = {
        "sonarr_url", "sonarr_api_key", "sonarr_root_folder",
        "sonarr_quality_profile", "sonarr_series_type", "sonarr_monitor",
        "sonarr_season_folder", "sonarr_search_on_add", "sonarr_tags",
        "refresh_frequency", "refresh_time", "refresh_day",
        "setup_complete",
    }

    to_save = {k: v for k, v in data.items() if k in allowed_keys}

    if "sonarr_url" in to_save and to_save["sonarr_url"]:
        if not _validate_url_scheme(to_save["sonarr_url"]):
            return jsonify({"status": "error", "message": "Sonarr URL must use http:// or https://"}), 400

    db.save_settings(to_save)
    config.clear_settings_cache()

    if to_save.keys() & {"refresh_frequency", "refresh_time", "refresh_day"}:
        from scheduler import reschedule
        reschedule()

    return jsonify({"status": "ok", "message": "Settings saved"})


@api_bp.route("/settings/test-sonarr", methods=["POST"])
@login_required
def api_test_sonarr():
    data = request.get_json(silent=True) or {}
    test_url = data.get("sonarr_url")
    if test_url and not _validate_url_scheme(test_url):
        return jsonify({"connected": False, "message": "URL must use http:// or https://"})
    ok, msg = test_connection(
        url=test_url or None,
        api_key=data.get("sonarr_api_key") or None,
    )
    return jsonify({"connected": ok, "message": msg})


@api_bp.route("/settings/sonarr-options", methods=["POST"])
@login_required
def api_sonarr_options():
    """Fetch root folders and quality profiles from a Sonarr instance."""
    data = request.get_json(silent=True) or {}
    url = (data.get("sonarr_url") or config.sonarr_url() or "").rstrip("/")
    api_key = data.get("sonarr_api_key") or config.sonarr_api_key()

    if url and not _validate_url_scheme(url):
        return jsonify({"status": "error", "message": "URL must use http:// or https://"}), 400

    if not url or not api_key or api_key == "your_sonarr_api_key_here":
        return jsonify({"status": "error", "message": "URL and API key required"}), 400

    headers = {"X-Api-Key": api_key}
    result = {"status": "ok", "root_folders": [], "quality_profiles": []}

    def fetch_root_folders():
        resp = requests.get(f"{url}/api/v3/rootfolder", headers=headers, timeout=10)
        resp.raise_for_status()
        return [{"path": rf.get("path", ""), "freeSpace": rf.get("freeSpace", 0)} for rf in resp.json()]

    def fetch_quality_profiles():
        resp = requests.get(f"{url}/api/v3/qualityprofile", headers=headers, timeout=10)
        resp.raise_for_status()
        return [{"id": qp.get("id"), "name": qp.get("name", "")} for qp in resp.json()]

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        rf_future = pool.submit(fetch_root_folders)
        qp_future = pool.submit(fetch_quality_profiles)
        try:
            result["root_folders"] = rf_future.result(timeout=15)
        except Exception as e:
            log.warning("Failed to fetch root folders: %s", e)
        try:
            result["quality_profiles"] = qp_future.result(timeout=15)
        except Exception as e:
            log.warning("Failed to fetch quality profiles: %s", e)

    return jsonify(result)


@api_bp.route("/settings/user", methods=["POST"])
@login_required
def api_update_user():
    data = request.get_json(silent=True) or {}
    current_user = session["user"]

    new_username = data.get("username", "").strip()
    new_password = data.get("password", "").strip()
    current_password = data.get("current_password", "")

    if not db.verify_password(current_user, current_password):
        return jsonify({"status": "error", "message": "Current password is incorrect"}), 400

    if new_username and new_username != current_user:
        if db.get_user_by_username(new_username):
            return jsonify({"status": "error", "message": "Username already taken"}), 400
        db.update_username(current_user, new_username)
        session["user"] = new_username

    if new_password:
        err = db.validate_password(new_password)
        if err:
            return jsonify({"status": "error", "message": err}), 400
        db.update_password(session["user"], new_password)

    return jsonify({"status": "ok", "message": "User updated"})


@api_bp.route("/cache/clear", methods=["POST"])
@login_required
def api_clear_cache():
    """Clear all cached data."""
    db.clear_cache()
    return jsonify({"status": "ok", "message": "Cache cleared"})


@api_bp.route("/status")
@login_required
def api_status():
    sonarr_ok, sonarr_msg = test_connection()
    return jsonify({
        "refresh_status": get_refresh_status(),
        "last_refresh": get_last_refresh(),
        "sonarr": {"connected": sonarr_ok, "message": sonarr_msg},
    })


# --- Update ---

@api_bp.route("/update/check")
@login_required
def api_update_check():
    from services.updater import check_for_update
    force = request.args.get("force") == "1"
    return jsonify(check_for_update(force=force))


@api_bp.route("/update/changelog")
@login_required
def api_update_changelog():
    from services.updater import get_changelog
    return jsonify(get_changelog())


@api_bp.route("/update/apply", methods=["POST"])
@login_required
def api_update_apply():
    from services.updater import perform_update, schedule_restart
    result = perform_update()
    if result.get("success") and result.get("restart_required"):
        schedule_restart()
    return jsonify(result)
