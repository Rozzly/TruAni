"""Background scheduler for automatic refresh and update checks."""

import logging

import config
import db
from core import refresh_data
from services.anilist import current_season, advance_season, count_upcoming_titles

log = logging.getLogger("truani")

_scheduler = None

# An upcoming season is auto-created once AniList lists at least this many titles
# — enough to distinguish a genuinely-announced season from the handful of
# speculative entries AniList shows a year or more in advance.
UPCOMING_MIN_TITLES = 10
# Safety cap on how many seasons past the current one to look ahead.
UPCOMING_LOOKAHEAD = 4


@db.closes_connection
def scheduled_refresh():
    """Refresh the current season plus every upcoming season AniList already lists.
    Walks forward from the current season so newly-announced seasons (e.g. Fall
    appearing while it's still Spring) get created automatically, stopping at the
    first season AniList hasn't meaningfully populated yet."""
    season, year = current_season()
    refresh_data(season, year)

    for step in range(1, UPCOMING_LOOKAHEAD + 1):
        season, year = advance_season(season, year)
        # Always refresh the immediate next season; for seasons further out, only
        # create them once AniList has genuinely populated the listing.
        if step == 1 or count_upcoming_titles(season, year) >= UPCOMING_MIN_TITLES:
            refresh_data(season, year)
        else:
            break


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
    else:
        return CronTrigger(hour=hour, minute=minute)


def start_scheduler():
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler

    trigger = _build_trigger()
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(scheduled_refresh, trigger, id="refresh", replace_existing=True)

    from apscheduler.triggers.cron import CronTrigger as _CronTrigger
    from services.updater import check_for_update
    _scheduler.add_job(db.closes_connection(lambda: check_for_update(force=True)),
                       _CronTrigger(day_of_week="sun", hour=2, minute=0),
                       id="update_check", replace_existing=True)

    _scheduler.start()
    log.info("Refresh scheduled: %s at %s", config.refresh_frequency(), config.refresh_time())


def reschedule():
    if _scheduler:
        _scheduler.reschedule_job("refresh", trigger=_build_trigger())
        log.info("Rescheduled: %s at %s", config.refresh_frequency(), config.refresh_time())
