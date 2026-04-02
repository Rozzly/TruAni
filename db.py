import os
import sqlite3
import json
import threading
from datetime import datetime, timezone, timedelta

import bcrypt

_db_path = os.getenv("DB_PATH", "data/truani.db")
_local = threading.local()
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode()


def _connect():
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        return conn
    os.makedirs(os.path.dirname(_db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _local.conn = conn
    return conn


def close_connection():
    """Close the thread-local DB connection. Call from Flask teardown or thread cleanup."""
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def init():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS anime (
                anilist_id    INTEGER PRIMARY KEY,
                title_english TEXT,
                title_romaji  TEXT,
                title_native  TEXT,
                synonyms      TEXT,
                format        TEXT,
                season        TEXT,
                season_year   INTEGER,
                episodes      INTEGER,
                description   TEXT,
                genres        TEXT,
                score         INTEGER,
                anilist_url   TEXT,
                cover_url     TEXT,
                cover_url_lg  TEXT,
                tvdb_id       INTEGER,
                tvdb_title    TEXT,
                tmdb_id       INTEGER,
                mapping_source TEXT,
                sonarr_status TEXT DEFAULT 'pending',
                season_number     INTEGER DEFAULT 0,
                ignored       INTEGER DEFAULT 0,
                updated_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS cache (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)

    # Auto-migrate: add columns that may be missing from older schemas
    _migrate()

    print(f"[DB] Initialized at {_db_path}")

    # Create default admin user if no users exist
    if not get_users():
        create_user("truani", "truani")


def _migrate():
    """Add any missing columns to existing tables. Safe to run repeatedly."""
    anime_columns = {
        "title_native": "TEXT",
        "synonyms": "TEXT",
        "description": "TEXT",
        "genres": "TEXT",
        "score": "INTEGER",
        "anilist_url": "TEXT",
        "cover_url_lg": "TEXT",
        "tvdb_title": "TEXT",
        "ignored": "INTEGER DEFAULT 0",
        "season_number": "INTEGER DEFAULT 0",
        "episodes_source": "TEXT",
    }
    with _connect() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(anime)").fetchall()}
        for col, col_type in anime_columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE anime ADD COLUMN {col} {col_type}")
                print(f"[DB] Migrated: added anime.{col}")


# --- Users ---

def get_users():
    with _connect() as conn:
        rows = conn.execute("SELECT id, username FROM users").fetchall()
    return [dict(r) for r in rows]


def get_user_by_username(username):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def create_user(username, password):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with _connect() as conn:
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed))


def verify_password(username, password):
    user = get_user_by_username(username)
    if not user:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH.encode())
        return False
    return bcrypt.checkpw(password.encode(), user["password"].encode())


def update_password(username, new_password):
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    with _connect() as conn:
        conn.execute("UPDATE users SET password = ? WHERE username = ?", (hashed, username))


def update_username(old_username, new_username):
    with _connect() as conn:
        conn.execute("UPDATE users SET username = ? WHERE username = ?", (new_username, old_username))


