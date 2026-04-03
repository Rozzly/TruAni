"""
Update system: check GitHub for new versions, pull updates, restart.
Works in both Docker and LXC without root.
"""

import os
import sys
import subprocess
import threading

import logging
import requests
import db
import config

log = logging.getLogger("truani")

GITHUB_REPO = "Rozzly/TruAni"
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_session = requests.Session()
_session.headers["Accept"] = "application/vnd.github.v3+json"


def _compare_versions(current, latest):
    """Return True if latest > current using semver comparison."""
    def parse(v):
        v = v.lstrip("v")
        parts = v.split(".")[:3]
        return tuple(int(p) for p in parts)
    try:
        return parse(latest) > parse(current)
    except (ValueError, TypeError):
        return False


def check_for_update(force=False):
    """Check GitHub for a newer version. Cached for 1 week."""
    if not force:
        cached = db.get_cache("update_check")
        if cached is not None:
            return cached

    current = config.APP_VERSION
    result = {
        "update_available": False,
        "current_version": current,
        "latest_version": current,
        "release_url": "",
        "changelog": "",
    }

    try:
        # Try releases first
        resp = _session.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get("tag_name", "").lstrip("v")
            result["latest_version"] = latest
            result["release_url"] = data.get("html_url", "")
            result["changelog"] = data.get("body", "")
            result["update_available"] = _compare_versions(current, latest)
        elif resp.status_code == 404:
            # No releases — try tags
            resp = _session.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=1",
                timeout=10,
            )
            if resp.status_code == 200:
                tags = resp.json()
                if tags:
                    latest = tags[0].get("name", "").lstrip("v")
                    result["latest_version"] = latest
                    result["update_available"] = _compare_versions(current, latest)
    except Exception as e:
        log.warning("Update check failed: %s", e)

    db.set_cache("update_check", result, ttl_seconds=604800)  # 1 week
    return result


def get_changelog():
    """Get changelog from GitHub releases (past 4 versions). Cached for 1 week."""
    cached = db.get_cache("update_changelog")
    if cached is not None:
        return cached

    entries = []
    try:
        resp = _session.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=4",
            timeout=10,
        )
        if resp.status_code == 200:
            for release in resp.json():
                entries.append({
                    "version": release.get("tag_name", "").lstrip("v"),
                    "date": (release.get("published_at") or "")[:10],
                    "changes": release.get("body", ""),
                })
    except Exception as e:
        log.warning("Changelog fetch failed: %s", e)

    # Fallback: if no releases, use git log
    if not entries:
        try:
            log = subprocess.run(
                ["git", "log", "--oneline", "-10", "--no-decorate"],
                capture_output=True, text=True, cwd=_APP_DIR, timeout=10,
            )
            if log.returncode == 0 and log.stdout.strip():
                entries.append({
                    "version": config.APP_VERSION,
                    "date": "",
                    "changes": log.stdout.strip(),
                })
        except Exception:
            pass

    db.set_cache("update_changelog", entries, ttl_seconds=604800)  # 1 week
    return entries


def perform_update():
    """Pull latest code from GitHub and prepare for restart."""
    try:
        # Read current requirements for comparison
        req_path = os.path.join(_APP_DIR, "requirements.txt")
        old_reqs = ""
        if os.path.exists(req_path):
            with open(req_path) as f:
                old_reqs = f.read()

        # Fetch and reset to latest
        result = subprocess.run(
            ["git", "fetch", "origin", "main"],
            capture_output=True, text=True, cwd=_APP_DIR, timeout=30,
        )
        if result.returncode != 0:
            return {"success": False, "message": f"Git fetch failed: {result.stderr.strip()}"}

        result = subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            capture_output=True, text=True, cwd=_APP_DIR, timeout=15,
        )
        if result.returncode != 0:
            return {"success": False, "message": f"Git reset failed: {result.stderr.strip()}"}

        # Check if requirements changed
        new_reqs = ""
        if os.path.exists(req_path):
            with open(req_path) as f:
                new_reqs = f.read()

        deps_changed = old_reqs != new_reqs
        if deps_changed:
            pip_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
                capture_output=True, text=True, cwd=_APP_DIR, timeout=120,
            )
            if pip_result.returncode != 0:
                return {"success": False, "message": f"Dependency install failed: {pip_result.stderr.strip()}"}

        # Read new version
        version_path = os.path.join(_APP_DIR, "VERSION")
        new_version = config.APP_VERSION
        if os.path.exists(version_path):
            with open(version_path) as f:
                new_version = f.read().strip()

        # Clear update cache so next check picks up new state
        db.set_cache("update_check", None, ttl_seconds=0)
        db.set_cache("update_changelog", None, ttl_seconds=0)

        return {
            "success": True,
            "new_version": new_version,
            "deps_changed": deps_changed,
            "message": f"Updated to v{new_version}" + (" (dependencies updated)" if deps_changed else ""),
            "restart_required": True,
        }

    except Exception as e:
        log.error("Update failed: %s", e)
        return {"success": False, "message": f"Update failed: {e}"}


def schedule_restart():
    """Schedule a process restart after a short delay (lets HTTP response finish)."""
    def _restart():
        log.info("Restarting application...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Timer(2.0, _restart).start()
