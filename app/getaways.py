"""Watch Koala and RedWeek for short Caribbean timeshare stays.

The rules: a Thursday, Friday or Saturday check-in between January and March,
3 or 4 nights, under $200/night (the listed nightly rate, before fees), at a
handful of resorts. Both sites are mostly 7-night owner weeks, so true matches
are rare; stays that are in season and under the price but miss on length or
check-in day are kept as "near" so the page shows what is out there.

Both sites are read with plain HTTP — no browser — and gently (a pause
between requests, one run every few hours).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models import GetawayListing, GetawayPriceHistory, GetawayResortStatus

logger = logging.getLogger(__name__)

MAX_PRICE_NIGHT = 200.0  # strictly under, listed nightly rate
CHECKIN_WEEKDAYS = (3, 4, 5)  # Thu, Fri, Sat
NIGHTS = (3, 4)
SEASON_MONTHS = (1, 3)  # January through March

RESORTS = [
    {
        "name": "Marriott's Aruba Surf Club",
        "short": "Surf Club",
        "area": "Aruba",
        "koala": ("marriott-s-aruba-surf-club", 78),
        "redweek": ["P4872-marriotts-aruba-surf-club"],
    },
    {
        "name": "Marriott's Aruba Ocean Club",
        "short": "Ocean Club",
        "area": "Aruba",
        "koala": ("marriotts-aruba-ocean-club", 54),
        "redweek": ["P148-marriotts-aruba-ocean-club"],
    },
    {
        "name": "Hyatt Sunset Harbor",
        "short": "Sunset Harbor",
        "area": "Key West",
        "koala": ("hyatt-key-west-sunset-harbor", 491),
        "redweek": ["P1178-hyatt-sunset-harbor-resort"],
    },
    {
        "name": "Hyatt Beach House",
        "short": "Beach House",
        "area": "Key West",
        "koala": ("hyatt-key-west-beach-house", 488),
        "redweek": ["P1177-hyatt-beach-house-resort"],
    },
    {
        "name": "Hyatt Windward Pointe",
        "short": "Windward Pointe",
        "area": "Key West",
        "koala": ("hyatt-residence-club-key-west-windward-pointe", 197),
        "redweek": ["P5196-hyatt-windward-pointe-resort"],
    },
    {
        "name": "The Galleon Resort",
        "short": "Galleon",
        "area": "Key West",
        "koala": ("the-galleon-resort", 659),
        "redweek": ["P1183-the-galleon-resort"],
    },
    {
        "name": "Westin St. John Resort Villas",
        "short": "Westin",
        "area": "St. John",
        "koala": ("westin-st-john-resort-villas", 39),
        # RedWeek splits the Westin into its four timeshare sections
        "redweek": [
            "P4649-the-westin-st-john-virgin-grand-villas",
            "P200-the-westin-st-john-bay-vista-villas",
            "P6450-the-westin-st-john-sunset-bay-villas",
            "P6381-the-westin-st-john-coral-vista-villas",
        ],
    },
]

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
REQUEST_PAUSE_S = 1.5
KOALA_MAX_QUERIES_PER_RESORT = 80


@dataclass
class Stay:
    source: str
    key: str
    resort: str
    area: str
    unit: str | None
    check_in: date
    check_out: date
    price_night: float
    total_price: float | None
    url: str

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


def season_window(today: date | None = None) -> tuple[date, date]:
    """The Jan–Mar season being hunted: this year's while it lasts, else next year's."""
    today = today or date.today()
    first, last = SEASON_MONTHS
    year = today.year if today.month <= last else today.year + 1
    start = max(date(year, first, 1), today)
    end = date(year, last + 1, 1) - timedelta(days=1)
    return start, end


def classify(stay: Stay, start: date, end: date) -> str | None:
    """'match', 'near', or None (out of season, or not under the price)."""
    if not (start <= stay.check_in <= end) or stay.price_night >= MAX_PRICE_NIGHT:
        return None
    if stay.check_in.weekday() in CHECKIN_WEEKDAYS and stay.nights in NIGHTS:
        return "match"
    return "near"


def miss_reason(stay) -> str:
    """Why a near-miss isn't a match, for the page."""
    reasons = []
    if stay.nights not in NIGHTS:
        reasons.append(f"{stay.nights} nights")
    if stay.check_in.weekday() not in CHECKIN_WEEKDAYS:
        reasons.append(f"{stay.check_in.strftime('%a')} check-in")
    return ", ".join(reasons)


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read()


# ── RedWeek ──────────────────────────────────────────────────────────────────

