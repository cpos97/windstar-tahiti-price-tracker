"""Check remaining cabin counts for specific Ocean View Suite categories on ID90.

This walks ID90's live booking flow (category list -> per-category cabin
picker) since remaining-inventory counts aren't shown on the summary page —
only after clicking into each category. It's slow (~20-30s per category,
real navigations against the vendor's live availability system) so it's
meant to run at most a few times a day, not on every price check.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

TARGET_CATEGORIES = ["S", "S1", "SS1", "S2", "S3"]


def _wait_for_title(page, contains: str, timeout_s: int = 45) -> bool:
    for _ in range(timeout_s // 2):
        page.wait_for_timeout(2000)
        if contains in (page.title() or ""):
            return True
    return False


def _selected_row_matches(page, expected_date: str) -> bool:
    """Is the highlighted sailing row actually the sailing we want?

    A CruiseDetails page lists every departure of the itinerary, so merely
    finding the date somewhere on the page proves nothing — scraper.
    page_confirms_date() returns True for the Nov page when asked about the
    May sailing and vice versa. What decides which sailing's cabins we read
    is `div.row.selected`, the row whose Select button we click, so that is
    the only row worth asserting on.
    """
    try:
        target = datetime.strptime(expected_date, "%Y-%m-%d")
    except ValueError:
        return True  # malformed — don't block on it

    try:
        text = page.locator("div.row.selected").first.inner_text()
    except Exception:  # noqa: BLE001
        return False

    abbr = target.strftime("%b")
    # e.g. "Mon, Nov 23, 26" / "Thu, May 20, 27"
    patterns = [
        rf"{abbr}\s+{target.day:02d},?\s*'?{target.year % 100:02d}\b",
        rf"{abbr}\s+{target.day},?\s*'?{target.year % 100:02d}\b",
        rf"{abbr}\s+{target.day:02d},?\s*{target.year}\b",
        rf"{abbr}\s+{target.day},?\s*{target.year}\b",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _go_to_category_page(page, url: str, expected_date: str | None = None) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(4000)
    if expected_date and not _selected_row_matches(page, expected_date):
        try:
            got = page.locator("div.row.selected").first.inner_text()[:80]
        except Exception:  # noqa: BLE001
            got = "<no selected row>"
        raise RuntimeError(
            f"Selected sailing is not {expected_date} (row reads: {got!r})"
        )
    page.locator("div.row.selected button.zzSelectButton").first.click()
    _wait_for_title(page, "Category Availability")
    page.wait_for_timeout(1500)


# Playwright defaults to 30s, which is too tight on a small single-core cloud
# VM — ID90's pages are slow and this flow does ~11 navigations. Observed a
# roughly 50% failure rate at the default before raising this.
DEFAULT_TIMEOUT_MS = 90_000


def check_categories(
    url: str,
    storage_state: str | None,
    codes: list[str] = TARGET_CATEGORIES,
    expected_date: str | None = None,
) -> list[dict]:
    """Return [{code, name, available}] for the requested category codes."""
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_kwargs = {"storage_state": storage_state} if storage_state else {}
        context = browser.new_context(**ctx_kwargs, viewport={"width": 1400, "height": 1400})
        context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        context.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
        page = context.new_page()
        try:
            _go_to_category_page(page, url, expected_date)

            # Category codes appear in the same top-to-bottom order as the
            # "Select" buttons, so pair them up positionally.
            listing_text = page.inner_text("body")
            codes_in_order = re.findall(r"Category:\s*\n?\s*([A-Z0-9]+)", listing_text)
            index_by_code: dict[str, int] = {
                code: i for i, code in enumerate(codes_in_order)
            }

            for code in codes:
                idx = index_by_code.get(code)
                if idx is None:
                    # Genuinely not offered anymore — i.e. sold out
                    logger.warning("Category %s not found on category list", code)
                    results.append(
                        {"code": code, "name": code, "available": None, "status": "absent"}
                    )
                    continue

                # Retry once — a single slow page shouldn't lose the whole run
                text = None
                for attempt in (1, 2):
                    try:
                        cards = page.locator("button:has-text('Select')")
                        cards.nth(idx).click()
                        _wait_for_title(page, "Cabin Selection")
                        page.wait_for_timeout(1500)
                        text = page.inner_text("body")
                        break
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Category %s attempt %s failed: %s", code, attempt, str(exc)[:120]
                        )
                        if attempt == 2:
                            break
                        _go_to_category_page(page, url, expected_date)

                if text is None:
                    # Couldn't read the page — unknown, NOT sold out
                    results.append(
                        {"code": code, "name": code, "available": None, "status": "error"}
                    )
                    if code != codes[-1]:
                        _go_to_category_page(page, url, expected_date)
                    continue

                m_cat = re.search(r"Category:\s*\n?(.+?)\n", text)
                m_avail = re.search(r"(\d+)\s+Cabins?\s+available", text, re.IGNORECASE)
                name = m_cat.group(1).strip() if m_cat else code
                # Strip the trailing "<nbsp>CODE" the page appends to the name
                name = re.sub(rf"[\s\xa0]*{re.escape(code)}$", "", name).strip()
                if m_avail:
                    available = int(m_avail.group(1))
                elif re.search(r"sold out|no cabins|not available", text, re.IGNORECASE):
                    available = 0
                else:
                    available = None

                results.append(
                    {
                        "code": code,
                        "name": name,
                        "available": available,
                        "status": "ok" if available is not None else "error",
                    }
                )

                # Fresh reload for the next category — most reliable against
                # this ASP.NET postback flow (in-page "Back" isn't always clickable)
                if code != codes[-1]:
                    _go_to_category_page(page, url, expected_date)
        finally:
            context.close()
            browser.close()

    return results
