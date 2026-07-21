#!/usr/bin/env python3
"""
Log into Perx + ID90 and save cookies for the price tracker.

Option A — credentials in .env (like Resend API key):
  PERX_USERNAME=...
  PERX_PASSWORD=...
  ID90_EMAIL=...
  ID90_PASSWORD=...
  python scripts/login_sites.py

Option B — interactive browser (you type passwords in the window):
  python scripts/login_sites.py --interactive
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.auth_sites import interactive_login_both, save_session_with_credentials  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Open a browser and log in manually",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show browser even when using .env credentials",
    )
    args = parser.parse_args()

    if args.interactive:
        print("Opening browser tabs for Perx + ID90…")
        status = interactive_login_both()
        print(status)
        return 0 if status.get("saved") else 1

    has_perx = bool(config.PERX_USERNAME and config.PERX_PASSWORD)
    has_id90 = bool(config.ID90_EMAIL and config.ID90_PASSWORD)
    if not has_perx and not has_id90:
        print("No site credentials in .env.")
        print("Add PERX_USERNAME / PERX_PASSWORD and/or ID90_EMAIL / ID90_PASSWORD")
        print("Or run: python scripts/login_sites.py --interactive")
        return 1

    print("Logging in with credentials from .env…")
    results = save_session_with_credentials(headless=not args.headed)
    print(results)
    if results.get("perx"):
        print("Perx:", results["perx"])
    if results.get("id90"):
        print("ID90:", results["id90"])
    print("Session saved:", results.get("saved"), "→", results.get("session_path"))
    ok = any(
        (results.get(k) or {}).get("ok")
        for k in ("perx", "id90")
    )
    return 0 if ok and results.get("saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
