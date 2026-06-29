"""
Update system: check GitHub for new versions, download a verified release
package, validate it, swap it in atomically, and restart in place.

Design notes
------------
The previous implementation did ``git reset --hard origin/main`` on the live
working tree and then ``os.execv``. That had no integrity check, no validation,
and no rollback. This version mirrors how the *arr apps update, adapted to a
pure-Python app that must keep working when launched as ``python app.py`` inside
the existing container (where the app is *not* PID 1, so a detached helper that
required the app to exit would be killed when the container stops):

  download -> verify sha256 -> extract to staging -> compile-check (no deps
  needed) -> back up current files -> swap into the install dir -> pip install
  if requirements changed -> import smoke-test -> os.execv (PID preserved).

Crucially the running process stays alive as its own supervisor through the
whole prepare/validate phase and only re-execs at the very end, after success.
Any failure before that point rolls the install dir back and leaves the running
(old) code untouched. ``os.execv`` is used (not a child process) because it
preserves the PID, so the container survives the restart.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from datetime import datetime, timezone

import logging
import requests
import db
import config

log = logging.getLogger("truani")

GITHUB_REPO = "Rozzly/TruAni"
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_session = requests.Session()
_session.headers["Accept"] = "application/vnd.github.v3+json"

# Top-level entries that must never be backed up, overwritten, or removed by an
# update. These are runtime state / local config that does not ship in a release
# package, so a release tarball won't contain them — this is belt-and-suspenders.
_EXCLUDE_TOP = {"data", ".git", ".venv", "venv", "__pycache__", ".env"}


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
            git_log = subprocess.run(
                ["git", "log", "--oneline", "-10", "--no-decorate"],
                capture_output=True, text=True, cwd=_APP_DIR, timeout=10,
            )
            if git_log.returncode == 0 and git_log.stdout.strip():
                entries.append({
                    "version": config.APP_VERSION,
                    "date": "",
                    "changes": git_log.stdout.strip(),
                })
        except Exception:
            pass

    db.set_cache("update_changelog", entries, ttl_seconds=604800)  # 1 week
    return entries


# --- Update application ---------------------------------------------------

def _data_dir():
    return os.path.dirname(os.path.abspath(config.DB_PATH)) or _APP_DIR


def _prepare_workspace():
    """Create a clean working area under the (persistent) data dir.
    Returns the workspace root. Cleared on every run so stale files never leak
    into a later update."""
    root = os.path.join(_data_dir(), ".update")
    shutil.rmtree(root, ignore_errors=True)
    for sub in ("download", "extract", "backup"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


def _resolve_package():
    """Resolve the latest release into a downloadable package.

    Prefers an uploaded ``*.tar.gz`` release asset (byte-stable, so its checksum
    is reliable) and a ``checksums.txt`` asset. Falls back to GitHub's
    auto-generated source tarball when no asset is published — that archive is
    not guaranteed byte-stable, so it is used without checksum verification."""
    resp = _session.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=10
    )
    resp.raise_for_status()
    rel = resp.json()
    tag = rel.get("tag_name", "") or ""
    version = tag.lstrip("v")
    assets = {a.get("name", ""): a.get("browser_download_url", "")
              for a in (rel.get("assets") or [])}

    tar_name = next((n for n in assets if n.endswith(".tar.gz")), None)
    if tar_name:
        tar_url = assets[tar_name]
    else:
        repo_name = GITHUB_REPO.split("/")[-1]
        tar_name = f"{repo_name}-{version}.tar.gz"
        tar_url = (rel.get("tarball_url")
                   or f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.tar.gz")

    return {
        "version": version,
        "tag": tag,
        "tar_name": tar_name,
        "tar_url": tar_url,
        "checksums_url": assets.get("checksums.txt"),
    }


def _download(url, dest_path):
    """Stream a URL to dest_path, returning the sha256 hex digest of the bytes."""
    h = hashlib.sha256()
    with _session.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    h.update(chunk)
    return h.hexdigest()


def _expected_sha(checksums_text, filename):
    """Parse sha256sum-format text for the digest matching filename."""
    for line in checksums_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and os.path.basename(parts[-1]) == filename:
            return parts[0].lower()
    return None


def _safe_extract(tar_path, dest):
    """Extract a .tar.gz, rejecting path traversal and links (zip-slip guard)."""
    dest_abs = os.path.abspath(dest)
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = os.path.abspath(os.path.join(dest, member.name))
            if target != dest_abs and not target.startswith(dest_abs + os.sep):
                raise ValueError(f"Unsafe path in archive: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"Refusing link member in archive: {member.name}")
        tar.extractall(dest)


def _find_app_root(extract_dir):
    """Locate the directory containing app.py within an extracted package.
    GitHub source archives wrap everything in a single top-level folder."""
    if os.path.exists(os.path.join(extract_dir, "app.py")):
        return extract_dir
    for name in sorted(os.listdir(extract_dir)):
        p = os.path.join(extract_dir, name)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "app.py")):
            return p
    raise ValueError("Could not locate app.py in update package")


def _iter_rel_files(root):
    """Yield paths (relative to root) of every file, skipping excluded trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        top = "" if rel == "." else rel.split(os.sep)[0]
        if top in _EXCLUDE_TOP:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_TOP]
        for f in filenames:
            yield os.path.relpath(os.path.join(dirpath, f), root)


