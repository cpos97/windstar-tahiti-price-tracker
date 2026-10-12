"""Watch Koala and RedWeek for timeshare stays that fit a trip's rules.

Each Trip has its own resorts and rules — Winter Getaway wants a Thu/Fri/Sat
check-in, 3–4 nights, Jan–Mar; Summer Banff wants a studio, 1 or 2 bedroom
any time Jul 23 – Sep 30. Both alert on anything under $200/night (the
listed nightly rate, before fees). Stays that are in season and under the
price but miss another rule are kept as "near" so the page shows what is
out there.

Both sites are read with plain HTTP — no browser — and gently (a pause
between requests, one run every few hours).
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models import GetawayListing, GetawayPriceHistory, GetawayResortStatus

logger = logging.getLogger(__name__)

# Grouped by destination. "koala" and "redweek" are lists because some
# resorts are split into separately-listed sections on those sites.
WINTER_RESORTS = [
    {
        "name": "Marriott's Aruba Surf Club",
        "short": "Surf Club",
        "area": "Aruba",
        "koala": [("marriott-s-aruba-surf-club", 78)],
        "redweek": ["P4872-marriotts-aruba-surf-club"],
    },
    {
        "name": "Marriott's Aruba Ocean Club",
        "short": "Ocean Club",
        "area": "Aruba",
        "koala": [("marriotts-aruba-ocean-club", 54)],
        "redweek": ["P148-marriotts-aruba-ocean-club"],
    },
    {
        "name": "Harborside Resort at Atlantis",
        "short": "Harborside",
        "area": "Bahamas",
        "koala": [("harborside-resort-at-atlantis", 68)],
        "redweek": ["P4748-harborside-resort-at-atlantis"],
    },
    {
        "name": "Pueblo Bonito Sunset Beach",
        "short": "Pueblo Bonito",
        "area": "Cabo",
        "koala": [("pueblo-bonito-sunset-beach", 47)],
        "redweek": ["P278-pueblo-bonito-sunset-beach-resort-spa"],
    },
    {
        "name": "Hilton Vacation Club Cabo Azul",
        "short": "Cabo Azul",
        "area": "Cabo",
        "koala": [("cabo-azul-resort-by-diamond-resorts", 1134)],
        "redweek": ["P5748-hilton-vacation-club-cabo-azul-los-cabos"],
    },
    {
        "name": "Westin Los Cabos Resort Villas",
        "short": "Westin Cabo",
        "area": "Cabo",
        "koala": [
            ("westin-los-cabos-resort-villas-and-spa", 1132),
            ("westin-los-cabos-resort-villas-baja-point", 2996),
        ],
        "redweek": [
            "P6496-the-westin-los-cabos-resort-villas-spa",
            "P5594-the-westin-los-cabos-resort-villas-baja",
        ],
    },
    {
        "name": "Hyatt Sunset Harbor",
        "short": "Sunset Harbor",
        "area": "Key West",
        "koala": [("hyatt-key-west-sunset-harbor", 491)],
        "redweek": ["P1178-hyatt-sunset-harbor-resort"],
    },
    {
        "name": "Hyatt Beach House",
        "short": "Beach House",
        "area": "Key West",
        "koala": [("hyatt-key-west-beach-house", 488)],
        "redweek": ["P1177-hyatt-beach-house-resort"],
    },
    {
        "name": "Hyatt Windward Pointe",
        "short": "Windward Pointe",
        "area": "Key West",
        "koala": [("hyatt-residence-club-key-west-windward-pointe", 197)],
        "redweek": ["P5196-hyatt-windward-pointe-resort"],
    },
    {
        "name": "The Galleon Resort",
        "short": "Galleon",
        "area": "Key West",
        "koala": [("the-galleon-resort", 659)],
        "redweek": ["P1183-the-galleon-resort"],
    },
    {
        "name": "Hilton Vacation Club Flamingo Beach",
        "short": "Flamingo Beach",
        "area": "St. Maarten",
        "koala": [("flamingo-beach-resort", 2395)],
        "redweek": ["P179-hilton-vacation-club-flamingo-beach-st"],
    },
    {
        "name": "Westin St. John Resort Villas",
        "short": "Westin St. John",
        "area": "St. John",
        "koala": [("westin-st-john-resort-villas", 39)],
        "redweek": [
            "P4649-the-westin-st-john-virgin-grand-villas",
            "P200-the-westin-st-john-bay-vista-villas",
            "P6450-the-westin-st-john-sunset-bay-villas",
            "P6381-the-westin-st-john-coral-vista-villas",
        ],
    },
    {
        "name": "Marriott's Frenchman's Cove",
        "short": "Frenchman's Cove",
        "area": "St. Thomas",
        "koala": [("marriotts-frenchmans-cove", 56)],
        "redweek": ["P5504-marriotts-frenchmans-cove"],
    },
]

BANFF_RESORTS = [
    {
        "name": "WorldMark Canmore-Banff",
        "short": "WorldMark Canmore",
        "area": "Canmore",
        "koala": [("worldmark-canmorebanff", 310)],
        "redweek": ["P5754-worldmark-canmore-banff"],
    },
]

BEDROOM_LABELS = {0: "Studio", 1: "1 Bedroom", 2: "2 Bedroom"}
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class Trip:
    key: str  # stored on every DB row for this trip
    name: str
    path: str  # dashboard URL
    resorts: list = field(hash=False)
    season: tuple  # ((month, day), (month, day)), inclusive check-in window
    max_price: float = 200.0  # strictly under, listed nightly rate
    checkin_weekdays: tuple | None = None  # None = any day
    nights: tuple | None = None  # None = any length
    bedrooms: tuple | None = None  # None = any size; 0 = studio
    # Scan every stay even when nothing is under the price, so the page can
    # show the going rate per unit size (cheap with one resort).
    always_scan: bool = False
    # Optional existing booking to compare against
    booking: dict | None = field(default=None, hash=False)

    @property
    def rules(self) -> list[str]:
        out = []
        if self.checkin_weekdays:
            out.append("/".join(WEEKDAY_NAMES[d] for d in self.checkin_weekdays) + " check-in")
        if self.nights:
            out.append(f"{min(self.nights)}–{max(self.nights)} nights")
        if self.bedrooms is not None:
            names = [BEDROOM_LABELS.get(b, f"{b} Bedroom") for b in self.bedrooms]
            out.append(", ".join(names[:-1]) + " or " + names[-1] if len(names) > 1 else names[0])
        out.append(f"under ${self.max_price:,.0f}/night")
        return out


TRIPS: dict[str, Trip] = {
    "winter": Trip(
        key="winter",
        name="Winter Getaway",
        path="/getaways",
        resorts=WINTER_RESORTS,
        season=((1, 1), (3, 31)),
        checkin_weekdays=(3, 4, 5),
        nights=(3, 4),
    ),
    "banff": Trip(
        key="banff",
        name="Summer Banff Vacation",
        path="/banff",
        resorts=BANFF_RESORTS,
        season=((7, 23), (9, 30)),
        bedrooms=(0, 1, 2),
        always_scan=True,
        booking={
            "check_in": date(2027, 8, 7),
            "check_out": date(2027, 8, 14),
            "unit": "2 Bedroom",
            "total_usd": 1459.0,
            "note": "all-in",
        },
    ),
}

# Back-compat for the single-trip callers
RESORTS = WINTER_RESORTS
MAX_PRICE_NIGHT = 200.0

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
REQUEST_PAUSE_S = 1.5
# The per-stay Koala scan is the bulk of the traffic, so it goes slower and
# is capped — RedWeek already rate-limited this server once.
KOALA_SCAN_PAUSE_S = 3.0
KOALA_MAX_QUERIES_PER_RESORT = 60


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
    bedrooms: int | None = None

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


def bedrooms_from_text(text: str | None) -> int | None:
    """'Studio Efficiency' -> 0, '2 Bedroom Suite' -> 2, unknown -> None."""
    if not text:
        return None
    if re.search(r"\b(studio|efficiency)\b", text, re.IGNORECASE):
        return 0
    m = re.search(r"(\d+)\s*(?:-\s*)?(?:bed(?:room)?s?|br)\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def season_window(trip: Trip | None = None, today: date | None = None) -> tuple[date, date]:
    """The check-in window being hunted: this year's while it lasts, else next year's."""
    trip = trip or TRIPS["winter"]
    today = today or date.today()
    (m1, d1), (m2, d2) = trip.season
    year = today.year if today <= date(today.year, m2, d2) else today.year + 1
    return max(date(year, m1, d1), today), date(year, m2, d2)


def _rule_misses(stay, trip: Trip) -> list[str]:
    misses = []
    if trip.nights and stay.nights not in trip.nights:
        misses.append(f"{stay.nights} nights")
    if trip.checkin_weekdays and stay.check_in.weekday() not in trip.checkin_weekdays:
        misses.append(f"{stay.check_in.strftime('%a')} check-in")
    beds = getattr(stay, "bedrooms", None)
    if trip.bedrooms is not None and beds is not None and beds not in trip.bedrooms:
        misses.append(BEDROOM_LABELS.get(beds, f"{beds} bedrooms"))
    return misses


def classify(stay: Stay, trip: Trip, start: date, end: date) -> str | None:
    """'match', 'near', or None (out of season, or not under the price)."""
    if not (start <= stay.check_in <= end) or stay.price_night >= trip.max_price:
        return None
    return "near" if _rule_misses(stay, trip) else "match"


def miss_reason(stay, trip: Trip | None = None) -> str:
    """Why a near-miss isn't a match, for the page."""
    return ", ".join(_rule_misses(stay, trip or TRIPS["winter"]))


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
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
                bedrooms=bedrooms_from_text(unit_el.get_text(" ", strip=True) if unit_el else None),
            )
        )
    return stays


