"""Work out which cabin category a tracked price is for.

ID90's Category Availability page lists every category for the sailing with
its own price (SP Star Porthole Suite $3,966, S1 Ocean View Suite 1 $6,616,
...). That table is the source of truth. Perx and VacationsToGo only print
broad labels ("Oceanview", "Our Suite"), so their prices are named by
matching them against the ID90 table for the same sailing, falling back to
the site's own label when no ID90 category has that exact price.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

# A label line must look like a cabin type, not "All Rates" or a heading
_CABIN_WORDS = re.compile(
    r"suite|ocean\s*view|oceanview|balcony|inside|interior|porthole|veranda|cabin|stateroom",
    re.IGNORECASE,
)
_ID90_BLOCK = re.compile(r"Category:\s*([A-Z0-9]+)\s*\n(.*?)(?=Category:|\Z)", re.DOTALL)
_ID90_PRICE = re.compile(r"USD\s*\$\s*([\d,]+(?:\.\d{2})?)")


def parse_id90_category_prices(text: str) -> list[dict]:
    """[{code, name, price}] from the inner text of ID90's Category Availability page."""
    out: list[dict] = []
    for code, block in _ID90_BLOCK.findall(text):
        m_price = _ID90_PRICE.search(block)
        if not m_price:
            continue
        # The display name is the line right after the "Select" button
        m_name = re.search(r"\nSelect\s*\n\s*([^\n]+)", "\n" + block)
        name = m_name.group(1).strip() if m_name else code
        out.append(
            {"code": code, "name": name, "price": float(m_price.group(1).replace(",", ""))}
        )
    return out


def _price_variants(price: float) -> list[str]:
    whole = int(round(price))
    return [f"{whole:,}", str(whole)]


def label_near_price(html: str, price: float) -> str | None:
    """The page's own cabin label for this price, e.g. "Oceanview" or "Suite"."""
    lines = BeautifulSoup(html, "lxml").get_text("\n", strip=True).split("\n")
    variants = _price_variants(price)
    for i, line in enumerate(lines):
        pos = min((line.find(v) for v in variants if v in line), default=-1)
        if pos < 0:
            continue
        # Same line first ("Oceanview $6,216"), then the line above ("Our Suite" / "$3,966")
        candidates = [line[:pos], lines[i - 1] if i else ""]
        for cand in candidates:
            cand = re.sub(r"(USD)?\s*\$\s*$", "", cand).strip(" :\t")
            if cand and len(cand) <= 40 and _CABIN_WORDS.search(cand):
                return re.sub(r"^Our\s+", "", cand).strip()
    return None


def load_table(raw: str | None) -> list[dict]:
    try:
        table = json.loads(raw) if raw else []
    except ValueError:
        return []
    return table if isinstance(table, list) else []


# How to read a site's own broad label when no ID90 category has the exact
# price. VacationsToGo's plain "Suite" fare is its Ocean View Suite.
SITE_LABEL_ALIASES = {
    "vacationstogo.com": {"suite": "Ocean View Suite"},
}


def resolve(
    price: float, site_label: str | None, id90_table: list[dict], url: str = ""
) -> str | None:
    """Best name for the cabin behind `price`."""
    for row in sorted(id90_table, key=lambda r: r.get("price") or 0):
        if abs((row.get("price") or 0) - price) < 1:
            return f"{row['name']} ({row['code']})"
    if site_label:
        for domain, aliases in SITE_LABEL_ALIASES.items():
            if domain in url.lower():
                return aliases.get(site_label.lower(), site_label)
    return site_label
