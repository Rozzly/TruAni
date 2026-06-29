"""Shared application state and refresh logic used by routes and scheduler."""

import json
import logging
import threading
import time as _time
from contextlib import contextmanager
from datetime import datetime, timezone

import db
from services.anilist import current_season, next_season, fetch_seasonal_anime, AniListRateLimited
from services.mapping import resolve_tvdb_id
from services.sonarr import lookup_series, get_existing_series

log = logging.getLogger("truani")

# --- Refresh state ---

class RefreshBusy(Exception):
    """Raised by RefreshManager.run() when a refresh is already in progress."""


class _RefreshManager:
    """Single source of truth for refresh concurrency and status. Both the
    non-streaming (scheduler) and streaming (SSE) entry points enter through
    run(), so the lock and the status are owned in exactly one place."""

    def __init__(self):
        self._lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status = "idle"

    @property
    def status(self):
        with self._status_lock:
            return self._status

    def _set(self, value):
        with self._status_lock:
            self._status = value

    @contextmanager
    def run(self):
        if not self._lock.acquire(blocking=False):
            raise RefreshBusy()
        self._set("running")
        try:
            yield
        except GeneratorExit:
            self._set("idle")          # client disconnected mid-stream
            raise
        except Exception:
            self._set("error")
            raise
        else:
            self._set("idle")
        finally:
            self._lock.release()


refresh_manager = _RefreshManager()


def get_refresh_status():
    return refresh_manager.status


def get_last_refresh():
    return db.get_setting("last_refresh")


def set_last_refresh(iso_str):
    db.save_setting("last_refresh", iso_str)


# --- Season validation ---

_VALID_SEASONS = {"WINTER", "SPRING", "SUMMER", "FALL"}


def default_season(season=None, year=None):
    """Default season/year to current if not provided. Validates season value."""
    if season and season not in _VALID_SEASONS:
        season = None
    if not season or not year:
        s, y = current_season()
        season = season or s
        year = year or y
    return season, year


# --- Helpers ---

def display_name(anime):
    """Get best display title for an anime dict."""
    return anime.get("title_english") or anime.get("tvdb_title") or anime.get("title_romaji") or "?"


def compute_stats(anime_list, ignored_list=None):
    """Compute stats dict for a season's anime."""
    return {
        "total": len(anime_list),
        "mapped": sum(1 for a in anime_list if a["tvdb_id"]),
        "unmapped": sum(1 for a in anime_list if not a["tvdb_id"]),
        "added": sum(1 for a in anime_list if a["sonarr_status"] in ("added", "exists")),
        "ignored": len(ignored_list) if ignored_list is not None else 0,
    }


