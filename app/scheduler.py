"""Background scheduler for periodic price checks."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import config
from app.database import SessionLocal
from app.models import Cruise
from app.tracker import check_all_active, check_cabin_availability, refresh_login_session

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# Once a day is plenty for cabin counts — each check walks 5 live category
# pages against ID90's vendor system, so it's not something to run often.
#
# Pinned to Eastern explicitly rather than relying on the host clock: the
# cloud VM runs on UTC, so a bare hour=15 would fire at 11:02 AM ET instead
# of the intended 3:02 PM. ZoneInfo also handles EST/EDT automatically.
CABIN_CHECK_TZ = ZoneInfo("America/New_York")
CABIN_CHECK_HOUR = 15
CABIN_CHECK_MINUTE = 2

# Proactively re-login weekly so the saved session rarely gets old enough to
# expire mid-check. Scrapes also refresh on demand if they hit a login wall;
# this just makes that path rare. Sunday 4:10 AM ET — off-hours, and well
# clear of the daily cabin check.
SESSION_REFRESH_DAY = "sun"
SESSION_REFRESH_HOUR = 4
SESSION_REFRESH_MINUTE = 10


def _job() -> None:
    logger.info("Scheduled price check starting…")
    db = SessionLocal()
    try:
        results = check_all_active(db)
        ok = sum(1 for r in results if r.get("ok"))
        logger.info("Scheduled check done: %s/%s ok", ok, len(results))
    finally:
        db.close()


def _cabin_job() -> None:
    logger.info("Scheduled cabin availability check starting…")
    db = SessionLocal()
    try:
        cruises = db.query(Cruise).filter(Cruise.active.is_(True)).all()
        for cruise in cruises:
            if "id90travel" not in cruise.url.lower():
                continue
            result = check_cabin_availability(db, cruise)
            logger.info("Cabin availability check for cruise %s: %s", cruise.id, result.get("ok"))
    finally:
        db.close()


def _session_refresh_job() -> None:
    logger.info("Scheduled login session refresh starting…")
    ok = refresh_login_session()
    logger.info("Scheduled login session refresh: ok=%s", ok)


def start_scheduler() -> None:
    if scheduler.running:
        return
    if config.DISABLE_LOCAL_SCHEDULER:
        logger.info(
            "Local scheduler disabled (DISABLE_LOCAL_SCHEDULER). "
            "Use /api/check-prices or scripts/hourly_check.py via external cron."
        )
        return
    minutes = max(5, config.CHECK_INTERVAL_MINUTES)
    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="price_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _cabin_job,
        CronTrigger(
            hour=CABIN_CHECK_HOUR, minute=CABIN_CHECK_MINUTE, timezone=CABIN_CHECK_TZ
        ),
        id="cabin_availability_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _session_refresh_job,
        CronTrigger(
            day_of_week=SESSION_REFRESH_DAY,
            hour=SESSION_REFRESH_HOUR,
            minute=SESSION_REFRESH_MINUTE,
            timezone=CABIN_CHECK_TZ,
        ),
        id="login_session_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: price check every %s minutes, cabin availability daily at "
        "%02d:%02d, login refresh %s %02d:%02d (%s)",
        minutes,
        CABIN_CHECK_HOUR,
        CABIN_CHECK_MINUTE,
        SESSION_REFRESH_DAY,
        SESSION_REFRESH_HOUR,
        SESSION_REFRESH_MINUTE,
        CABIN_CHECK_TZ.key,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
