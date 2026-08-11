"""Background scheduler for periodic price checks."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
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

# If nothing has been checked in this long, assume a job is wedged and restart.
# ~3 missed 30-minute cycles.
STALE_RESTART_MINUTES = 90


def _job() -> None:
    logger.info("Scheduled price check starting…")
    db = SessionLocal()
    try:
        results = check_all_active(db)
        ok = sum(1 for r in results if r.get("ok"))
        logger.info("Scheduled check done: %s/%s ok", ok, len(results))
    finally:
        db.close()


def _watchdog_job() -> None:
    """Bail out of the process if a scheduled check has wedged.

    A Playwright call can hang with no timeout (seen 2026-08-09: one hung for
    two days). Because the price job runs with max_instances=1, a stuck run
    holds the only slot and every later run is skipped — silently, since the
    web app keeps serving. Playwright's sync API can't be interrupted from
    another thread, so the reliable escape is to end the process and let
    systemd's Restart=always bring it back clean.
    """
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        newest = None
        for cruise in db.query(Cruise).filter(Cruise.active.is_(True)).all():
            ts = cruise.last_checked
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            newest = ts if newest is None else max(newest, ts)
    finally:
        db.close()

    if newest is None:
        return

    stale_min = (datetime.now(timezone.utc) - newest).total_seconds() / 60
    if stale_min > STALE_RESTART_MINUTES:
        logger.error(
            "No successful check in %.0f min (limit %s) — a job is wedged; "
            "exiting so systemd restarts the service",
            stale_min,
            STALE_RESTART_MINUTES,
        )
        os._exit(1)  # noqa: SLF001 - deliberate hard exit; a hung thread blocks a clean one


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
    # Fire shortly after startup as well as on the interval. An interval
    # trigger otherwise counts from scheduler start, so every restart —
    # a deploy, a reboot, a watchdog recovery — silently pushed the next
    # check out by a further full interval.
    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="price_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now() + timedelta(seconds=60),
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
    # Runs in its own slot so a wedged price check can't block it too
    scheduler.add_job(
        _watchdog_job,
        "interval",
        minutes=15,
        id="stall_watchdog",
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
