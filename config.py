"""
Configuration layer. Reads from DB settings table, falls back to env vars.
DB_PATH and FLASK_PORT are always from env (needed before DB is available).
SECRET_KEY is generated on first run and stored in DB.
"""

import os
import secrets
from dotenv import load_dotenv

load_dotenv()

# App version (read from VERSION file)
_version_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
try:
    with open(_version_file) as _f:
        APP_VERSION = _f.read().strip()
except FileNotFoundError:
    APP_VERSION = "0.0.0"

# These are always from env (bootstrap values)
DB_PATH = os.getenv("DB_PATH", "data/truani.db")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5656"))


def _bool(val):
    return str(val).lower() in ("true", "1", "yes")


_settings_cache = {}
_CACHE_TTL = 30


def _get(key, env_key=None, default=""):
    """Get setting from DB, falling back to env var. Cached for 30s."""
    import time
    now = time.monotonic()
    cached = _settings_cache.get(key)
    if cached and now < cached[1]:
        return cached[0]
    import db
    val = db.get_setting(key)
    if val:
        _settings_cache[key] = (val, now + _CACHE_TTL)
        return val
    result = os.getenv(env_key, default) if env_key else default
    _settings_cache[key] = (result, now + _CACHE_TTL)
    return result


def clear_settings_cache():
    """Clear the in-memory settings cache. Call after saving settings."""
    _settings_cache.clear()


def get_secret_key():
    import db
    key = db.get_setting("secret_key")
    if not key:
        key = secrets.token_hex(32)
        db.save_setting("secret_key", key)
    return key


# Accessors — called at request time so they pick up DB changes
def sonarr_url():
    return _get("sonarr_url", "SONARR_URL", "http://localhost:8989").rstrip("/")

def sonarr_api_key():
    return _get("sonarr_api_key", "SONARR_API_KEY", "")

def has_sonarr():
    """True if a real Sonarr API key is configured."""
    key = sonarr_api_key()
    return bool(key) and key != "your_sonarr_api_key_here"

def sonarr_root_folder():
    return _get("sonarr_root_folder", "SONARR_ROOT_FOLDER", "/tv/anime")

def sonarr_quality_profile():
    return _get("sonarr_quality_profile", "SONARR_QUALITY_PROFILE", "HD-1080p")

def sonarr_series_type():
    return _get("sonarr_series_type", "SONARR_SERIES_TYPE", "anime")

def sonarr_monitor():
    return _get("sonarr_monitor", "SONARR_MONITOR", "all")

def sonarr_season_folder():
    return _bool(_get("sonarr_season_folder", "SONARR_SEASON_FOLDER", "true"))

def sonarr_search_on_add():
    return _bool(_get("sonarr_search_on_add", "SONARR_SEARCH_ON_ADD", "false"))

def sonarr_tags():
    raw = _get("sonarr_tags", "SONARR_TAGS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]

def refresh_frequency():
    return _get("refresh_frequency", None, "daily")

def refresh_time():
    return _get("refresh_time", None, "06:00")

def refresh_day():
    return _get("refresh_day", None, "monday")
