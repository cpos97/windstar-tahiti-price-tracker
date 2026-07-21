#!/usr/bin/env python3
"""
Call the secured /api/check-prices endpoint (for Render cron / shell wrappers).

  CHECK_URL=https://your-app.onrender.com/api/check-prices \
  CRON_SECRET=... \
  python scripts/cron_ping.py
"""

from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    base = os.environ.get("CHECK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    secret = os.environ.get("CRON_SECRET", "")
    if not base:
        print("CHECK_URL is required", file=sys.stderr)
        return 1
    if not secret:
        print("CRON_SECRET is required", file=sys.stderr)
        return 1

    url = base.rstrip("/")
    if not url.endswith("/api/check-prices"):
        url = f"{url}/api/check-prices"

    print(f"POST {url}")
    resp = httpx.get(
        url,
        headers={"X-Cron-Secret": secret},
        timeout=300.0,
    )
    print(resp.status_code, resp.text[:800])
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