_RW_DATES = re.compile(
    r"([A-Za-z]{3})\s+(\d{1,2})(?:,\s*(\d{4}))?\s*-\s*(?:([A-Za-z]{3})\s+)?(\d{1,2}),\s*(\d{4})"
)


def parse_redweek_dates(text: str) -> tuple[date, date] | None:
    """'Dec 4–11, 2027' / 'Nov 28 – Dec 5, 2026' / 'Dec 26, 2026 – Jan 2, 2027'."""
    s = " ".join(text.replace("–", "-").replace("—", "-").split())
    m = _RW_DATES.search(s)
    if not m:
        return None
    mon1, d1, y1, mon2, d2, y2 = m.groups()
    mon2 = mon2 or mon1
    check_out = datetime.strptime(f"{mon2} {d2} {y2}", "%b %d %Y").date()
    check_in = datetime.strptime(f"{mon1} {d1} {y1 or y2}", "%b %d %Y").date()
    if not y1 and check_in > check_out:  # "Dec 28 – Jan 4, 2027"
        check_in = check_in.replace(year=check_in.year - 1)
    return check_in, check_out


def parse_redweek_page(html: str, resort: dict, page_url: str) -> list[Stay]:
    stays: list[Stay] = []
    for card in BeautifulSoup(html, "lxml").select("div.listing"):
        action = card.select_one(".listing-action")
        if action is None or "Rented" in action.get_text():
            continue
        posting = (action.get("title") or "").strip()
        dates_el = card.select_one(".listing-dates")
        price_el = card.select_one(".listing-price")
        if not (posting and dates_el and price_el):
            continue
        dates = parse_redweek_dates(dates_el.get_text(" ", strip=True))
        m_price = re.search(r"\$([\d,]+)/night", price_el.get_text(" ", strip=True))
        if not dates or not m_price:
            continue
        m_total = re.search(r"\$([\d,]+)\s+before tax", price_el.get_text(" ", strip=True))
        unit_el = card.select_one(".listing-be-ba")
        stays.append(
            Stay(
                source="redweek",
                key=f"redweek:{posting}",
                resort=resort["name"],
                area=resort["area"],
                unit=unit_el.get_text(" ", strip=True) if unit_el else None,
                check_in=dates[0],
                check_out=dates[1],
                price_night=float(m_price.group(1).replace(",", "")),
                total_price=float(m_total.group(1).replace(",", "")) if m_total else None,
                url=f"https://www.redweek.com/posting/{posting}",
            )
        )
    return stays


def fetch_redweek(
    resort: dict, start: date, end: date
) -> tuple[list[Stay], str | None, float | None]:
    """Unrented RedWeek stays for the season, cheapest first. Returns (stays, note, cheapest)."""
    stays: list[Stay] = []
    for rw_id in resort["redweek"]:
        for page in range(5):  # sorted by price, so a page or two is plenty
            url = (
                f"https://www.redweek.com/resort/{rw_id}/timeshare-rentals?"
                + urllib.parse.urlencode(
                    {
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        "sort": "per_night_price",
                        "collapse": 0,
                        "limit": 100,
                        "offset": page * 100,
                    }
                )
            )
            html = _get(url).decode("utf-8", "replace")
            page_stays = parse_redweek_page(html, resort, url)
            stays.extend(page_stays)
            time.sleep(REQUEST_PAUSE_S)
            cards = html.count('class="listing"')
            prices = [s.price_night for s in page_stays]
            if cards < 100 or (prices and prices[-1] >= MAX_PRICE_NIGHT):
                break
    return stays, None, None


# ── Koala ────────────────────────────────────────────────────────────────────

_KOALA_API = "https://admin.go-koala.com/api/auth"
_KOALA_HEADERS = {"Origin": "https://www.go-koala.com", "Referer": "https://www.go-koala.com/"}


def _koala_params(slug: str, rid: int, **overrides) -> str:
    p = {
        "email": 0, "slug": slug, "previewmode": 0, "previewListingId": "null", "hot_deals": 0,
        "availability_page": 1, "all_availability_page": 1, "loadmore": 0,
        "defaultView": "calenderview", "paginate": 25, "page": 1, "sleeps": 0, "minPrice": 0,
        "maxPrice": 0, "startD": "", "endD": "", "months": "", "beds": 0, "baths": 0, "mfBd": 0,
        "fkitchen": 0, "kkitchen": 0, "flexibility": "", "dateOption": 1, "getSimilarListings": 1,
        "unitId": "", "is_resortPage": 1, "dateFreeSearch": 0, "unitTypeSort": "", "sortName": "",
        "sortOrder": "", "unitAmenities": "", "bookingOptions": "", "cancelationPolicies": "",
        "recommendedTags": "", "pets_friendly": 0, "rId": rid, "featured_membership_deals": 1,
        "isFeatureListings": 1, "pageName": "resort_details", "getStaysByState": 1,
    }
    p.update(overrides)
    return urllib.parse.urlencode(p)


