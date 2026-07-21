#!/usr/bin/env python3
"""
Open a real browser so you can log into sites that hide rates (e.g. Perx).

Usage:
  cd ~/cruise-price-tracker
  source .venv/bin/activate
  python scripts/save_browser_session.py

1. A Chromium window opens on the login page.
2. Log in normally (Perx / ID90 / etc.).
3. Optionally open the cruise page and confirm you see prices.
4. Return to the terminal and press Enter.
5. Cookies are saved to data/browser_session.json for the tracker to use.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as script from project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from app import config  # noqa: E402

DEFAULT_START = "https://perx.com/accounts/login/"


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    out = config.PLAYWRIGHT_STORAGE_STATE
    out.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Cruise Price Tracker — save browser login session")
    print("=" * 60)
    print(f"Opening: {start}")
    print(f"Will save session to: {out}")
    print()
    print("Steps:")
    print("  1. Log in in the browser window that opens")
    print("  2. Visit your cruise page and confirm rates are visible")
    print("  3. Come back here and press Enter to save")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        page.goto(start, wait_until="domcontentloaded")
        input(">>> Press Enter after you are logged in and can see rates… ")
        context.storage_state(path=str(out))
        browser.close()

    print(f"Saved session → {out}")
    print("Restart the tracker (or click Check price now) to use it.")


if __name__ == "__main__":
    main()
