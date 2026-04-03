import functools
import json
import threading
import time as _time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests

from flask import (
    Flask, render_template, jsonify, request,
    redirect, url_for, session, flash, Response
)

import config
import db
from services.anilist import current_season, next_season, fetch_seasonal_anime
from services.mapping import resolve_tvdb_id, rescan_tvdb_id
from services.sonarr import test_connection, sync_all, lookup_series, get_existing_series

app = Flask(__name__)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.permanent_session_lifetime = timedelta(days=7)

from services.titleutil import strip_season_suffix, display_title
app.jinja_env.filters['display_title'] = display_title


@app.context_processor
def _inject_globals():
    return {"app_version": config.APP_VERSION}


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.teardown_appcontext
def _close_db(exc):
    db.close_connection()

_refresh_lock = threading.Lock()
_refresh_status = "idle"

def _get_last_refresh():
    return db.get_setting("last_refresh")

def _set_last_refresh(iso_str):
    db.save_setting("last_refresh", iso_str)


def _default_season(season=None, year=None):
    """Default season/year to current if not provided."""
    if not season or not year:
        s, y = current_season()
        season = season or s
        year = year or y
    return season, year


def _display_name(anime):
    """Get best display title for an anime dict."""
    return anime.get("title_english") or anime.get("tvdb_title") or anime.get("title_romaji") or "?"


def _compute_stats(anime_list, ignored_list=None):
    """Compute stats dict for a season's anime."""
    return {
        "total": len(anime_list),
        "mapped": sum(1 for a in anime_list if a["tvdb_id"]),
        "unmapped": sum(1 for a in anime_list if not a["tvdb_id"]),
        "added": sum(1 for a in anime_list if a["sonarr_status"] in ("added", "exists")),
        "ignored": len(ignored_list) if ignored_list is not None else 0,
    }


