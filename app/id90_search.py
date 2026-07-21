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

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


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

    # Matches how ID90 prints the date on results rows, e.g. "May 20 2027"
    date_label = f"{target.strftime('%B')} {target.day} {target.year}"

    search_url = _search_url(params, target)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_kwargs = {"storage_state": storage_state} if storage_state else {}
        context = browser.new_context(**ctx_kwargs, viewport={"width": 1400, "height": 1200})
        page = context.new_page()
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3000)

            search_btn = page.get_by_role("button", name="Search")
            if search_btn.count() == 0:
                logger.warning("Advanced search page had no Search button")
                return None
            with page.expect_navigation(timeout=20_000, wait_until="domcontentloaded"):
                search_btn.click()
            page.wait_for_timeout(3000)

            date_anchor = page.locator(f"text={date_label}")
            if date_anchor.count() == 0:
                logger.warning("No result row found for %s", date_label)
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

            with page.expect_navigation(timeout=20_000, wait_until="domcontentloaded"):
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
