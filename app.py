"""TruAni — Seasonal anime manager. Flask application entry point."""

import logging
from datetime import timedelta
from urllib.parse import urlparse

from flask import Flask, jsonify, request

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


@app.context_processor
def _inject_globals():
    return {"app_version": config.APP_VERSION}


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.before_request
def _csrf_check():
    """Verify Origin header on state-changing requests to prevent CSRF."""
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return
    if not request.is_json and request.endpoint in ("auth.login", "auth.setup"):
        return
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return
    try:
        parsed = urlparse(origin)
        req_host = request.host.split(":")[0]
        origin_host = parsed.hostname or ""
        if origin_host != req_host:
            log.warning("CSRF: Origin %s does not match host %s", origin, request.host)
            return jsonify({"status": "error", "message": "Cross-origin request blocked"}), 403
    except Exception:
        return jsonify({"status": "error", "message": "Invalid origin"}), 403


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