def validate_password(password):
    """Return error message if password is invalid, or None if ok."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    return None


# --- Settings ---

_DEFAULTS = {
    "sonarr_url": "http://localhost:8989",
    "sonarr_api_key": "",
    "sonarr_root_folder": "/tv/anime",
    "sonarr_quality_profile": "HD-1080p",
    "sonarr_series_type": "anime",
    "sonarr_monitor": "all",
    "sonarr_season_folder": "true",
    "sonarr_search_on_add": "false",
    "sonarr_tags": "",
    "tvdb_api_key": "",
    "refresh_frequency": "daily",
    "refresh_time": "06:00",
    "refresh_day": "monday",
}


def get_setting(key):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return _DEFAULTS.get(key, "")


def get_all_settings():
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = dict(_DEFAULTS)
    for r in rows:
        result[r["key"]] = r["value"]
    return result


def save_setting(key, value):
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )


def save_settings(settings_dict):
    with _connect() as conn:
        for key, value in settings_dict.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value))
            )


# --- Anime ---

def upsert_anime(anime):
    # Work on a copy to avoid mutating the caller's dict
    anime = dict(anime)
    # Serialize synonyms list to JSON string for storage
    if isinstance(anime.get("synonyms"), list):
        anime["synonyms"] = json.dumps(anime["synonyms"])
    # Default deprecated tmdb_id field for DB compatibility
    if "tmdb_id" not in anime:
        anime["tmdb_id"] = None
    with _connect() as conn:
        conn.execute("""
            INSERT INTO anime
                (anilist_id, title_english, title_romaji, title_native, synonyms,
                 format, season, season_year, episodes, description, genres, score,
                 anilist_url, cover_url, cover_url_lg,
                 tvdb_id, tvdb_title, tmdb_id, mapping_source, sonarr_status, season_number, updated_at)
            VALUES
                (:anilist_id, :title_english, :title_romaji, :title_native, :synonyms,
                 :format, :season, :season_year, :episodes, :description, :genres, :score,
                 :anilist_url, :cover_url, :cover_url_lg,
                 :tvdb_id, :tvdb_title, :tmdb_id, :mapping_source, :sonarr_status, :season_number, :updated_at)
            ON CONFLICT(anilist_id) DO UPDATE SET
                title_english  = excluded.title_english,
                title_romaji   = excluded.title_romaji,
                title_native   = excluded.title_native,
                synonyms       = excluded.synonyms,
                format         = excluded.format,
                season         = excluded.season,
                season_year    = excluded.season_year,
                episodes       = CASE
                    WHEN excluded.episodes IS NOT NULL AND excluded.episodes > 0 THEN excluded.episodes
                    ELSE COALESCE(anime.episodes, excluded.episodes)
                END,
                episodes_source = CASE
                    WHEN excluded.episodes IS NOT NULL AND excluded.episodes > 0 THEN 'anilist'
                    ELSE anime.episodes_source
                END,
                description    = excluded.description,
                genres         = excluded.genres,
                score          = excluded.score,
                anilist_url    = excluded.anilist_url,
                cover_url      = excluded.cover_url,
                cover_url_lg   = excluded.cover_url_lg,
                tvdb_id        = COALESCE(excluded.tvdb_id, anime.tvdb_id),
                tvdb_title     = CASE
                    WHEN excluded.tvdb_id IS NOT NULL THEN excluded.tvdb_title
                    ELSE anime.tvdb_title
                END,
                tmdb_id        = COALESCE(excluded.tmdb_id, anime.tmdb_id),
                mapping_source = COALESCE(excluded.mapping_source, anime.mapping_source),
                sonarr_status  = excluded.sonarr_status,
                season_number      = COALESCE(excluded.season_number, anime.season_number),
                updated_at     = excluded.updated_at
        """, anime)


def get_all_seasons():
    """Return list of (season, year) tuples for all seasons with data, ordered newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT season, season_year FROM anime ORDER BY season_year DESC, "
            "CASE season WHEN 'FALL' THEN 0 WHEN 'SUMMER' THEN 1 WHEN 'SPRING' THEN 2 WHEN 'WINTER' THEN 3 END"
        ).fetchall()
    return [(r["season"], r["season_year"]) for r in rows]


def _deserialize_anime(row):
    """Convert a DB row to a dict, deserializing synonyms JSON."""
    d = dict(row)
    if d.get("synonyms"):
        try:
            d["synonyms"] = json.loads(d["synonyms"])
        except (json.JSONDecodeError, TypeError):
            d["synonyms"] = []
    else:
        d["synonyms"] = []
    return d


