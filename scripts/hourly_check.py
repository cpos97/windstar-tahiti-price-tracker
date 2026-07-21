#!/usr/bin/env python3
"""
Hourly cruise price check — for cron, GitHub Actions, or manual ops.

Runs the same logic as the dashboard / local scheduler:
  1) Optionally refresh Perx/ID90 login session
  2) Scrape all active cruises
  3) Append price history on changes
  4) Email on drops (YOUR TAHITI CRUISE DROPPED IN PRICE!)

Usage:
  cd ~/cruise-price-tracker
  source .venv/bin/activate
  python scripts/hourly_check.py

  # Force re-login before scrape (recommended in cloud):
  python scripts/hourly_check.py --refresh-login
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db  # noqa: E402
from app.tracker import check_all_active  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hourly_check")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run hourly cruise price checks")
    parser.add_argument(
        "--refresh-login",
        action="store_true",
        help="Re-login to Perx/ID90 using .env credentials before scraping",
    )
    args = parser.parse_args()

    init_db()

    if args.refresh_login:
        from app.auth_sites import save_session_with_credentials

        logger.info("Refreshing browser login session…")
        login_result = save_session_with_credentials(headless=True)
        logger.info("Login result: %s", login_result)

    db = SessionLocal()
    try:
        results = check_all_active(db)
    finally:
        db.close()

    ok = sum(1 for r in results if r.get("ok"))
    drops = [r for r in results if r.get("alert")]
    logger.info(
        "Hourly check done: %s/%s ok, %s price-drop alert(s)",
        ok,
        len(results),
        len(drops),
    )
    for r in results:
        logger.info(
            "  cruise_id=%s ok=%s price=%s alert=%s error=%s",
            r.get("cruise_id"),
            r.get("ok"),
            r.get("price"),
            bool(r.get("alert")),
            r.get("error"),
        )

    # Non-zero exit if all scrapes failed (helps monitoring)
    if results and ok == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
