#!/usr/bin/env python3
"""
Ensure the two family booking sources exist in SQLite.

Used by GitHub Actions / fresh cloud disks so hourly checks have something
to scrape without opening the web UI first.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Cruise  # noqa: E402

# Prefer env overrides (ID90_CRUISE_URL / PERX_CRUISE_URL); otherwise defaults.
ID90_URL = config.ID90_CRUISE_URL or (
    "https://cruise.id90travel.com/cs/forms/CruiseDetails.aspx"
    "?skin=636&mon=5%2F1%2F2027&vid=664&pid=9476&pin=W8-1386879-1401"
    "&nr=y&did=-1&iid=3675911&sno=1"
)
PERX_URL = config.PERX_CRUISE_URL or (
    "https://perx.com/cruises/windstar-cruises/star-breeze/"
    "itineraries/223329/sailings/2027-05-20/"
)

DEFAULTS = [
    {
        "name": "ID90 · Star Breeze · French Polynesia · May 20 2027",
        "url": ID90_URL,
        "css_selector": "span.price.min",
        "expected_date": "2027-05-20",
        "match": "id90travel",
    },
    {
        "name": "Perx · Star Breeze · French Polynesia · May 20 2027",
        "url": PERX_URL,
        "css_selector": None,
        "expected_date": "2027-05-20",
        "match": "perx.com",
    },
]


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        for item in DEFAULTS:
            exists = (
                db.query(Cruise)
                .filter(Cruise.url.contains(item["match"]))
                .first()
            )
            if exists:
                # Don't touch `url` here — the tracker self-heals it when ID90's
                # iid drifts (see app/id90_search.py), and overwriting it back to
                # this static default on every run would undo that self-heal.
                if not exists.expected_date:
                    exists.expected_date = item["expected_date"]
                    db.commit()
                print(f"OK existing: {exists.name} (id={exists.id})")
                continue
            c = Cruise(
                name=item["name"],
                url=item["url"],
                css_selector=item["css_selector"],
                expected_date=item["expected_date"],
                active=True,
            )
            db.add(c)
            db.commit()
            db.refresh(c)
            print(f"Created: {c.name} (id={c.id})")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