def delete_season_anime(season, year):
    """Delete all non-ignored anime for a season. Returns count deleted."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM anime WHERE season = ? AND season_year = ? AND ignored = 0",
            (season, year)
        )
        return cursor.rowcount


def get_season_anime(season, year, include_ignored=False):
    with _connect() as conn:
        if include_ignored:
            rows = conn.execute(
                "SELECT * FROM anime WHERE season = ? AND season_year = ? ORDER BY ignored, title_english, title_romaji",
                (season, year)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM anime WHERE season = ? AND season_year = ? AND ignored = 0 ORDER BY title_english, title_romaji",
                (season, year)
            ).fetchall()
    return [_deserialize_anime(r) for r in rows]


def get_ignored_anime(season, year):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM anime WHERE season = ? AND season_year = ? AND ignored = 1 ORDER BY title_english, title_romaji",
            (season, year)
        ).fetchall()
    return [_deserialize_anime(r) for r in rows]


def get_ignored_ids():
    """Return set of all ignored anilist_ids."""
    with _connect() as conn:
        rows = conn.execute("SELECT anilist_id FROM anime WHERE ignored = 1").fetchall()
    return {r["anilist_id"] for r in rows}


def set_ignored(anilist_id, ignored=True):
    with _connect() as conn:
        conn.execute(
            "UPDATE anime SET ignored = ?, updated_at = ? WHERE anilist_id = ?",
            (1 if ignored else 0, _now(), anilist_id)
        )


def set_ignored_bulk(anilist_ids, ignored=True):
    if not anilist_ids:
        return
    with _connect() as conn:
        placeholders = ",".join("?" for _ in anilist_ids)
        conn.execute(
            f"UPDATE anime SET ignored = ?, updated_at = ? WHERE anilist_id IN ({placeholders})",
            [1 if ignored else 0, _now()] + list(anilist_ids)
        )


def get_anime_by_anilist_id(anilist_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM anime WHERE anilist_id = ?", (anilist_id,)
        ).fetchone()
    return _deserialize_anime(row) if row else None


def get_anime_by_anilist_ids(anilist_ids):
    if not anilist_ids:
        return []
    placeholders = ",".join("?" for _ in anilist_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM anime WHERE anilist_id IN ({placeholders})",
            anilist_ids
        ).fetchall()
    return [_deserialize_anime(r) for r in rows]


def update_sonarr_status(anilist_id, status):
    with _connect() as conn:
        conn.execute(
            "UPDATE anime SET sonarr_status = ?, updated_at = ? WHERE anilist_id = ?",
            (status, _now(), anilist_id)
        )


def set_tvdb_id(anilist_id, tvdb_id, source):
    with _connect() as conn:
        conn.execute(
            "UPDATE anime SET tvdb_id = ?, mapping_source = ?, updated_at = ? WHERE anilist_id = ?",
            (tvdb_id, source, _now(), anilist_id)
        )


def set_tvdb_override(anilist_id, tvdb_id, tvdb_title=None):
    """Set a manual TVDB ID override. Resets sonarr_status if ID changed."""
    with _connect() as conn:
        conn.execute(
            """UPDATE anime SET
                tvdb_id = ?, tvdb_title = ?, mapping_source = 'manual',
                sonarr_status = CASE
                    WHEN tvdb_id != ? OR tvdb_id IS NULL THEN 'pending'
                    ELSE sonarr_status
                END,
                updated_at = ?
            WHERE anilist_id = ?""",
            (tvdb_id, tvdb_title, tvdb_id, _now(), anilist_id)
        )


def set_tvdb_title(anilist_id, tvdb_title):
    with _connect() as conn:
        conn.execute(
            "UPDATE anime SET tvdb_title = ?, updated_at = ? WHERE anilist_id = ?",
            (tvdb_title, _now(), anilist_id)
        )


def update_episode_count(anilist_id, episodes):
    """Update episode count from TVDB scrape. Only overwrites if current value is null/0
    or was previously set by a scrape (not by AniList).
    episodes_source=NULL is treated as scrape-sourced (legacy data)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE anime SET episodes = ?, episodes_source = 'tvdb', updated_at = ? "
            "WHERE anilist_id = ? AND (episodes IS NULL OR episodes = 0 "
            "OR episodes_source = 'tvdb' OR episodes_source IS NULL)",
            (episodes, _now(), anilist_id)
        )


# --- Cache ---

def get_cache(key):
    with _connect() as conn:
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    if row["expires_at"]:
        expires = datetime.fromisoformat(row["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            with _connect() as conn:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            return None
    return json.loads(row["value"])


def set_cache(key, value, ttl_seconds=86400):
    expires = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=ttl_seconds)).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), expires)
        )


def clear_cache():
    """Delete all cached data."""
    with _connect() as conn:
        conn.execute("DELETE FROM cache")


def _now():
    return datetime.now(timezone.utc).isoformat()