def _build_entry(anime, tvdb_id, tvdb_title, source, sonarr_tvdb_ids):
    """Build an entry dict for upserting anime with TVDB mapping."""
    return {
        **anime,
        "tvdb_id": tvdb_id,
        "tvdb_title": tvdb_title,
        "mapping_source": source,
        "sonarr_status": "exists" if (tvdb_id and tvdb_id in sonarr_tvdb_ids) else "pending",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# --- Auth ---

_login_attempts = {}  # IP -> (fail_count, first_failure_time)
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes


def _check_rate_limit(ip):
    """Return (is_locked, seconds_remaining). Cleans up stale entries."""
    now = _time.monotonic()
    entry = _login_attempts.get(ip)
    if not entry:
        return False, 0
    count, first_failure = entry
    if now - first_failure > _LOGIN_LOCKOUT_SECONDS:
        del _login_attempts[ip]
        return False, 0
    if count >= _LOGIN_MAX_ATTEMPTS:
        return True, int(_LOGIN_LOCKOUT_SECONDS - (now - first_failure))
    return False, 0


def _record_failure(ip):
    now = _time.monotonic()
    entry = _login_attempts.get(ip)
    if entry and now - entry[1] <= _LOGIN_LOCKOUT_SECONDS:
        _login_attempts[ip] = (entry[0] + 1, entry[1])
    else:
        _login_attempts[ip] = (1, now)


def _clear_failures(ip):
    _login_attempts.pop(ip, None)


def _validate_url_scheme(url):
    """Return True if URL uses http or https scheme."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "Not authenticated"}), 401
            return redirect(url_for("login"))
        if not db.get_setting("setup_complete") and request.endpoint not in ("setup", "api_settings", "api_test_sonarr", "api_sonarr_options"):
            return redirect(url_for("setup"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr
        locked, remaining = _check_rate_limit(ip)
        if locked:
            flash(f"Too many failed attempts. Try again in {remaining // 60 + 1} minutes.", "error")
            return render_template("login.html"), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if db.verify_password(username, password):
            _clear_failures(ip)
            session["user"] = username
            session.permanent = True
            if not db.get_setting("setup_complete"):
                return redirect(url_for("setup"))
            return redirect(url_for("index"))
        _record_failure(ip)
        flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """First-login setup: force credential change."""
    if db.get_setting("setup_complete"):
        return redirect(url_for("index"))
    if not session.get("user"):
        return redirect(url_for("login"))

    if request.method == "POST":
        is_json = request.is_json
        data = request.get_json(silent=True) if is_json else request.form
        new_username = (data.get("username") or "").strip()
        new_password = data.get("new_password") or ""
        confirm = data.get("confirm_password") or ""

        error = None
        field = "new_password"
        if not new_username:
            error, field = "Username is required", "username"
        elif not new_password:
            error = "Password is required"
        elif new_password != confirm:
            error, field = "Passwords do not match", "confirm_password"
        else:
            err = db.validate_password(new_password)
            if err:
                error = err
            else:
                old_username = session["user"]
                if new_username != old_username:
                    if db.get_user_by_username(new_username):
                        error, field = "Username already taken", "username"

        if error:
            if is_json:
                return jsonify({"status": "error", "message": error, "field": field}), 400
            flash(error, "error")
            return render_template("setup.html")

        if new_username != session["user"]:
            db.update_username(session["user"], new_username)
            session["user"] = new_username
        db.update_password(session["user"], new_password)
        db.save_setting("credentials_set", "true")

        if is_json:
            return jsonify({"status": "ok"})
        flash("Account secured! Welcome to TruAni.", "success")
        return redirect(url_for("setup"))

    step = 2 if db.get_setting("credentials_set") else 1
    return render_template("setup.html", settings=db.get_all_settings(), step=step)


# --- Core Logic ---

def _sse_format(generator):
    """Wrap a dict-yielding generator into SSE-formatted strings."""
    for d in generator:
        yield f"data: {json.dumps(d)}\n\n"


def _refresh_generator(season=None, year=None, fresh=False):
    """Generator that yields event dicts during refresh."""

    def event(step, detail, progress=None, **extra):
        d = {"step": step, "detail": detail, **extra}
        if progress is not None:
            d["progress"] = progress
        return d

    if not season or not year:
        season, year = current_season()

    yield event("fetch", f"Fetching {season.capitalize()} {year} from AniList...", progress=2)

    try:
        anime_list = fetch_seasonal_anime(season, year)
    except Exception as e:
        yield event("error", f"AniList fetch failed: {e}")
        return

    # Fresh scan: remove existing season data AFTER successful fetch to avoid data loss
    if fresh:
        deleted = db.delete_season_anime(season, year)
        yield event("fetch", f"Cleared {deleted} existing entries for {season.capitalize()} {year}", progress=4)

    # Get set of ignored anilist_ids to skip expensive lookups
    ignored_ids = db.get_ignored_ids()
    ignored_count = 0

    # Separate ignored from active for accurate counts
    active_list = []
    for anime in anime_list:
        if anime["anilist_id"] in ignored_ids:
            ignored_count += 1
        else:
            active_list.append(anime)

    total = len(active_list)
    yield event("fetch", f"Found {total} titles" + (f" ({ignored_count} ignored)" if ignored_count else ""), progress=6, count=total)

    # Refresh Sonarr status — check which TVDB IDs are actually in Sonarr
    sonarr_tvdb_ids = set()
    try:
        sonarr_tvdb_ids = get_existing_series()
        yield event("mapping", f"Found {len(sonarr_tvdb_ids)} series in Sonarr", progress=10)
    except Exception:
        yield event("mapping", "Could not connect to Sonarr — status unchanged", progress=10)

    # Upsert ignored items with existing DB data (no expensive lookups)
    if ignored_count:
        ignored_anime = [a for a in anime_list if a["anilist_id"] in ignored_ids]
        existing_ignored = {r["anilist_id"]: r for r in db.get_anime_by_anilist_ids([a["anilist_id"] for a in ignored_anime])}
        for anime in ignored_anime:
            existing = existing_ignored.get(anime["anilist_id"])
            ex_tvdb = existing.get("tvdb_id") if existing else None
            entry = _build_entry(
                anime, ex_tvdb,
                existing.get("tvdb_title") if existing else None,
                existing.get("mapping_source") if existing else None,
                sonarr_tvdb_ids,
            )
            db.upsert_anime(entry)

    # Batch pre-fetch existing DB records to avoid N individual queries
    existing_records = {r["anilist_id"]: r for r in db.get_anime_by_anilist_ids([a["anilist_id"] for a in active_list])}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from services.mapping import _sonarr_lookup

    mapped = 0
    unmapped = 0
    processed = 0
    need_sonarr = []  # anime dicts that need Sonarr lookup

    def _match_pct():
        return 12 + int((processed / max(total, 1)) * 86)

    def _fetch_title(anime, existing, tvdb_id):
        """Fetch TVDB title via Sonarr if not already known."""
        if existing and existing.get("tvdb_title"):
            return existing["tvdb_title"]
        try:
            info = lookup_series(tvdb_id)
            if info and info.get("title"):
                db.set_tvdb_title(anime["anilist_id"], info["title"])
                return info["title"]
        except Exception:
            pass
        return None

    def _full_resolve(item):
        """Worker: Sonarr lookup + title fetch, all in one shot."""
        idx, anime, existing = item
        tvdb_id, source, tvdb_title = _sonarr_lookup(anime)
        if tvdb_id and not tvdb_title:
            tvdb_title = _fetch_title(anime, existing, tvdb_id)
        return idx, anime, existing, tvdb_id, source, tvdb_title

    # --- Pass 1: Fast resolve (DB cache only — no network calls) ---
    for anime in active_list:
        existing = existing_records.get(anime["anilist_id"])
        tvdb_id, source, tvdb_title, existing = resolve_tvdb_id(anime, existing=existing, skip_sonarr=True)

        if tvdb_id:
            title = _display_name(anime)
            db.upsert_anime(_build_entry(anime, tvdb_id, tvdb_title, source, sonarr_tvdb_ids))
            mapped += 1
            processed += 1
            yield event("match", title, progress=_match_pct(), matched=True, tvdb_id=tvdb_id, index=processed, total=total)
        else:
            need_sonarr.append((0, anime, existing))

    # --- Pass 2: Parallel Sonarr lookups (match + title + episodes per item) ---
    if need_sonarr:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_full_resolve, item): item for item in need_sonarr}
            for future in as_completed(futures):
                idx, anime, existing, tvdb_id, source, tvdb_title = future.result()
                title = _display_name(anime)
                processed += 1

                db.upsert_anime(_build_entry(anime, tvdb_id, tvdb_title, source, sonarr_tvdb_ids))

                if tvdb_id:
                    mapped += 1
                    yield event("match", title, progress=_match_pct(), matched=True, tvdb_id=tvdb_id, index=processed, total=total)
                else:
                    unmapped += 1
                    yield event("match", title, progress=_match_pct(), matched=False, index=processed, total=total)
    else:
        unmapped = total - mapped

    yield event("done", f"{total} titles processed: {mapped} mapped, {unmapped} unmapped",
                progress=100, mapped=mapped, unmapped=unmapped, total=total)


def refresh_data(season=None, year=None):
    """Non-streaming refresh for scheduler use. Returns summary dict."""
    global _refresh_status

    if not _refresh_lock.acquire(blocking=False):
        return {"status": "busy", "message": "Refresh already in progress"}

    try:
        _refresh_status = "running"
        result = None
        for event_dict in _refresh_generator(season, year):
            if event_dict.get("step") in ("done", "error"):
                result = event_dict

        _set_last_refresh(datetime.now(timezone.utc).isoformat())
        _refresh_status = "idle"

        if result and result.get("step") == "done":
            return {"status": "ok", "message": result["detail"],
                    "total": result.get("total", 0), "mapped": result.get("mapped", 0), "unmapped": result.get("unmapped", 0)}
        elif result and result.get("step") == "error":
            return {"status": "error", "message": result["detail"]}
        return {"status": "ok", "message": "Refresh complete"}

    except Exception as e:
        _refresh_status = "error"
        print(f"[Refresh] Error: {e}")
        return {"status": "error", "message": "An unexpected error occurred during refresh"}
    finally:
        _refresh_lock.release()


def _build_season_tabs(active_season, active_year):
    """Build the list of season tabs for the UI."""
    from services.anilist import SEASON_ORDER
    cur_season, cur_year = current_season()
    nxt_season, nxt_year = next_season()

    db_seasons = db.get_all_seasons()

    tab_set = set(db_seasons)
    tab_set.add((cur_season, cur_year))
    tab_set.add((nxt_season, nxt_year))

    season_rank = {s: i for i, s in enumerate(SEASON_ORDER)}

    def sort_key(t):
        return (t[1], season_rank.get(t[0], 0))

    cur_rank = sort_key((cur_season, cur_year))

    # Sort oldest first (left=past, right=future) so current is naturally centered
    tabs = sorted(tab_set, key=sort_key)

    return [
        {
            "season": s,
            "year": y,
            "label": f"{s.capitalize()} {y}",
            "active": s == active_season and y == active_year,
            "is_current": s == cur_season and y == cur_year,
            "is_next": s == nxt_season and y == nxt_year,
            "is_past": sort_key((s, y)) < cur_rank,
        }
        for s, y in tabs
    ]


# --- Pages ---

@app.route("/")
@login_required
def index():
    season, year = _default_season(request.args.get("season", "").upper(), request.args.get("year", type=int))

    anime_list = db.get_season_anime(season, year)
    ignored_list = db.get_ignored_anime(season, year)

    stats = _compute_stats(anime_list, ignored_list)

    sonarr_ok, sonarr_msg = test_connection()
    tabs = _build_season_tabs(season, year)

    return render_template(
        "index.html",
        anime_list=anime_list,
        ignored_list=ignored_list,
        season=season,
        year=year,
        stats=stats,
        sonarr_ok=sonarr_ok,
        sonarr_msg=sonarr_msg,
        last_refresh=_get_last_refresh(),
        refresh_status=_refresh_status,
        user=session.get("user"),
        tabs=tabs,
    )


@app.route("/settings")
@login_required
def settings_page():
    sonarr_ok, sonarr_msg = test_connection()
    return render_template(
        "settings.html",
        settings=db.get_all_settings(),
        sonarr_ok=sonarr_ok,
        sonarr_msg=sonarr_msg,
        user=session.get("user"),
    )


# --- API ---

@app.route("/api/anime")
@login_required
def api_anime():
    season, year = _default_season(request.args.get("season", "").upper(), request.args.get("year", type=int))
    return jsonify(db.get_season_anime(season, year))


@app.route("/api/season-data")
@login_required
def api_season_data():
    season, year = _default_season(request.args.get("season", "").upper(), request.args.get("year", type=int))
    anime_list = db.get_season_anime(season, year)
    ignored_list = db.get_ignored_anime(season, year)
    # Apply display_title to each item so JS doesn't need to replicate the logic
    for a in anime_list + ignored_list:
        a["display_title"] = display_title(a.get("title_english") or a.get("tvdb_title") or a.get("title_romaji") or "")
        a["display_romaji"] = display_title(a.get("title_romaji") or "")
    stats = _compute_stats(anime_list, ignored_list)
    return jsonify({"anime": anime_list, "ignored": ignored_list, "stats": stats,
                     "season": season, "year": year})



@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    data = request.get_json(silent=True) or {}
    season = data.get("season", "").upper() or None
    year = data.get("year") or None
    if year:
        year = int(year)
    result = refresh_data(season, year)
    return jsonify(result)


@app.route("/api/refresh/stream")
@login_required
def api_refresh_stream():
    """SSE endpoint that streams refresh progress events."""
    season = request.args.get("season", "").upper() or None
    year = request.args.get("year", type=int) or None
    fresh = request.args.get("fresh") == "1"

    if not _refresh_lock.acquire(blocking=False):
        def busy():
            yield 'data: {"step":"error","detail":"Refresh already in progress"}\n\n'
        return Response(busy(), mimetype="text/event-stream")

    def generate():
        global _refresh_status
        try:
            _refresh_status = "running"
            for sse_event in _sse_format(_refresh_generator(season, year, fresh=fresh)):
                yield sse_event
            _set_last_refresh(datetime.now(timezone.utc).isoformat())
            _refresh_status = "idle"
        except Exception as e:
            print(f"[SSE] Error: {e}")
            yield f'data: {json.dumps({"step":"error","detail":"An unexpected error occurred"})}\n\n'
            _refresh_status = "error"
        finally:
            _refresh_lock.release()

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/rescan", methods=["POST"])
@login_required
def api_rescan():
    """Re-scan selected anime for TVDB ID mapping. Accepts JSON with 'anilist_ids' list."""
    data = request.get_json(silent=True) or {}
    anilist_ids = data.get("anilist_ids", [])

    if not anilist_ids:
        return jsonify({"status": "error", "message": "No anime IDs provided"})

    anime_list = db.get_anime_by_anilist_ids(anilist_ids)
    found = 0
    failed = 0
    updated = {}

    for anime in anime_list:
        tvdb_id, source, tvdb_title = rescan_tvdb_id(anime)
        if tvdb_id:
            db.set_tvdb_id(anime["anilist_id"], tvdb_id, source)
            # Only fetch title from Sonarr if resolve didn't return one
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


@app.route("/api/ignore", methods=["POST"])
@login_required
def api_ignore():
    """Ignore or unignore anime. Expects {anilist_ids: [...], ignored: true/false}."""
    data = request.get_json(silent=True) or {}
    anilist_ids = data.get("anilist_ids", [])
    ignored = data.get("ignored", True)

    if not anilist_ids:
        return jsonify({"status": "error", "message": "No anime IDs provided"})

    db.set_ignored_bulk(anilist_ids, ignored)
    action = "ignored" if ignored else "restored"
    return jsonify({"status": "ok", "message": f"{len(anilist_ids)} title(s) {action}"})


@app.route("/api/tvdb/set", methods=["POST"])
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
        # Clear the TVDB mapping
        db.set_tvdb_override(anilist_id, None, None)
        return jsonify({"status": "ok", "message": "TVDB mapping cleared"})

    try:
        tvdb_id = int(tvdb_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid TVDB ID"}), 400

    # If the ID hasn't changed, just return — don't override the source
    if anime.get("tvdb_id") == tvdb_id:
        return jsonify({
            "status": "ok",
            "message": f"TVDB ID unchanged ({tvdb_id})",
            "tvdb_id": tvdb_id,
            "tvdb_title": anime.get("tvdb_title"),
        })

    # New ID — validate via Sonarr and mark as manual
    sonarr_info = lookup_series(tvdb_id)
    tvdb_title = sonarr_info["title"] if sonarr_info else None

    db.set_tvdb_override(anilist_id, tvdb_id, tvdb_title)

    return jsonify({
        "status": "ok",
        "message": f"TVDB ID set to {tvdb_id}" + (f" ({tvdb_title})" if tvdb_title else ""),
        "tvdb_id": tvdb_id,
        "tvdb_title": tvdb_title,
    })


@app.route("/api/tvdb/verify", methods=["POST"])
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
        return jsonify({"status": "error", "message": f"TVDB ID {tvdb_id} not found"})

    info["source"] = "sonarr"
    return jsonify({"status": "ok", "series": info})


@app.route("/api/sync", methods=["POST"])
@login_required
def api_sync():
    """Sync selected anime to Sonarr. Accepts optional anilist_ids, season, year."""
    data = request.get_json(silent=True) or {}
    selected_ids = data.get("anilist_ids")

    if selected_ids:
        anime_list = db.get_anime_by_anilist_ids(selected_ids)
    else:
        season, year = _default_season(
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
        print(f"[Sync] Error: {e}")
        return jsonify({"status": "error", "message": "An unexpected error occurred during sync"})

    statuses = {}
    details = []
    title_map = {a["anilist_id"]: _display_name(a) for a in syncable}
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


@app.route("/api/settings", methods=["POST"])
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
        _reschedule()

    return jsonify({"status": "ok", "message": "Settings saved"})


@app.route("/api/settings/test-sonarr", methods=["POST"])
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


@app.route("/api/settings/sonarr-options", methods=["POST"])
@login_required
def api_sonarr_options():
    """Fetch root folders and quality profiles from a Sonarr instance (uses form values, not saved)."""
    data = request.get_json(silent=True) or {}
    url = (data.get("sonarr_url") or config.sonarr_url() or "").rstrip("/")
    api_key = data.get("sonarr_api_key") or config.sonarr_api_key()

    if url and not _validate_url_scheme(url):
        return jsonify({"status": "error", "message": "URL must use http:// or https://"})

    if not url or not api_key or api_key == "your_sonarr_api_key_here":
        return jsonify({"status": "error", "message": "URL and API key required"})

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
            result["root_folders"] = rf_future.result()
        except Exception:
            pass
        try:
            result["quality_profiles"] = qp_future.result()
        except Exception:
            pass

    return jsonify(result)


@app.route("/api/settings/user", methods=["POST"])
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


@app.route("/api/cache/clear", methods=["POST"])
@login_required
def api_clear_cache():
    """Clear all cached data (episode counts, TVDB tokens, Sonarr series list)."""
    db.clear_cache()
    return jsonify({"status": "ok", "message": "Cache cleared"})


@app.route("/api/status")
@login_required
def api_status():
    sonarr_ok, sonarr_msg = test_connection()
    return jsonify({
        "refresh_status": _refresh_status,
        "last_refresh": _get_last_refresh(),
        "sonarr": {"connected": sonarr_ok, "message": sonarr_msg},
    })


# --- Update ---

@app.route("/api/update/check")
@login_required
def api_update_check():
    from services.updater import check_for_update
    force = request.args.get("force") == "1"
    return jsonify(check_for_update(force=force))


@app.route("/api/update/changelog")
@login_required
def api_update_changelog():
    from services.updater import get_changelog
    return jsonify(get_changelog())


@app.route("/api/update/apply", methods=["POST"])
@login_required
def api_update_apply():
    from services.updater import perform_update, schedule_restart
    result = perform_update()
    if result.get("success") and result.get("restart_required"):
        schedule_restart()
    return jsonify(result)


# --- Scheduler ---

def scheduled_refresh():
    """Refresh both current and next season on schedule."""
    refresh_data()  # current season
    nxt_s, nxt_y = next_season()
    refresh_data(nxt_s, nxt_y)  # upcoming season


def _build_trigger():
    from apscheduler.triggers.cron import CronTrigger

    freq = config.refresh_frequency()
    time_str = config.refresh_time()
    day = config.refresh_day()

    try:
        hour, minute = (int(x) for x in time_str.split(":"))
    except (ValueError, AttributeError):
        hour, minute = 6, 0

    if freq == "every_6h":
        return CronTrigger(hour="*/6", minute=minute)
    elif freq == "every_12h":
        return CronTrigger(hour="*/12", minute=minute)
    elif freq == "weekly":
        return CronTrigger(day_of_week=day[:3].lower(), hour=hour, minute=minute)
    else:  # daily (default)
        return CronTrigger(hour=hour, minute=minute)


_scheduler = None


def start_scheduler():
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler

    trigger = _build_trigger()
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(scheduled_refresh, trigger, id="refresh", replace_existing=True)

    from apscheduler.triggers.cron import CronTrigger as _CronTrigger
    from services.updater import check_for_update
    _scheduler.add_job(lambda: check_for_update(force=True),
                       _CronTrigger(day_of_week="sun", hour=2, minute=0),
                       id="update_check", replace_existing=True)

    _scheduler.start()
    print(f"[Scheduler] Refresh scheduled: {config.refresh_frequency()} at {config.refresh_time()}")


def _reschedule():
    if _scheduler:
        _scheduler.reschedule_job("refresh", trigger=_build_trigger())
        print(f"[Scheduler] Rescheduled: {config.refresh_frequency()} at {config.refresh_time()}")


if __name__ == "__main__":
    db.init()
    app.secret_key = config.get_secret_key()

    start_scheduler()

    print(f"[Startup] Ready — refresh data from the web UI or wait for scheduled refresh")

    from waitress import serve
    serve(app, host="0.0.0.0", port=config.FLASK_PORT)
