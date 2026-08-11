"""Fetch cruise pages and extract prices."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Amounts: with commas (1,299.00) OR plain digits ($6216) — do not stop mid-number
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?"

# Prefer amounts that look like real cruise prices (not tiny fees)
PRICE_RE = re.compile(
    rf"(?P<currency>US\$|USD\s*\$|CAD\s*\$|€|£|\$)\s*(?P<amount>{_AMOUNT})"
    rf"|(?P<amount2>{_AMOUNT})\s*(?P<currency2>USD|CAD|EUR|GBP)",
    re.IGNORECASE,
)

# CSS selectors commonly used on cruise booking sites
COMMON_PRICE_SELECTORS = [
    # ID90 Travel / Cruise.com employee portal
    "span.price.min",
    "span.price",
    ".price.min",
    ".currencyCode",
    "[data-testid*='price']",
    "[class*='price']",
    "[class*='Price']",
    "[id*='price']",
    "[id*='Price']",
    ".fare-price",
    ".cruise-price",
    ".total-price",
    ".pricing",
    "[itemprop='price']",
    "span[class*='amount']",
    "div[class*='amount']",
]


@dataclass
class ScrapeResult:
    price: float | None
    currency: str
    raw_text: str | None
    error: str | None = None
    page_title: str | None = None


_MONTHS = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december"
)
_MONTH_NAMES = _MONTHS.split("|")
_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b(?:{_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
]


def page_confirms_date(html: str, expected_date: str) -> bool:
    """Check whether the page text mentions the expected sailing date (YYYY-MM-DD).

    Booking sites vary in how they print dates — full "May 20, 2027", an
    itinerary line like "Day 1 Thu May 20" with the year omitted, or a
    2-digit year like "May 20, '27". Try strict matches first, then a
    looser month+day match paired with either the 4-digit or 2-digit year
    appearing nearby.
    """
    try:
        target = datetime.strptime(expected_date, "%Y-%m-%d").date()
    except ValueError:
        return True  # malformed expected_date — don't block on it

    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)

    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0)
            for fmt in ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y", "%m/%d/%Y"):
                try:
                    if datetime.strptime(raw.replace(",", ""), fmt.replace(",", "")).date() == target:
                        return True
                except ValueError:
                    continue

    # Loose fallback: "<Month> <Day>" near the target's 4-digit or 2-digit year
    month_name = _MONTH_NAMES[target.month - 1]
    year_2digit = f"{target.year % 100:02d}"
    loose_patterns = [
        re.compile(rf"\b{month_name}\s+{target.day}\b", re.IGNORECASE),
        re.compile(rf"\b{target.day}\s+{month_name}\b", re.IGNORECASE),
    ]
    for pattern in loose_patterns:
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 25) : match.end() + 25]
            if str(target.year) in window or re.search(rf"['’,]\s*{year_2digit}\b", window):
                return True

    return False


def _parse_amount(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _currency_from_symbol(symbol: str) -> str:
    s = symbol.upper().replace(" ", "")
    if "CAD" in s:
        return "CAD"
    if "€" in symbol or "EUR" in s:
        return "EUR"
    if "£" in symbol or "GBP" in s:
        return "GBP"
    return "USD"


def extract_prices_from_text(text: str) -> list[tuple[float, str, str]]:
    """Return list of (amount, currency, raw_match) from free text."""
    found: list[tuple[float, str, str]] = []
    for match in PRICE_RE.finditer(text):
        groups = match.groupdict()
        amount_str = groups.get("amount") or groups.get("amount2")
        currency_raw = groups.get("currency") or groups.get("currency2") or "$"
        if not amount_str:
            continue
        amount = _parse_amount(amount_str)
        if amount is None:
            continue
        # Skip implausible cruise prices
        if amount < 50 or amount > 100_000:
            continue
        currency = _currency_from_symbol(currency_raw)
        found.append((amount, currency, match.group(0).strip()))
    return found


def pick_best_price(
    candidates: list[tuple[float, str, str]],
) -> tuple[float, str, str] | None:
    """Pick the most likely advertised cruise fare from candidates."""
    if not candidates:
        return None

    # Prefer typical cruise fares over tiny fees/taxes when possible
    preferred = [c for c in candidates if 199 <= c[0] <= 25_000]
    pool = preferred or candidates

    # Most common rounded amount wins; ties → prefer higher fare (main price, not tax)
    counts: dict[int, list[tuple[float, str, str]]] = {}
    for c in pool:
        key = round(c[0])
        counts.setdefault(key, []).append(c)

    best_key = max(
        counts.keys(),
        key=lambda k: (len(counts[k]), k),
    )
    return max(counts[best_key], key=lambda c: c[0])


def extract_with_selector(html: str, selector: str) -> tuple[float, str, str] | None:
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(selector)
    if not el:
        return None
    text = el.get_text(" ", strip=True)
    # Also check content attributes
    for attr in ("content", "data-price", "value"):
        val = el.get(attr)
        if val:
            text = f"{text} {val}"
    prices = extract_prices_from_text(text)
    if prices:
        return pick_best_price(prices)
    # Bare number
    bare = re.search(rf"({_AMOUNT})", text)
    if bare:
        amount = _parse_amount(bare.group(1))
        if amount is not None and 50 <= amount <= 100_000:
            return amount, "USD", text[:80]
    return None


def _element_price_text(el) -> str:
    """Collect text + useful attrs + nearby currency sibling (ID90 layout)."""
    parts = [el.get_text(" ", strip=True)]
    for attr in ("content", "data-price", "value"):
        val = el.get(attr)
        if val:
            parts.append(str(val))
    # Parent often holds "USD $6216" when currency is a sibling span
    parent = el.parent
    if parent is not None:
        parts.append(parent.get_text(" ", strip=True))
    prev = el.find_previous(string=True)
    if prev:
        parts.append(str(prev).strip()[:20])
    return " ".join(p for p in parts if p)


def extract_auto(html: str) -> tuple[float, str, str] | None:
    soup = BeautifulSoup(html, "lxml")

    # 0) ID90 Travel: explicit min fare element
    id90 = soup.select_one("span.price.min") or soup.select_one("span.price")
    if id90 is not None:
        text = _element_price_text(id90)
        best = pick_best_price(extract_prices_from_text(text))
        if best:
            return best
        bare = re.search(rf"({_AMOUNT})", text.replace(",", ""))
        if bare:
            amount = _parse_amount(bare.group(1))
            if amount is not None and 50 <= amount <= 100_000:
                return amount, "USD", text[:80]

    # 1) Try common selectors first
    selector_candidates: list[tuple[float, str, str]] = []
    for sel in COMMON_PRICE_SELECTORS:
        for el in soup.select(sel)[:20]:
            text = _element_price_text(el)
            selector_candidates.extend(extract_prices_from_text(text))
    best = pick_best_price(selector_candidates)
    if best:
        return best

    # 2) Full page text fallback
    body = soup.body.get_text(" ", strip=True) if soup.body else soup.get_text(" ", strip=True)
    # Limit noise
    body = body[:50_000]
    return pick_best_price(extract_prices_from_text(body))


def fetch_page_html(url: str, wait_ms: int = 4000) -> tuple[str, str | None]:
    """Load URL with Playwright and return (html, title)."""
    from app import config

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1400, "height": 900},
            "locale": "en-US",
        }
        if config.browser_session_exists():
            context_kwargs["storage_state"] = str(config.PLAYWRIGHT_STORAGE_STATE)
            logger.info("Using saved browser session: %s", config.PLAYWRIGHT_STORAGE_STATE)

        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(wait_ms)
            # Soft wait for price-looking content
            try:
                page.wait_for_function(
                    """() => {
                        const t = document.body ? document.body.innerText : '';
                        return /\\$\\s*\\d{2,}|USD\\s*\\$?\\s*\\d{2,}|from\\s*\\$?\\s*\\d{3,}/i.test(t)
                            && !/log in for rates/i.test(t);
                    }""",
                    timeout=10_000,
                )
            except PlaywrightTimeout:
                pass
            # Extra wait on known gated sites after session load
            if "perx.com" in url.lower():
                page.wait_for_timeout(2500)
            html = page.content()
            title = page.title()
            return html, title
        finally:
            context.close()
            browser.close()


def _login_gated_message(html: str, url: str) -> str | None:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True).lower()
    if "log in for rates" in text or "login for rates" in text:
        from app import config

        if not config.browser_session_exists():
            return (
                "This site shows 'Log in for rates' (Perx). "
                "Save a login session: python scripts/save_browser_session.py "
                f"(writes {config.PLAYWRIGHT_STORAGE_STATE.name}), then re-check."
            )
        return (
            "Still seeing 'Log in for rates' with the saved session. "
            "Re-run python scripts/save_browser_session.py and log in again, then re-check."
        )
    if "perx.com" in url.lower() and not re.search(r"\$\s*\d{3,}", text):
        from app import config

        if not config.browser_session_exists():
            return (
                "No public price on Perx without login. "
                "Run: python scripts/save_browser_session.py"
            )
    # VacationsToGo bounces signed-out visitors to login.cfm, which renders
    # only site navigation — no ship name and no price. Report it as a login
    # wall so the caller refreshes the session and retries.
    if "vacationstogo.com" in url.lower() and "star breeze" not in text:
        return (
            "VacationsToGo redirected to its members page without login — "
            "session expired or VTG_EMAIL is not set."
        )
    return None


def scrape_cruise(
    url: str, css_selector: str | None = None, expected_date: str | None = None
) -> ScrapeResult:
    """Scrape a cruise booking URL and return the detected price."""
    try:
        # Perx rates often need a longer settle time after login
        wait_ms = 7000 if "perx.com" in url.lower() else 4000
        html, title = fetch_page_html(url, wait_ms=wait_ms)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load %s", url)
        return ScrapeResult(
            price=None,
            currency="USD",
            raw_text=None,
            error=f"Failed to load page: {exc}",
            page_title=None,
        )

    if expected_date and not page_confirms_date(html, expected_date):
        logger.warning(
            "Date mismatch on %s: page does not confirm expected sailing %s",
            url,
            expected_date,
        )
        return ScrapeResult(
            price=None,
            currency="USD",
            raw_text=None,
            error=(
                f"Departure date mismatch: page did not confirm expected sailing "
                f"{expected_date}. The booking URL may have drifted to a different "
                "sailing — re-check and update the tracked URL."
            ),
            page_title=title,
        )

    try:
        result: tuple[float, str, str] | None = None
        if css_selector:
            result = extract_with_selector(html, css_selector)
            if result is None:
                # Fall back to auto if selector fails
                result = extract_auto(html)
                if result is None:
                    gated = _login_gated_message(html, url)
                    return ScrapeResult(
                        price=None,
                        currency="USD",
                        raw_text=None,
                        error=gated
                        or f"CSS selector '{css_selector}' found no price (auto also failed)",
                        page_title=title,
                    )
        else:
            result = extract_auto(html)

        if result is None:
            gated = _login_gated_message(html, url)
            return ScrapeResult(
                price=None,
                currency="USD",
                raw_text=None,
                error=gated or "Could not detect a price on the page. Try adding a CSS selector.",
                page_title=title,
            )

        price, currency, raw = result
        return ScrapeResult(
            price=price,
            currency=currency,
            raw_text=raw,
            error=None,
            page_title=title,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to parse price from %s", url)
        return ScrapeResult(
            price=None,
            currency="USD",
            raw_text=None,
            error=f"Parse error: {exc}",
            page_title=title,
        )
