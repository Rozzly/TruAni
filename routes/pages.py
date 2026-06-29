"""Page routes (index, settings)."""

from flask import Blueprint, render_template, request, session

import db
from core import default_season, compute_stats, get_last_refresh, build_season_nav
from routes.auth import login_required
from services.sonarr import test_connection

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
@login_required
def index():
    season, year = default_season(request.args.get("season", "").upper(), request.args.get("year", type=int))

    anime_list = db.get_season_anime(season, year)
    ignored_list = db.get_ignored_anime(season, year)

    stats = compute_stats(anime_list, ignored_list)

    sonarr_ok, sonarr_msg = test_connection()
    nav = build_season_nav(season, year)

    return render_template(
        "index.html",
        anime_list=anime_list,
        ignored_list=ignored_list,
        season=season,
        year=year,
        stats=stats,
        sonarr_ok=sonarr_ok,
        sonarr_msg=sonarr_msg,
        last_refresh=get_last_refresh(),
        refresh_status="idle",
        user=session.get("user"),
        nav=nav,
    )


@pages_bp.route("/settings")
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