def _koala_json(path: str, query: str) -> dict:
    return json.loads(_get(f"{_KOALA_API}/{path}?{query}", _KOALA_HEADERS))


def _koala_enabled_days(ranges: list[dict]) -> set[date]:
    days: set[date] = set()
    for r in ranges or []:
        try:
            a = datetime.strptime(r["start"], "%d/%m/%Y").date()
            b = datetime.strptime(r["end"], "%d/%m/%Y").date()
        except (KeyError, ValueError):
            continue
        while a <= b:
            days.add(a)
            a += timedelta(days=1)
    return days


def _koala_stays(payload: dict, resort: dict, slug: str) -> list[Stay]:
    stays: list[Stay] = []
    units = ((payload.get("data") or {}).get("data")) or []
    for unit in units:
        for key in ("availableListings", "similarListings"):
            for l in unit.get(key) or []:
                try:
                    ci = date.fromisoformat(l["checkin_db"])
                    co = date.fromisoformat(l["checkout_db"])
                    price = float(l["price_night"])
                except (KeyError, TypeError, ValueError):
                    continue
                nights = (co - ci).days
                stays.append(
                    Stay(
                        source="koala",
                        key=f"koala:{l.get('listing_id')}:{ci}:{co}",
                        resort=resort["name"],
                        area=resort["area"],
                        unit=unit.get("name"),
                        check_in=ci,
                        check_out=co,
                        price_night=price,
                        total_price=round(price * nights, 2) if nights > 0 else None,
                        url=(
                            f"https://www.go-koala.com/resort/{slug}?startD={ci}&endD={co}"
                            "&dateOption=1"
                        ),
                    )
                )
    return stays


def fetch_koala(
    resort: dict, start: date, end: date
) -> tuple[list[Stay], str | None, float | None]:
    """Koala stays for the season. Returns (stays, note, cheapest).

    Koala only lists stays for a given check-in/check-out, so this first asks
    for the resort's cheapest nightly price across the whole season — one
    request. If even that isn't under the limit nothing can match, so no
    further requests are made. Otherwise it asks about each Thu/Fri/Sat
    3–4 night stay on days the resort has availability.
    """
    slug, rid = resort["koala"]
    info = _koala_json(
        f"resort/enabled/dates/{slug}",
        _koala_params(slug, rid, startD=start.isoformat(), endD=end.isoformat()),
    )
    try:
        cheapest = float(info.get("minPrice")) if info.get("minPrice") is not None else None
    except (TypeError, ValueError):
        cheapest = None
    time.sleep(REQUEST_PAUSE_S)
    if cheapest is not None and cheapest >= MAX_PRICE_NIGHT:
        return [], None, cheapest

    enabled = _koala_enabled_days(info.get("enabledRanges") or [])
    if cheapest is None and not enabled:
        return [], None, None  # nothing listed this season
    stays: list[Stay] = []
    seen: set[str] = set()
    queries = 0
    day = start
    while day <= end and queries < KOALA_MAX_QUERIES_PER_RESORT:
        if day.weekday() in CHECKIN_WEEKDAYS and (not enabled or day in enabled):
            for n in NIGHTS:
                co = day + timedelta(days=n)
                payload = _koala_json(
                    "pt/rt/resort/availability/optimised",
                    _koala_params(slug, rid, startD=day.isoformat(), endD=co.isoformat()),
                )
                queries += 1
                for s in _koala_stays(payload, resort, slug):
                    if s.key not in seen:
                        seen.add(s.key)
                        stays.append(s)
                time.sleep(REQUEST_PAUSE_S)
        day += timedelta(days=1)
    return stays, None, cheapest


# ── Run ──────────────────────────────────────────────────────────────────────

_run_lock = threading.Lock()
FETCHERS = {"redweek": fetch_redweek, "koala": fetch_koala}


def is_running() -> bool:
    return _run_lock.locked()


def _status_row(db: Session, source: str, resort: str) -> GetawayResortStatus:
    row = (
        db.query(GetawayResortStatus)
        .filter(GetawayResortStatus.source == source, GetawayResortStatus.resort == resort)
        .first()
    )
    if row is None:
        row = GetawayResortStatus(source=source, resort=resort)
        db.add(row)
    return row


