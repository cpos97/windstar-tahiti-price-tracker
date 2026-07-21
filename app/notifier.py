"""Local Mac notifications (no account required) + optional email."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app import config
from app.emailer import send_price_drop_email, send_test_email as send_test_email_msg

logger = logging.getLogger(__name__)


def mac_notify(title: str, message: str) -> tuple[bool, str]:
    """Show a macOS Notification Center banner."""
    # Escape for AppleScript string — backslash/quote for string literal safety,
    # strip newlines since AppleScript string literals can't contain raw ones
    # (cruise names/messages could otherwise originate from a scraped page)
    def _escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")

    safe_title = _escape(title)
    safe_msg = _escape(message)
    script = f'display notification "{safe_msg}" with title "{safe_title}" sound name "Glass"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return True, "Mac notification shown"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mac notification failed: %s", exc)
        return False, str(exc)


def notify_price_drop(
    cruise_name: str,
    cruise_url: str,
    old_price: float,
    new_price: float,
    currency: str = "USD",
) -> dict:
    """
    Always try Mac notification.
    Also email if configured.
    """
    drop = old_price - new_price
    title = "Cruise price drop"
    short = (
        f"{cruise_name}: {currency} {old_price:,.0f} → {currency} {new_price:,.0f} "
        f"(save {currency} {drop:,.0f})"
    )

    mac_ok, mac_msg = mac_notify(title, short[:180])

    # Append to local alert log file (always)
    log_path = config.DATA_DIR / "price_drop_alerts.log"
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(
                f"{cruise_name} | {old_price} -> {new_price} {currency} | {cruise_url}\n"
            )
    except Exception:  # noqa: BLE001
        pass

    email_ok, email_msg = False, "Email not configured"
    if config.email_configured():
        email_ok, email_msg = send_price_drop_email(
            cruise_name=cruise_name,
            cruise_url=cruise_url,
            old_price=old_price,
            new_price=new_price,
            currency=currency,
        )
    else:
        email_msg = (
            "Email not set up yet (needs free Resend API key). "
            "Mac notification + local log still fired."
        )

    overall_ok = mac_ok or email_ok
    combined = f"Mac: {mac_msg}; Email: {email_msg}"
    return {
        "ok": overall_ok,
        "message": combined,
        "mac_ok": mac_ok,
        "email_ok": email_ok,
    }


def send_test_alert() -> tuple[bool, str]:
    mac_ok, mac_msg = mac_notify(
        "Cruise Price Tracker",
        "Test alert — price-drop notifications work on this Mac.",
    )
    parts = [f"Mac: {mac_msg}"]
    if config.email_configured():
        email_ok, email_msg = send_test_email_msg()
        parts.append(f"Email: {email_msg}")
        return mac_ok or email_ok, " | ".join(parts)
    parts.append(
        "Email: not configured — add RESEND_API_KEY to .env "
        "(free at resend.com). Alerts still show on your Mac."
    )
    return mac_ok, " | ".join(parts)
