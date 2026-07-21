"""Background job status for manual price checks."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "results": [],
    "error": None,
    "message": None,
}


def get_status() -> dict:
    with _lock:
        return dict(_state)


def is_running() -> bool:
    with _lock:
        return bool(_state["running"])


def _set(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)


def start_check_all() -> tuple[bool, str]:
    """
    Kick off a background check of all active cruises.
    Returns (started, message).
    """
    if is_running():
        return False, "A price check is already running — wait a moment, then refresh."

    def worker() -> None:
        from app.database import SessionLocal
        from app.models import Cruise
        from app.tracker import check_all_active, check_cabin_availability

        _set(
            running=True,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            results=[],
            error=None,
            message="Checking prices on all booking sites…",
        )
        db = SessionLocal()
        try:
            results = check_all_active(db)
            ok = sum(1 for r in results if r.get("ok"))

            _set(message="Checking cabin availability…")
            cabin_results = []
            id90_cruises = (
                db.query(Cruise)
                .filter(Cruise.active.is_(True), Cruise.url.ilike("%id90travel%"))
                .all()
            )
            for cruise in id90_cruises:
                cabin_results.append(check_cabin_availability(db, cruise))
            cabin_ok = sum(1 for r in cabin_results if r.get("ok"))

            msg = (
                f"Check finished: {ok}/{len(results)} sources updated, "
                f"{cabin_ok}/{len(cabin_results)} cabin availability checks ok."
                if cabin_results
                else f"Check finished: {ok}/{len(results)} sources updated successfully."
            )
            _set(
                running=False,
                finished_at=datetime.now(timezone.utc).isoformat(),
                results=results + cabin_results,
                message=msg,
                error=None,
            )
            logger.info(msg)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Background check-all failed")
            _set(
                running=False,
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
                message=f"Check failed: {exc}",
            )
        finally:
            db.close()

    thread = threading.Thread(target=worker, name="check-all", daemon=True)
    thread.start()
    return True, "Price check started — this can take a few minutes while pages load."