def fetch_redweek(
    trip: Trip, resort: dict, start: date, end: date
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
            if cards < 100 or (
                not trip.always_scan and prices and prices[-1] >= trip.max_price
            ):
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
        try:
            beds = int(unit.get("bedrooms"))
        except (TypeError, ValueError):
            beds = bedrooms_from_text(unit.get("name"))
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
                        # Open-availability stays come back with listing_id 0, so
                        # the resort slug and Koala's per-stay id keep keys unique
                        key=(
                            f"koala:{slug}:"
                            f"{l.get('listing_id') or l.get('pt_rt_li_id') or unit.get('id')}:{ci}:{co}"
                        ),
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
                        bedrooms=beds,
                    )
                )
    return stays


def _koala_windows(trip: Trip, start: date, end: date, enabled: set[date]) -> list[tuple[date, date]]:
    """Which check-in/check-out pairs to ask Koala about.

    With fixed rules (Thu–Sat, 3–4 nights) that's every qualifying stay. With
    open rules (any day, any length) it's a week-long window stepping through
    each run of available days — Koala also returns similar nearby stays for
    each query, which fills in the gaps.
    """
    windows: list[tuple[date, date]] = []
    if trip.checkin_weekdays and trip.nights:
        day = start
        while day <= end:
            if day.weekday() in trip.checkin_weekdays and (not enabled or day in enabled):
                windows += [(day, day + timedelta(days=n)) for n in trip.nights]
            day += timedelta(days=1)
        return windows
    day = start
    while day <= end:
        if enabled and day not in enabled:
            day += timedelta(days=1)
            continue
        windows.append((day, day + timedelta(days=7)))
        day += timedelta(days=7)
    return windows