def _stage_into_install(staging, install, backup):
    """Back up files that will be overwritten, then copy staging over install.
    Returns the list of newly-added relative paths (for rollback)."""
    added = []
    for rel in _iter_rel_files(staging):
        dst = os.path.join(install, rel)
        if os.path.exists(dst):
            b = os.path.join(backup, rel)
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(dst, b)
        else:
            added.append(rel)

    for rel in _iter_rel_files(staging):
        src = os.path.join(staging, rel)
        dst = os.path.join(install, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return added


def _rollback(install, backup, added):
    """Restore overwritten files from backup and remove newly-added files."""
    for rel in _iter_rel_files(backup):
        dst = os.path.join(install, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(backup, rel), dst)
    for rel in added:
        try:
            os.remove(os.path.join(install, rel))
        except OSError:
            pass


def _compile_check(root):
    """Byte-compile the tree to catch syntax errors / truncated files.
    Dependency-independent, so it is safe to run before swapping (current deps)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", root],
            capture_output=True, text=True, timeout=120,
        )
        return r.returncode == 0, (r.stdout + r.stderr)
    except Exception as e:
        return False, str(e)


def _import_check(install):
    """Import the app in a subprocess to confirm the swapped tree loads cleanly
    against the (post-pip) installed dependencies. No DB or network side effects:
    importing app.py only constructs the Flask app and registers blueprints."""
    env = dict(os.environ)
    env["PYTHONPATH"] = install + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=install, env=env, capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0, (r.stdout + r.stderr)
    except Exception as e:
        return False, str(e)


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _is_containerized():
    """True when running inside a Docker/OCI container.

    In-place self-update is meaningless here: the code is baked into the image,
    and any file swap lands only in the container's writable layer — lost the
    moment the container is recreated (the usual way images get updated). The
    Dockerfile sets TRUANI_DEPLOYMENT=docker; ``/.dockerenv`` is a fallback for
    images built without it."""
    if os.getenv("TRUANI_DEPLOYMENT", "").lower() == "docker":
        return True
    return os.path.exists("/.dockerenv")


def perform_update():
    """Download, verify, validate, swap in, and prepare to restart.
    Returns a result dict; on success ``restart_required`` is True and the caller
    should invoke ``schedule_restart()``."""
    if _is_containerized():
        return {
            "success": False,
            "restart_required": False,
            "message": ("This instance runs in Docker — in-app update is disabled "
                        "for containers. Update by rebuilding the image: "
                        "`git pull && docker compose up -d --build` (your data is "
                        "preserved in the ./data volume). See the README."),
        }
    try:
        return _perform_update()
    except Exception as e:
        log.error("Update failed: %s", e)
        return {"success": False, "message": f"Update failed: {e}"}


def _perform_update():
    pkg = _resolve_package()
    if not pkg["version"]:
        return {"success": False, "message": "No release found to update to"}
    if not _compare_versions(config.APP_VERSION, pkg["version"]):
        return {
            "success": True,
            "new_version": config.APP_VERSION,
            "restart_required": False,
            "message": f"Already up to date (v{config.APP_VERSION})",
        }

    work = _prepare_workspace()
    download_dir = os.path.join(work, "download")
    extract_dir = os.path.join(work, "extract")
    backup_dir = os.path.join(work, "backup")
    tar_path = os.path.join(download_dir, pkg["tar_name"])

    # 1. Download + integrity check
    try:
        actual_sha = _download(pkg["tar_url"], tar_path)
    except Exception as e:
        return {"success": False, "message": f"Download failed: {e}"}

    if pkg["checksums_url"]:
        try:
            ck = _session.get(pkg["checksums_url"], timeout=10)
            ck.raise_for_status()
            expected = _expected_sha(ck.text, pkg["tar_name"])
        except Exception as e:
            return {"success": False, "message": f"Could not fetch checksums: {e}"}
        if not expected:
            return {"success": False,
                    "message": "Checksum for update package not found in checksums.txt"}
        if expected != actual_sha:
            log.error("Update checksum mismatch: expected %s, got %s", expected, actual_sha)
            return {"success": False,
                    "message": "Update package failed checksum verification — aborted"}
    else:
        log.warning("No checksums.txt published for this release; "
                    "proceeding without integrity verification")

    # 2. Extract + pre-swap validation (current deps; nothing on disk touched yet)
    try:
        _safe_extract(tar_path, extract_dir)
        app_root = _find_app_root(extract_dir)
    except Exception as e:
        return {"success": False, "message": f"Failed to extract update package: {e}"}

    ok, out = _compile_check(app_root)
    if not ok:
        log.error("Pre-swap compile check failed: %s", out)
        return {"success": False,
                "message": "Update package failed validation — aborted (no changes made)"}

    install = _APP_DIR
    old_reqs = _read(os.path.join(install, "requirements.txt"))

    # 3. Back up + swap into the live install dir
    try:
        added = _stage_into_install(app_root, install, backup_dir)
    except Exception as e:
        log.error("Failed while installing update, rolling back: %s", e)
        _rollback(install, backup_dir, [])
        return {"success": False,
                "message": f"Update failed during install and was rolled back: {e}"}

    # 4. Install dependencies if they changed; roll back on failure
    new_reqs = _read(os.path.join(install, "requirements.txt"))
    deps_changed = old_reqs != new_reqs
    if deps_changed:
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
            cwd=install, capture_output=True, text=True, timeout=180,
        )
        if pip.returncode != 0:
            log.error("Dependency install failed, rolling back: %s", pip.stderr.strip())
            _rollback(install, backup_dir, added)
            return {"success": False,
                    "message": "Dependency install failed — rolled back to previous version"}

    # 5. Post-swap smoke test against the installed deps; roll back on failure
    ok, out = _import_check(install)
    if not ok:
        log.error("Post-update import check failed, rolling back: %s", out)
        _rollback(install, backup_dir, added)
        return {"success": False,
                "message": "Updated code failed to load — rolled back to previous version"}

    # 6. Success — record state and signal the caller to restart
    new_version = _read(os.path.join(install, "VERSION")).strip() or pkg["version"]
    db.set_cache("update_check", None, ttl_seconds=0)
    db.set_cache("update_changelog", None, ttl_seconds=0)
    try:
        db.save_setting("last_update", json.dumps(
            {"version": new_version, "at": datetime.now(timezone.utc).isoformat()}))
    except Exception:
        pass

    return {
        "success": True,
        "new_version": new_version,
        "deps_changed": deps_changed,
        "restart_required": True,
        "message": f"Updated to v{new_version}" + (" (dependencies updated)" if deps_changed else ""),
    }


def schedule_restart():
    """Schedule an in-place re-exec after a short delay (lets the HTTP response
    finish). ``os.execv`` preserves the PID, so the container/supervisor sees the
    same process and stays up across the restart."""
    def _restart():
        log.info("Restarting application...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Timer(2.0, _restart).start()
