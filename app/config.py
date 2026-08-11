"""Application configuration loaded from environment / .env file only.

Never hardcode API keys, passwords, or private URLs in source.
Set secrets in local `.env` (gitignored) or GitHub Actions Secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = f"sqlite:///{DATA_DIR / 'tracker.db'}"

# Email: "resend" (recommended) or "smtp"
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "resend").strip().lower()

# Resend — from env / GitHub secret RESEND_API_KEY only
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv(
    "EMAIL_FROM",
    "Windstar Tahiti Price Tracker <onboarding@resend.dev>",
)

# --- Email sandbox (Resend free-tier / testing) ---
# When true, ALL outgoing mail is redirected to SANDBOX_INBOX with a banner
# showing the original To/CC. Avoids Resend 403 for unverified recipients.
#
# Enable via any of:
#   EMAIL_SANDBOX=1
#   NODE_ENV=development   (Node-style; supported for convenience)
#   ENV=development
# Or set FORCE_EMAIL_SANDBOX = True below for a hard code toggle.
FORCE_EMAIL_SANDBOX = False  # manual override — set True to force sandbox always

_sandbox_env = os.getenv("EMAIL_SANDBOX", "").strip().lower()
_node_env = os.getenv("NODE_ENV", "").strip().lower()
_env = os.getenv("ENV", "").strip().lower()
EMAIL_SANDBOX = FORCE_EMAIL_SANDBOX or _sandbox_env in {
    "1",
    "true",
    "yes",
    "on",
} or _node_env in {"development", "dev"} or _env in {"development", "dev"}

# Where sandbox-mode mail is delivered (must be a Resend-verified address).
# Falls back to ALERT_EMAIL if unset, since that's already the owner's inbox.
SANDBOX_INBOX = os.getenv("SANDBOX_INBOX", "").strip() or os.getenv("ALERT_EMAIL", "").strip()

DEPARTURE_DATE = os.getenv("DEPARTURE_DATE", "2027-05-20")

# SMTP fallback (all from env)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# Legacy single recipient (still supported)
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "").strip()

# Multi personalized recipients (email:FirstName,email:FirstName,...)
# If unset, defaults to the family list for this trip.
ALERT_RECIPIENTS_RAW = os.getenv("ALERT_RECIPIENTS", "").strip()

CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))

CRON_SECRET = os.getenv("CRON_SECRET", "").strip()

# Shared password protecting the whole site (HTTP Basic Auth) — the app is
# reachable from the public internet via the tunnel, so this stops random
# visitors from viewing family data, deleting cruises, or sending email
# through the /family/intro-email form. Username is fixed ("family");
# only the password needs to be shared with people who should have access.
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "").strip()

DISABLE_LOCAL_SCHEDULER = os.getenv("DISABLE_LOCAL_SCHEDULER", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

# Public/dashboard link used in emails (set DASHBOARD_URL in env when you have a live URL)
DASHBOARD_URL = os.getenv(
    "DASHBOARD_URL",
    f"http://{os.getenv('HOST', '127.0.0.1')}:{os.getenv('PORT', '8765')}/",
)

# CC for family intro email — falls back to ALERT_EMAIL (the owner's inbox) if unset
INTRO_CC_EMAIL = os.getenv("INTRO_CC_EMAIL", "").strip() or os.getenv("ALERT_EMAIL", "").strip()

_storage_env = os.getenv("PLAYWRIGHT_STORAGE_STATE", str(DATA_DIR / "browser_session.json"))
PLAYWRIGHT_STORAGE_STATE = Path(_storage_env)

# Site logins — env / GitHub secrets only
PERX_USERNAME = os.getenv("PERX_USERNAME", "")
PERX_PASSWORD = os.getenv("PERX_PASSWORD", "")
ID90_EMAIL = os.getenv("ID90_EMAIL", "")
ID90_PASSWORD = os.getenv("ID90_PASSWORD", "")

# VacationsToGo gates FastDeal pricing behind a members page, but signing in
# needs an email address only — the site has no password field.
VTG_EMAIL = os.getenv("VTG_EMAIL", "").strip()

# Optional override for default booking URLs used only by seed scripts (not secrets, but configurable)
ID90_CRUISE_URL = os.getenv("ID90_CRUISE_URL", "").strip()
PERX_CRUISE_URL = os.getenv("PERX_CRUISE_URL", "").strip()


def alert_recipients() -> list[dict[str, str]]:
    """
    Return [{email, name, greeting}, ...] for price-drop personalization.
    greeting is e.g. "Hi Cameron!"
    """
    if ALERT_RECIPIENTS_RAW:
        out: list[dict[str, str]] = []
        for part in ALERT_RECIPIENTS_RAW.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                email, name = part.split(":", 1)
            else:
                email, name = part, part.split("@")[0].title()
            email = email.strip()
            name = name.strip()
            if email:
                out.append(
                    {
                        "email": email,
                        "name": name,
                        "greeting": f"Hi {name}!",
                    }
                )
        if out:
            return out

    # No ALERT_RECIPIENTS configured — fall back to the single legacy
    # ALERT_EMAIL recipient, if set. Set ALERT_RECIPIENTS in .env /
    # GitHub Secrets to notify multiple people (see .env.example).
    if ALERT_EMAIL:
        return [
            {
                "email": ALERT_EMAIL,
                "name": ALERT_EMAIL.split("@")[0].title(),
                "greeting": "Hi!",
            }
        ]
    return []


def email_configured() -> bool:
    recipients = alert_recipients()
    if not recipients and not ALERT_EMAIL:
        return False
    smtp_ok = bool(
        SMTP_USER
        and SMTP_PASSWORD
        and SMTP_PASSWORD not in {"", "your-app-password"}
    )
    resend_ok = bool(RESEND_API_KEY and RESEND_API_KEY != "re_xxxxxxxx")
    if EMAIL_PROVIDER == "smtp":
        return smtp_ok
    if EMAIL_PROVIDER == "resend":
        # Resend alone OR Gmail fallback ready for non-owner recipients
        return resend_ok or smtp_ok
    return smtp_ok or resend_ok


def email_provider_label() -> str:
    if EMAIL_PROVIDER == "resend":
        return "Resend (auto From address)"
    if EMAIL_PROVIDER == "smtp":
        return f"SMTP ({SMTP_HOST})"
    return EMAIL_PROVIDER


def browser_session_exists() -> bool:
    return PLAYWRIGHT_STORAGE_STATE.is_file()


def cron_secret_configured() -> bool:
    return bool(CRON_SECRET)


def verify_cron_secret(provided: str | None) -> bool:
    if not CRON_SECRET:
        return False
    expected = CRON_SECRET
    got = (provided or "").strip()
    if len(expected) != len(got):
        return False
    result = 0
    for a, b in zip(expected.encode(), got.encode()):
        result |= a ^ b
    return result == 0