def _fetch_koala_section(
    trip: Trip, resort: dict, slug: str, rid: int, start: date, end: date
) -> tuple[list[Stay], float | None]:
    """One Koala listing page's stays for the season, and its cheapest nightly rate."""
    info = _koala_json(
        f"resort/enabled/dates/{slug}",
        _koala_params(slug, rid, startD=start.isoformat(), endD=end.isoformat()),
    )
    try:
        cheapest = float(info.get("minPrice")) if info.get("minPrice") is not None else None
    except (TypeError, ValueError):
        cheapest = None
    time.sleep(REQUEST_PAUSE_S)
    if cheapest is not None and cheapest >= trip.max_price and not trip.always_scan:
        return [], cheapest

    enabled = _koala_enabled_days(info.get("enabledRanges") or [])
    if cheapest is None and not enabled:
        return [], None  # nothing listed this season
    stays: list[Stay] = []
    seen: set[str] = set()
    for ci, co in _koala_windows(trip, start, end, enabled)[:KOALA_MAX_QUERIES_PER_RESORT]:
        payload = _koala_json(
            "pt/rt/resort/availability/optimised",
            _koala_params(slug, rid, startD=ci.isoformat(), endD=co.isoformat()),
        )
        for s in _koala_stays(payload, resort, slug):
            if s.key not in seen:
                seen.add(s.key)
                stays.append(s)
        time.sleep(KOALA_SCAN_PAUSE_S)
    return stays, cheapest


