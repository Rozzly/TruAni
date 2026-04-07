"""Background scheduler for automatic refresh and update checks."""

import logging

import config
from core import refresh_data
from services.anilist import next_season

log = logging.getLogger("truani")

_scheduler = None


def scheduled_refresh():
    """Refresh both current and next season on schedule."""
    refresh_data()
    nxt_s, nxt_y = next_season()
    refresh_data(nxt_s, nxt_y)


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
    _scheduler.add_job(lambda: check_for_update(force=True),
                       _CronTrigger(day_of_week="sun", hour=2, minute=0),
                       id="update_check", replace_existing=True)

    _scheduler.start()
    log.info("Refresh scheduled: %s at %s", config.refresh_frequency(), config.refresh_time())


def reschedule():
    if _scheduler:
        _scheduler.reschedule_job("refresh", trigger=_build_trigger())
        log.info("Rescheduled: %s at %s", config.refresh_frequency(), config.refresh_time())