def build_entry(anime, tvdb_id, tvdb_title, source, sonarr_tvdb_ids):
    """Build an entry dict for upserting anime with TVDB mapping."""
    return {
        **anime,
        "tvdb_id": tvdb_id,
        "tvdb_title": tvdb_title,
        "mapping_source": source,
        "sonarr_status": "exists" if (tvdb_id and tvdb_id in sonarr_tvdb_ids) else "pending",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _anilist_year_horizon(cur_year):
    """Years AniList currently lists anime for, from the current year forward.
    Probed once and cached (~6h; AniList's announced range moves slowly) so the
    nav can offer future years for scanning before anything is scanned, while
    hiding years AniList hasn't announced yet. Falls back to just the current
    year if AniList is unreachable."""
    cached = db.get_cache("anilist_year_horizon")
    if cached is not None:
        return set(cached)
    from services.anilist import year_has_listings
    years = {cur_year}
    try:
        y = cur_year
        while y - cur_year < 5:  # safety cap on look-ahead
            y += 1
            if not year_has_listings(y, interactive=True):
                break
            years.add(y)
        db.set_cache("anilist_year_horizon", sorted(years), ttl_seconds=21600)
    except Exception:
        log.warning("AniList year-horizon probe failed; nav limited to known years")
    return years


def build_season_nav(active_season, active_year):
    """Build the year-tab + season-dropdown navigation model for the UI.

    Year tabs span every year AniList has a filter for (scanned years in the DB,
    the current/next year, and AniList's announced future horizon) — so an
    unscanned-but-announced year like 2027 appears and can be navigated to and
    scanned, while a year AniList hasn't added yet (2028) stays hidden. Each
    shown year offers all four seasons; seasons with no data are navigable but
    empty."""
    from services.anilist import SEASON_ORDER
    cur_season, cur_year = current_season()
    nxt_season, nxt_year = next_season()

    db_seasons = set(db.get_all_seasons())

    year_set = {yr for _, yr in db_seasons}
    year_set.update({cur_year, nxt_year, active_year})
    year_set.update(_anilist_year_horizon(cur_year))

    years = []
    for y in sorted(year_set):
        seasons = [
            {
                "season": s,
                "label": s.capitalize(),
                "active": s == active_season and y == active_year,
                "is_current": s == cur_season and y == cur_year,
                "has_data": (s, y) in db_seasons,
            }
            for s in SEASON_ORDER
        ]
        years.append({
            "year": y,
            "active": y == active_year,
            "is_current": y == cur_year,
            # season label shown inline on the active year tab
            "active_season_label": active_season.capitalize() if y == active_year else "",
            "seasons": seasons,
        })

    return {
        "years": years,
        "active_season": active_season,
        "active_year": active_year,
        "active_is_current": active_season == cur_season and active_year == cur_year,
        # exposed to JS so the set-current button can recompute its disabled state
        "cur_season": cur_season, "cur_year": cur_year,
    }


# --- SSE helpers ---

def sse_format(generator):
    """Wrap a dict-yielding generator into SSE-formatted strings."""
    for d in generator:
        yield f"data: {json.dumps(d)}\n\n"


# --- Refresh logic ---

def refresh_generator(season=None, year=None, fresh=False, interactive=False):
    """Generator that yields event dicts during refresh.

    interactive=True (the SSE/dashboard path) surfaces an AniList rate-limit as
    an error event instead of blocking the request thread for minutes."""

    def event(step, detail, progress=None, **extra):
        d = {"step": step, "detail": detail, **extra}
        if progress is not None:
            d["progress"] = progress
        return d

    if not season or not year:
        season, year = current_season()

    yield event("fetch", f"Fetching {season.capitalize()} {year} from AniList...", progress=2)

    try:
        anime_list = fetch_seasonal_anime(season, year, interactive=interactive)
    except AniListRateLimited as e:
        yield event("error", f"AniList is rate-limiting requests — try again in about {e.retry_after}s")
        return
    except Exception as e:
        yield event("error", f"AniList fetch failed: {e}")
        return

    if fresh:
        deleted = db.delete_season_anime(season, year)
        yield event("fetch", f"Cleared {deleted} existing entries for {season.capitalize()} {year}", progress=4)

    ignored_ids = db.get_ignored_ids()
    ignored_count = 0

    active_list = []
    for anime in anime_list:
        if anime["anilist_id"] in ignored_ids:
            ignored_count += 1
        else:
            active_list.append(anime)

    total = len(active_list)
    yield event("fetch", f"Found {total} titles on AniList" + (f" ({ignored_count} ignored)" if ignored_count else ""), progress=6, count=total)

    sonarr_tvdb_ids = set()
    try:
        sonarr_tvdb_ids = get_existing_series()
        yield event("mapping", f"Found {len(sonarr_tvdb_ids)} series in Sonarr", progress=10)
    except Exception:
        yield event("mapping", "Could not connect to Sonarr — status unchanged", progress=10)

    if ignored_count:
        ignored_anime = [a for a in anime_list if a["anilist_id"] in ignored_ids]
        existing_ignored = {r["anilist_id"]: r for r in db.get_anime_by_anilist_ids([a["anilist_id"] for a in ignored_anime])}
        for anime in ignored_anime:
            existing = existing_ignored.get(anime["anilist_id"])
            ex_tvdb = existing.get("tvdb_id") if existing else None
            entry = build_entry(
                anime, ex_tvdb,
                existing.get("tvdb_title") if existing else None,
                existing.get("mapping_source") if existing else None,
                sonarr_tvdb_ids,
            )
            db.upsert_anime(entry)

    existing_records = {r["anilist_id"]: r for r in db.get_anime_by_anilist_ids([a["anilist_id"] for a in active_list])}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from services.mapping import _sonarr_lookup

    mapped = 0
    unmapped = 0
    processed = 0
    need_sonarr = []

    def _match_pct():
        return 12 + int((processed / max(total, 1)) * 86)

    def _fetch_title(anime, existing, tvdb_id):
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
        idx, anime, existing = item
        tvdb_id, source, tvdb_title = _sonarr_lookup(anime)
        if tvdb_id and not tvdb_title:
            tvdb_title = _fetch_title(anime, existing, tvdb_id)
        return idx, anime, existing, tvdb_id, source, tvdb_title

    # Pass 1: Fast resolve (DB cache only)
    for anime in active_list:
        existing = existing_records.get(anime["anilist_id"])
        tvdb_id, source, tvdb_title, existing = resolve_tvdb_id(anime, existing=existing, skip_sonarr=True)

        if tvdb_id:
            title = display_name(anime)
            db.upsert_anime(build_entry(anime, tvdb_id, tvdb_title, source, sonarr_tvdb_ids))
            mapped += 1
            processed += 1
            yield event("match", title, progress=_match_pct(), matched=True, tvdb_id=tvdb_id, index=processed, total=total)
        else:
            need_sonarr.append((0, anime, existing))

    # Pass 2: Parallel Sonarr lookups
    if need_sonarr:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_full_resolve, item): item for item in need_sonarr}
            for future in as_completed(futures):
                try:
                    idx, anime, existing, tvdb_id, source, tvdb_title = future.result()
                except Exception as exc:
                    item = futures[future]
                    _, anime, existing = item
                    log.error("Resolve failed for anilist:%s: %s", anime.get("anilist_id"), exc)
                    tvdb_id, source, tvdb_title = None, None, None
                title = display_name(anime)
                processed += 1

                db.upsert_anime(build_entry(anime, tvdb_id, tvdb_title, source, sonarr_tvdb_ids))

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
    result = None
    try:
        with refresh_manager.run():
            for event_dict in refresh_generator(season, year):
                if event_dict.get("step") in ("done", "error"):
                    result = event_dict
            set_last_refresh(datetime.now(timezone.utc).isoformat())
    except RefreshBusy:
        return {"status": "busy", "message": "Refresh already in progress"}
    except Exception as e:
        log.error("Refresh error: %s", e)
        return {"status": "error", "message": "An unexpected error occurred during refresh"}

    if result and result.get("step") == "done":
        return {"status": "ok", "message": result["detail"],
                "total": result.get("total", 0), "mapped": result.get("mapped", 0), "unmapped": result.get("unmapped", 0)}
    elif result and result.get("step") == "error":
        return {"status": "error", "message": result["detail"]}
    return {"status": "ok", "message": "Refresh complete"}