def fetch_koala(
    trip: Trip, resort: dict, start: date, end: date
) -> tuple[list[Stay], str | None, float | None]:
    """Koala stays for the season. Returns (stays, note, cheapest).

    Koala only lists stays for a given check-in/check-out, so this first asks
    for the resort's cheapest nightly price across the whole season — one
    request. If even that isn't under the limit nothing can match, so no
    further requests are made (unless the trip wants every rate). Otherwise
    it asks about the stays the trip's rules allow.
    """
    stays: list[Stay] = []
    cheapest: float | None = None
    for slug, rid in resort["koala"]:
        section_stays, section_cheapest = _fetch_koala_section(trip, resort, slug, rid, start, end)
        stays.extend(section_stays)
        if section_cheapest is not None:
            cheapest = section_cheapest if cheapest is None else min(cheapest, section_cheapest)
    return stays, None, cheapest


# ── Run ──────────────────────────────────────────────────────────────────────

_run_lock = threading.Lock()
FETCHERS = {"redweek": fetch_redweek, "koala": fetch_koala}
SOURCES = ("redweek", "koala")
SOURCE_LABELS = {"redweek": "RedWeek", "koala": "Koala"}
NOT_RESPONDING = "Not responding"


def is_running() -> bool:
    return _run_lock.locked()


def backfill_trip_keys(db: Session) -> None:
    """Rows written before trips existed all belong to Winter Getaway.

    Listing keys also gain a trip prefix, so one resort could be watched by
    two trips without their rows colliding.
    """
    n = 0
    for row in db.query(GetawayListing).filter(GetawayListing.trip.is_(None)).all():
        row.trip = "winter"
        if not row.key.startswith("winter:"):
            row.key = f"winter:{row.key}"
        n += 1
    for model in (GetawayResortStatus, GetawayPriceHistory):
        for row in db.query(model).filter(model.trip.is_(None)).all():
            row.trip = "winter"
            n += 1
    if n:
        db.commit()
        logger.info("Backfilled trip=winter on %d getaway rows", n)


def _status_row(db: Session, trip: Trip, source: str, resort: str) -> GetawayResortStatus:
    row = (
        db.query(GetawayResortStatus)
        .filter(
            GetawayResortStatus.trip == trip.key,
            GetawayResortStatus.source == source,
            GetawayResortStatus.resort == resort,
        )
        .first()
    )
    if row is None:
        row = GetawayResortStatus(trip=trip.key, source=source, resort=resort)
        db.add(row)
    return row


def _is_unreachable(exc: Exception) -> bool:
    """A dead connection (timeout, refused) as opposed to one bad page.

    HTTPError is a URLError too, but a 404/500 is about that resort's page,
    not the site being down.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError))


def _check_one(
    db: Session, trip: Trip, resort: dict, source: str, start: date, end: date, now: datetime
) -> bool:
    """Check one resort on one site. False if the site itself isn't reachable."""
    status = _status_row(db, trip, source, resort["name"])
    status.last_checked = now
    try:
        stays, note, cheapest_hint = FETCHERS[source](trip, resort, start, end)
    except Exception as exc:  # noqa: BLE001 — one site/resort must not stop the rest
        logger.warning("Getaway check failed: %s @ %s: %s", resort["name"], source, exc)
        unreachable = _is_unreachable(exc)
        status.last_error = NOT_RESPONDING if unreachable else str(exc)[:500]
        db.commit()
        # Keep the listings and prices we already know about rather than dropping them
        return not unreachable

    in_season = [s for s in stays if start <= s.check_in <= end]
    wanted = [
        s for s in in_season
        if trip.bedrooms is None or s.bedrooms is None or s.bedrooms in trip.bedrooms
    ]
    status.cheapest_price = min((s.price_night for s in wanted), default=cheapest_hint)
    by_size: dict[str, float] = {}
    for s in in_season:
        if s.bedrooms is not None:
            k = str(s.bedrooms)
            by_size[k] = min(by_size.get(k, s.price_night), s.price_night)
    status.size_prices = json.dumps(by_size) if by_size else None
    status.note = note
    status.last_error = None
    status.last_ok = now

    seen: set[str] = set()
    for stay in stays:
        kind = classify(stay, trip, start, end)
        key = f"{trip.key}:{stay.key}"
        if kind is None or key in seen:
            continue
        seen.add(key)
        row = db.query(GetawayListing).filter(GetawayListing.key == key).first()
        if row is None:
            row = GetawayListing(key=key, trip=trip.key, first_seen=now)
            db.add(row)
        row.source = stay.source
        row.resort = stay.resort
        row.area = stay.area
        row.unit = (stay.unit or "")[:200] or None
        row.bedrooms = stay.bedrooms
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
            GetawayListing.trip == trip.key,
            GetawayListing.source == source,
            GetawayListing.resort == resort["name"],
            GetawayListing.active.is_(True),
        )
        .all()
    ):
        if row.key not in seen:
            row.active = False
    db.commit()
    return True


