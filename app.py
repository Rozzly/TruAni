"""TruAni — Seasonal anime manager. Flask application entry point."""

import hmac
import logging
import secrets
from datetime import timedelta

from flask import Flask, jsonify, request, session, redirect, url_for, flash

import config
import db
from services.titleutil import display_title

log = logging.getLogger("truani")
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)

app = Flask(__name__)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.permanent_session_lifetime = timedelta(days=7)

app.jinja_env.filters['display_title'] = display_title


def _csrf_token():
    """Per-session CSRF token (synchronizer-token pattern), created lazily and
    stored in the signed session cookie."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def _inject_globals():
    return {"app_version": config.APP_VERSION, "csrf_token": _csrf_token()}


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


@app.before_request
def _csrf_protect():
    """Synchronizer-token CSRF check on state-changing requests. The token is
    delivered to the page (meta tag / hidden form field) and echoed back via the
    X-CSRFToken header (fetch) or the csrf_token field (HTML forms). Replaces the
    previous Origin/Referer heuristic, which allowed requests that sent neither."""
    if request.method in _CSRF_SAFE_METHODS:
        return
    expected = session.get("_csrf_token")
    sent = request.headers.get("X-CSRFToken") or request.form.get("csrf_token", "")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        log.warning("CSRF validation failed for %s %s", request.method, request.path)
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"status": "error", "message": "CSRF validation failed"}), 403
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("auth.login"))


@app.teardown_appcontext
def _close_db(exc):
    db.close_connection()


# --- Register blueprints ---

from routes.auth import auth_bp
from routes.pages import pages_bp
from routes.api import api_bp

app.register_blueprint(auth_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(api_bp)


if __name__ == "__main__":
    db.init()
    app.secret_key = config.get_secret_key()

    from scheduler import start_scheduler
    start_scheduler()

    log.info("Ready — refresh data from the web UI or wait for scheduled refresh")

    from waitress import serve
    serve(app, host="0.0.0.0", port=config.FLASK_PORT)
