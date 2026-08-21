"""Re-discover a fresh ID90 CruiseDetails URL when the saved one drifts.

ID90's booking URLs encode a sailing via `iid`, which is not a stable
permalink — it's tied to a search/browse session and goes stale after a
day or two, at which point the page silently falls back to showing a
*different* sailing instead of erroring. `vid` (vendor/cruise line) and
`pin` (product/fare code) stay constant for a given itinerary, though.

This walks ID90's real "Advanced Search" flow (same one a human would use)
scoped to the sailing's month, finds the result row whose departure date
matches, and clicks through to get a freshly-issued, currently-valid URL.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# The small cloud VM is far slower than a laptop and ID90's pages are sluggish;
# the original 20s navigation timeout made this re-search fail outright there.
NAV_TIMEOUT_MS = 90_000


def _search_url(params: dict, target_date: datetime) -> str:
    month_str = f"{target_date.month}%2F1%2F{target_date.year}"
    # `pid` scopes results to the exact fare product — without it the search
    # doesn't return this itinerary at all. Older saved URLs (from before we
    # started capturing it) omit it, so fall back to the last known-good
    # value; any freshly re-searched URL will include it going forward.
    pid = params.get("pid", ["9476"])[0] or "9476"
    return (
        "https://cruise.id90travel.com/cs/default.aspx?type=advanced"
        f"&skin={params.get('skin', ['636'])[0]}"
        f"&mon={month_str}"
        f"&vid={params['vid'][0]}"
        f"&pid={pid}"
        f"&pin={params['pin'][0]}"
        "&ad=2&ch=0&inf=0&nr=y&did=-1"
    )


def find_current_url(current_url: str, expected_date: str, storage_state: str | None) -> str | None:
    """Search ID90 fresh and return a currently-valid URL for expected_date (YYYY-MM-DD)."""
    parsed = urlparse(current_url)
    params = parse_qs(parsed.query)
    if "vid" not in params or "pin" not in params:
        logger.warning("Cannot search: URL missing vid/pin: %s", current_url)
        return None

    try:
        target = datetime.strptime(expected_date, "%Y-%m-%d")
    except ValueError:
        return None

    # ID90 prints result dates abbreviated, e.g. "Nov 23 2026". Try the full
    # month name too: they're identical for May, which is why a full-name-only
    # match worked for the tracked sailing and silently failed for every
    # other month.
    date_labels = [
        f"{target.strftime('%b')} {target.day} {target.year}",
        f"{target.strftime('%B')} {target.day} {target.year}",
        f"{target.strftime('%b')} {target.day:02d} {target.year}",
    ]

    search_url = _search_url(params, target)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_kwargs = {"storage_state": storage_state} if storage_state else {}
        context = browser.new_context(**ctx_kwargs, viewport={"width": 1400, "height": 1200})
        context.set_default_timeout(NAV_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = context.new_page()
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(3000)

            search_btn = page.get_by_role("button", name="Search")
            if search_btn.count() == 0:
                logger.warning("Advanced search page had no Search button")
                return None
            with page.expect_navigation(timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded"):
                search_btn.click()
            page.wait_for_timeout(3000)

            date_label = None
            date_anchor = None
            for cand in date_labels:
                loc = page.locator(f"text={cand}")
                if loc.count() > 0:
                    date_label, date_anchor = cand, loc
                    break
            if date_anchor is None:
                logger.warning("No result row found for any of %s", date_labels)
                return None

            row = date_anchor.first.locator(
                f"xpath=ancestor::*[self::div or self::tr or self::li]"
                f"[.//text()[contains(., '{date_label}')]][1]"
            )
            if row.count() == 0:
                logger.warning("Could not find a container for result row: %s", date_label)
                return None

            select_btn = row.locator("text=Select").first
            if select_btn.count() == 0:
                logger.warning("Result row for %s had no Select button", date_label)
                return None

            with page.expect_navigation(timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded"):
                select_btn.click()
            page.wait_for_timeout(2000)

            new_url = page.url
            if "CruiseDetails.aspx" not in new_url:
                logger.warning("Unexpected landing page after select: %s", new_url)
                return None
            return new_url
        except Exception:
            logger.exception("ID90 re-search failed for %s", expected_date)
            return None
        finally:
            context.close()
            browser.close()


# Mirrors ID90's own delQsp() list in the CruiseResultPage inline script.
_DROP_PARAMS = {
    "bdc", "bgc", "bt", "days", "iid", "lnk1", "lnk2", "months", "ports",
    "pp", "sailings", "sno", "ships", "tl", "tlc", "txt", "type",
    "vendor", "zones", "pc",
}


def details_url_from_results(
    results_url: str,
    expected_date: str,
    storage_state: str | None,
    must_contain: str | None = None,
) -> str | None:
    """Build a CruiseDetails URL for expected_date from an ID90 results page.

    ID90's own results page navigates with:
        window.location = "/cs/forms/CruiseDetails.aspx?"
                          + $.param(qso) + $(this).attr("data-parms")
    Reproducing that is far more reliable than clicking Select: it doesn't
    depend on guessing which DOM ancestor wraps the matched date, and costs
    one page load instead of two.

    Note `iid` is an *itinerary* id shared by every departure of that
    itinerary — it does not identify the sailing. The sailing is selected by
    the mon/dt date window carried over from the results URL, which is why
    that window must be preserved verbatim.
    """
    from app.scraper import fetch_page_html

    try:
        target = datetime.strptime(expected_date, "%Y-%m-%d")
    except ValueError:
        return None

    labels = (
        f"{target.strftime('%b')} {target.day:02d} {target.year}",
        f"{target.strftime('%b')} {target.day} {target.year}",
    )

    html, _title = fetch_page_html(results_url)
    soup = BeautifulSoup(html, "lxml")

    matches: list[str] = []
    for art in soup.select("article.crCruiseListing"):
        text = art.get_text(" ", strip=True)
        if not any(lbl in text for lbl in labels):
            continue
        # Required: two sailings can share a date (Nov 23 2026 has both a
        # 10-day and a 17-day Tahiti), so a date alone is ambiguous.
        if must_contain and must_contain.lower() not in text.lower():
            continue
        node = art.select_one(
            "button.zzSelectButton[data-parms], a.crListingViewDetails[data-parms]"
        )
        if node and node.get("data-parms"):
            matches.append(node["data-parms"])

    if not matches:
        logger.warning(
            "No result row on %s matching %s (%r)", results_url, expected_date, must_contain
        )
        return None
    if len(matches) > 1:
        # Fail closed rather than silently picking the wrong sailing.
        logger.error(
            "Ambiguous: %s rows match %s (%r) — refusing to guess",
            len(matches), expected_date, must_contain,
        )
        return None

    # Filter raw query pairs textually so ID90's original percent-encoding
    # (%2f, %7c) survives; re-encoding would change case/escapes.
    kept = [
        pair for pair in urlparse(results_url).query.split("&")
        if pair and pair.split("=", 1)[0].lower() not in _DROP_PARAMS
    ]
    return (
        "https://cruise.id90travel.com/cs/forms/CruiseDetails.aspx?"
        + "&".join(kept) + matches[0]
    )