def cheapest_by_source(db: Session, trip: Trip | None = None) -> dict[str, GetawayResortStatus | None]:
    """Each site's cheapest in-season resort right now, for one trip."""
    trip = trip or TRIPS["winter"]
    out: dict[str, GetawayResortStatus | None] = {}
    for source in SOURCES:
        rows = (
            db.query(GetawayResortStatus)
            .filter(
                GetawayResortStatus.trip == trip.key,
                GetawayResortStatus.source == source,
                GetawayResortStatus.cheapest_price.isnot(None),
            )
            .all()
        )
        out[source] = min(rows, key=lambda r: r.cheapest_price, default=None)
    return out


def _log_cheapest(db: Session, trip: Trip, now: datetime) -> None:
    """Append a price-history point per site when its cheapest rate changes."""
    for source, best in cheapest_by_source(db, trip).items():
        if best is None:
            continue
        last = (
            db.query(GetawayPriceHistory)
            .filter(GetawayPriceHistory.trip == trip.key, GetawayPriceHistory.source == source)
            .order_by(GetawayPriceHistory.checked_at.desc())
            .first()
        )
        if last is None or abs(last.price - best.cheapest_price) >= 0.01:
            db.add(
                GetawayPriceHistory(
                    trip=trip.key,
                    source=source,
                    price=best.cheapest_price,
                    resort=best.resort,
                    checked_at=now,
                )
            )
    db.commit()


def run_getaway_check(db: Session, trip_keys: list[str] | None = None) -> dict:
    """Check every trip's resorts on both sites; email about brand-new matches."""
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "message": "A getaway check is already running"}
    try:
        backfill_trip_keys(db)
        now = datetime.now(timezone.utc)
        # If a site stops answering (e.g. it has rate-limited this server),
        # don't keep knocking for every remaining resort — mark them and move
        # on; the next scheduled check tries again.
        down: set[str] = set()
        results = {}
        for key in trip_keys or list(TRIPS):
            trip = TRIPS[key]
            start, end = season_window(trip)
            for resort in trip.resorts:
                for source in SOURCES:
                    if source in down:
                        status = _status_row(db, trip, source, resort["name"])
                        status.last_checked = now
                        status.last_error = NOT_RESPONDING
                        db.commit()
                        continue
                    if not _check_one(db, trip, resort, source, start, end, now):
                        down.add(source)
                        logger.warning(
                            "%s isn't responding — skipping it until the next check", source
                        )
            _log_cheapest(db, trip, now)
            results[key] = _notify_new_matches(db, trip, now)
        return {"ok": True, **results}
    finally:
        _run_lock.release()


def _notify_new_matches(db: Session, trip: Trip, now: datetime) -> dict:
    new_matches = (
        db.query(GetawayListing)
        .filter(
            GetawayListing.trip == trip.key,
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

        emailed, msg = send_getaway_matches_email(new_matches, trip)
        logger.info(
            "%s match email (%d new): ok=%s %s", trip.name, len(new_matches), emailed, msg
        )
        if emailed:
            for row in new_matches:
                row.notified_at = now
            db.commit()
    return {"new_matches": len(new_matches), "emailed": emailed}


def start_background_check(trip_keys: list[str] | None = None) -> bool:
    """Kick off a check from a web request. False if one is already running."""
    if is_running():
        return False
    from app.database import SessionLocal

    def run() -> None:
        db = SessionLocal()
        try:
            run_getaway_check(db, trip_keys)
        except Exception:  # noqa: BLE001
            logger.exception("Getaway check crashed")
        finally:
            db.close()

    threading.Thread(target=run, name="getaway-check", daemon=True).start()
    return True
