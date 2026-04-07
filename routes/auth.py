"""Authentication routes and helpers."""

import functools
import logging
import threading
import time as _time

from flask import Blueprint, render_template, jsonify, request, redirect, url_for, session, flash

import db

log = logging.getLogger("truani")

auth_bp = Blueprint("auth", __name__)

# --- Rate limiting ---

_login_attempts = {}  # IP -> (fail_count, first_failure_time)
_login_lock = threading.Lock()
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_LOCKOUT_SECONDS = 900


def _check_rate_limit(ip):
    now = _time.monotonic()
    with _login_lock:
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
    with _login_lock:
        entry = _login_attempts.get(ip)
        if entry and now - entry[1] <= _LOGIN_LOCKOUT_SECONDS:
            _login_attempts[ip] = (entry[0] + 1, entry[1])
        else:
            _login_attempts[ip] = (1, now)


def _clear_failures(ip):
    with _login_lock:
        _login_attempts.pop(ip, None)


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "Not authenticated"}), 401
            return redirect(url_for("auth.login"))
        if not db.get_setting("setup_complete") and request.endpoint not in ("auth.setup", "api.api_settings", "api.api_test_sonarr", "api.api_sonarr_options"):
            return redirect(url_for("auth.setup"))
        return f(*args, **kwargs)
    return decorated


# --- Routes ---

@auth_bp.route("/login", methods=["GET", "POST"])
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
                return redirect(url_for("auth.setup"))
            return redirect(url_for("pages.index"))
        _record_failure(ip)
        flash("Invalid username or password", "error")
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-login setup: force credential change."""
    if db.get_setting("setup_complete"):
        return redirect(url_for("pages.index"))
    if not session.get("user"):
        return redirect(url_for("auth.login"))

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
        return redirect(url_for("auth.setup"))

    step = 2 if db.get_setting("credentials_set") else 1
    return render_template("setup.html", settings=db.get_all_settings(), step=step)