def _check_one(db: Session, resort: dict, source: str, start: date, end: date, now: datetime) -> None:
    status = _status_row(db, source, resort["name"])
    status.last_checked = now
    try:
        stays, note, cheapest_hint = FETCHERS[source](resort, start, end)
    except Exception as exc:  # noqa: BLE001 — one site/resort must not stop the rest
        logger.warning("Getaway check failed: %s @ %s: %s", resort["name"], source, exc)
        status.last_error = str(exc)[:500]
        db.commit()
        return  # keep the listings we already know about rather than dropping them

    in_season = [s for s in stays if start <= s.check_in <= end]
    status.cheapest_price = min((s.price_night for s in in_season), default=cheapest_hint)
    status.note = note
    status.last_error = None

    seen: set[str] = set()
    for stay in stays:
        kind = classify(stay, start, end)
        if kind is None or stay.key in seen:
            continue
        seen.add(stay.key)
        row = db.query(GetawayListing).filter(GetawayListing.key == stay.key).first()
        if row is None:
            row = GetawayListing(key=stay.key, first_seen=now)
            db.add(row)
        row.source = stay.source
        row.resort = stay.resort
        row.area = stay.area
        row.unit = (stay.unit or "")[:200] or None
        row.check_in = stay.check_in
        row.check_out = stay.check_out
        row.nights = stay.nights
        row.price_night = stay.price_night
        row.total_price = stay.total_price
        row.url = stay.url
        row.kind = kind
        row.active = True
        row.last_seen = now

    # Anything we knew about for this resort+site that's gone has been booked or pulled
    for row in (
        db.query(GetawayListing)
        .filter(
            GetawayListing.source == source,
            GetawayListing.resort == resort["name"],
            GetawayListing.active.is_(True),
        )
        .all()
    ):
        if row.key not in seen:
            row.active = False
    db.commit()


SOURCES = ("redweek", "koala")
SOURCE_LABELS = {"redweek": "RedWeek", "koala": "Koala"}


def cheapest_by_source(db: Session) -> dict[str, GetawayResortStatus | None]:
    """Each site's cheapest in-season resort right now."""
    out: dict[str, GetawayResortStatus | None] = {}
    for source in SOURCES:
        rows = (
            db.query(GetawayResortStatus)
            .filter(
                GetawayResortStatus.source == source,
                GetawayResortStatus.cheapest_price.isnot(None),
            )
            .all()
        )
        out[source] = min(rows, key=lambda r: r.cheapest_price, default=None)
    return out


def _log_cheapest(db: Session, now: datetime) -> None:
    """Append a price-history point per site when its cheapest rate changes."""
    for source, best in cheapest_by_source(db).items():
        if best is None:
            continue
        last = (
            db.query(GetawayPriceHistory)
            .filter(GetawayPriceHistory.source == source)
            .order_by(GetawayPriceHistory.checked_at.desc())
            .first()
        )
        if last is None or abs(last.price - best.cheapest_price) >= 0.01:
            db.add(
                GetawayPriceHistory(
                    source=source, price=best.cheapest_price, resort=best.resort, checked_at=now
                )
            )
    db.commit()


def run_getaway_check(db: Session) -> dict:
    """Check every resort on both sites; email about brand-new matches."""
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "message": "A getaway check is already running"}
    try:
        start, end = season_window()
        now = datetime.now(timezone.utc)
        for resort in RESORTS:
            for source in ("redweek", "koala"):
                _check_one(db, resort, source, start, end, now)
        _log_cheapest(db, now)

        new_matches = (
            db.query(GetawayListing)
            .filter(
                GetawayListing.active.is_(True),
                GetawayListing.kind == "match",
                GetawayListing.notified_at.is_(None),
            )
            .order_by(GetawayListing.price_night)
            .all()
        )
        emailed = False
        if new_matches:
            from app.emailer import send_getaway_matches_email

            emailed, msg = send_getaway_matches_email(new_matches)
            logger.info("Getaway match email (%d new): ok=%s %s", len(new_matches), emailed, msg)
            if emailed:
                for row in new_matches:
                    row.notified_at = now
                db.commit()
        return {"ok": True, "new_matches": len(new_matches), "emailed": emailed}
    finally:
        _run_lock.release()


def start_background_check() -> bool:
    """Kick off a check from a web request. False if one is already running."""
    if is_running():
        return False
    from app.database import SessionLocal

    def run() -> None:
        db = SessionLocal()
        try:
            run_getaway_check(db)
        except Exception:  # noqa: BLE001
            logger.exception("Getaway check crashed")
        finally:
            db.close()

    threading.Thread(target=run, name="getaway-check", daemon=True).start()
    return True
