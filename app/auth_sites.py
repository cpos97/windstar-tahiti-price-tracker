"""Log into Perx / ID90 and save a shared Playwright browser session."""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app import config

logger = logging.getLogger(__name__)

# Perx retired /accounts/login/ (now a 404); login is a modal on the homepage.
PERX_LOGIN = "https://perx.com/"
# Fallback only — the tracked cruise's own URL is preferred (see _tracked_url),
# because Perx renumbers itineraries and a hard-coded one goes stale.
PERX_CRUISE = (
    "https://perx.com/cruises/windstar-cruises/star-breeze/"
    "itineraries/233619/sailings/2027-05-20/"
)
# VacationsToGo gates FastDeal pricing behind a members-only page, but the
# "already a member" form asks for an email address only — there is no
# password field at all.
VTG_LOGIN = "https://www.vacationstogo.com/login.cfm"
VTG_DEAL = "https://www.vacationstogo.com/fastdeal.cfm?deal=27296"

ID90_LOGIN = "https://www.id90travel.com/login"
ID90_CRUISE = (
    "https://cruise.id90travel.com/cs/forms/CruiseDetails.aspx"
    "?skin=636&did=-1&mon=5%2F1%2F2027&vid=664&pid=9476"
    "&pin=W8-1386879-1401&iid=4216556&sno=1"
)


def _tracked_url(domain: str, fallback: str) -> str:
    """Current URL of the tracked (non-benchmark) cruise on this site.

    Login checks load a real sailing page to prove rates are visible. Using
    the URL the tracker already keeps up to date means a vendor renumbering
    its pages can't make every login look like a failure.
    """
    try:
        from app.database import SessionLocal
        from app.models import Cruise

        db = SessionLocal()
        try:
            for c in db.query(Cruise).filter(Cruise.active.is_(True)).all():
                if c.is_benchmark:
                    continue
                if domain in (c.url or "").lower():
                    return c.url
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.warning("Could not look up tracked %s URL; using fallback", domain)
    return fallback


def _dismiss_cookies(page: Page) -> None:
    for label in (
        "Accept All Cookies",
        "Accept All",
        "Allow All",
        "Accept",
        "I Agree",
        "Got it",
    ):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() and btn.first.is_visible(timeout=500):
                btn.first.click(timeout=2000)
                page.wait_for_timeout(500)
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            loc = page.locator(f"text={label}").first
            if loc.is_visible(timeout=400):
                loc.click(timeout=2000)
                page.wait_for_timeout(500)
                return
        except Exception:  # noqa: BLE001
            pass


def perx_looks_logged_in(page: Page) -> bool:
    url = page.url.lower()
    if "/accounts/login" in url:
        return False
    text = ""
    try:
        text = page.inner_text("body").lower()
    except Exception:  # noqa: BLE001
        return False
    if "log out" in text or "logout" in text or "my account" in text:
        return True
    if "log in for rates" in text:
        return False
    # After login, username field usually gone from nav
    return "sign out" in text


def id90_looks_logged_in(page: Page) -> bool:
    url = page.url.lower()
    if "/login" in url or "/up-auth/" in url:
        return False
    # A successful login redirects to the members' search pages
    if "id90travel.com/search" in url:
        return True
    try:
        text = page.inner_text("body").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(
        x in text
        for x in ("log out", "logout", "sign out", "my trips", "my account", "dashboard")
    )


def vtg_looks_logged_in(page: Page) -> bool:
    """FastDeal content only renders for signed-in members."""
    if "login.cfm" in page.url.lower():
        return False
    try:
        text = page.inner_text("body")
    except Exception:  # noqa: BLE001
        return False
    # The deal page shows the ship + a price once the session is valid
    return "Star Breeze" in text and bool(re.search(r"\$[0-9][0-9,]{2,7}", text))


def login_vtg(page: Page, email: str) -> tuple[bool, str]:
    """Sign in to VacationsToGo. Email only — the site has no password field."""
    try:
        page.goto(VTG_DEAL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1500)
        _dismiss_cookies(page)

        if vtg_looks_logged_in(page):
            return True, "VacationsToGo already signed in"

        # Redirected to the members page — fill the "already a member" form
        box = page.locator("input[name='LogEmail']").first
        box.wait_for(state="visible", timeout=15_000)
        box.fill(email)
        box.press("Enter")
        page.wait_for_timeout(4000)

        if not vtg_looks_logged_in(page):
            page.goto(VTG_DEAL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2500)

        if vtg_looks_logged_in(page):
            return True, "VacationsToGo FastDeal page loaded (session saved)"
        return False, f"VacationsToGo sign-in did not take (at {page.url})"
    except Exception as exc:  # noqa: BLE001
        return False, f"VacationsToGo login error: {exc}"


def login_perx(page: Page, username: str, password: str) -> tuple[bool, str]:
    page.goto(PERX_LOGIN, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2500)
    _dismiss_cookies(page)
    page.wait_for_timeout(800)

    # Open the login modal from the header
    try:
        page.locator(
            "a[data-target='#login-modal']:visible, a[href='#login-modal']:visible"
        ).first.click(timeout=10_000)
        page.wait_for_timeout(1500)
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not open the Perx login window: {exc}"

    modal = page.locator("#login-modal")
    try:
        user = modal.locator("input[name='username']").first
        user.wait_for(state="visible", timeout=12_000)
        user.fill(username)
        modal.locator("input[name='password']").first.fill(password)
        remember = modal.locator("input[name='remember_me']").first
        if remember.count() and not remember.is_checked():
            remember.check(force=True)
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not fill Perx login form: {exc}"

    try:
        modal.locator(
            "button[type='submit'], input[type='submit'], button:has-text('Log In')"
        ).first.click(timeout=5_000)
    except Exception:  # noqa: BLE001
        modal.locator("input[name='password']").first.press("Enter")

    for _ in range(15):
        page.wait_for_timeout(1000)
        try:
            body_l = page.inner_text("body").lower()
        except Exception:  # noqa: BLE001
            continue
        if any(x in body_l for x in ("please enter a correct", "invalid username", "incorrect")):
            return False, "Perx rejected the username/password — update them in Settings"
        if "log out" in body_l:
            break

    # Prove rates are visible on the sailing we actually track
    page.goto(
        _tracked_url("perx.com", PERX_CRUISE), wait_until="domcontentloaded", timeout=45_000
    )
    page.wait_for_timeout(5000)
    _dismiss_cookies(page)
    body = page.inner_text("body")
    if "log in for rates" in body.lower():
        return False, "Perx still shows 'Log in for rates' — check username/password in Settings"
    if re.search(r"\$\s*\d{3,}", body):
        return True, "Perx login OK (rates visible)"
    if perx_looks_logged_in(page):
        return True, "Perx login OK"
    return False, "Perx login could not be confirmed"


def login_id90(page: Page, email: str, password: str) -> tuple[bool, str]:
    """ID90's two-step login: email -> Next -> password -> Next."""
    try:
        page.goto(ID90_LOGIN, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(3000)
        _dismiss_cookies(page)
        page.wait_for_timeout(800)

        box = page.locator("input[type='email']:visible, input[name='email']:visible").first
        box.wait_for(state="visible", timeout=15_000)
        box.fill(email)
        page.get_by_role("button", name="Next").first.click()

        pwd = page.locator("input[type='password']:visible").first
        pwd.wait_for(state="visible", timeout=20_000)
        pwd.fill(password)
        page.get_by_role("button", name="Next").first.click()
    except Exception as exc:  # noqa: BLE001
        return False, f"ID90 login form step failed: {exc}"

    for _ in range(25):
        page.wait_for_timeout(1000)
        if id90_looks_logged_in(page):
            break
        url = page.url.lower()
        if "company-selection" in url:
            return False, "ID90 could not locate the account from this email"
        try:
            body_l = page.inner_text("body").lower()
        except Exception:  # noqa: BLE001
            continue
        if "enter-password" in url and any(
            x in body_l for x in ("incorrect", "invalid", "wrong password", "try again")
        ):
            return False, "ID90 rejected the password — update it in Settings"
    else:
        return False, f"ID90 login did not complete (stuck at {page.url[:80]})"

    # Prove the tracked sailing page loads with a rate
    try:
        page.goto(
            _tracked_url("id90travel", ID90_CRUISE),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout(4000)
        if re.search(r"USD\s*\$?\s*\d{3,}|\$\s*\d{3,}", page.inner_text("body")):
            return True, "ID90 login OK (rates visible)"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ID90 cruise page check failed after login: %s", exc)
    return True, "ID90 login OK"


def save_session_with_credentials(
    perx_user: str | None = None,
    perx_pass: str | None = None,
    id90_email: str | None = None,
    id90_pass: str | None = None,
    vtg_email: str | None = None,
    headless: bool = True,
) -> dict:
    """
    Log into available sites using credentials and write browser_session.json.
    """
    perx_user = perx_user or config.PERX_USERNAME
    perx_pass = perx_pass or config.PERX_PASSWORD
    id90_email = id90_email or config.ID90_EMAIL
    id90_pass = id90_pass or config.ID90_PASSWORD
    vtg_email = vtg_email or config.VTG_EMAIL

    out = config.PLAYWRIGHT_STORAGE_STATE
    out.parent.mkdir(parents=True, exist_ok=True)
    results: dict = {"session_path": str(out), "perx": None, "id90": None, "vtg": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        if perx_user and perx_pass:
            ok, msg = login_perx(page, perx_user, perx_pass)
            results["perx"] = {"ok": ok, "message": msg}
            logger.info("Perx login: %s — %s", ok, msg)
        else:
            results["perx"] = {
                "ok": False,
                "message": "No PERX_USERNAME / PERX_PASSWORD in .env",
            }

        if id90_email and id90_pass:
            ok, msg = login_id90(page, id90_email, id90_pass)
            results["id90"] = {"ok": ok, "message": msg}
            logger.info("ID90 login: %s — %s", ok, msg)
        else:
            results["id90"] = {
                "ok": False,
                "message": "No ID90_EMAIL / ID90_PASSWORD in .env",
            }

        if vtg_email:
            ok, msg = login_vtg(page, vtg_email)
            results["vtg"] = {"ok": ok, "message": msg}
            logger.info("VacationsToGo login: %s — %s", ok, msg)
        else:
            results["vtg"] = {"ok": False, "message": "No VTG_EMAIL in .env"}

        # Only persist when a login actually succeeded. This function used to
        # write unconditionally, which meant a failed login (expired password,
        # site outage, a CAPTCHA) would overwrite a perfectly good session
        # with a logged-out one and silently break all scraping.
        attempted = [r for r in (results["perx"], results["id90"], results["vtg"]) if r]
        any_ok = any(r.get("ok") for r in attempted)
        if any_ok:
            if out.is_file():
                try:
                    shutil.copy2(out, out.with_name(out.name + ".bak"))
                except OSError:  # noqa: PERF203
                    logger.warning("Could not back up existing session file")
            context.storage_state(path=str(out))
        else:
            logger.error(
                "No site login succeeded — keeping the existing session file untouched"
            )
        browser.close()

    results["any_ok"] = any_ok
    results["saved"] = bool(any_ok and out.is_file())
    return results


def interactive_login_both(timeout_seconds: int = 600) -> dict:
    """
    Open a visible browser; user logs into Perx then ID90.
    Auto-saves when both look logged in, or after timeout if at least one is.
    """
    out = config.PLAYWRIGHT_STORAGE_STATE
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    status = {"perx": False, "id90": False, "saved": False, "path": str(out)}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        perx_page = context.new_page()
        perx_page.goto(PERX_LOGIN, wait_until="domcontentloaded")
        _dismiss_cookies(perx_page)

        id90_page = context.new_page()
        id90_page.goto(ID90_LOGIN, wait_until="domcontentloaded")
        _dismiss_cookies(id90_page)

        print("=" * 60)
        print("Log into BOTH browser tabs:")
        print("  1) Perx  — log in until you leave the login page")
        print("  2) ID90  — complete airline login")
        print("Then open the Perx cruise tab if needed so rates show.")
        print("This window auto-saves when it detects login (or after timeout).")
        print("=" * 60)

        while time.time() - started < timeout_seconds:
            try:
                if not status["perx"] and perx_looks_logged_in(perx_page):
                    status["perx"] = True
                    print("✓ Perx login detected")
                    # Open cruise page to confirm rates
                    perx_page.goto(PERX_CRUISE, wait_until="domcontentloaded")
                    perx_page.wait_for_timeout(2000)
            except Exception:  # noqa: BLE001
                pass
            try:
                if not status["id90"] and id90_looks_logged_in(id90_page):
                    status["id90"] = True
                    print("✓ ID90 login detected")
            except Exception:  # noqa: BLE001
                pass

            if status["perx"] and status["id90"]:
                break
            time.sleep(2)

        # Always save whatever cookies we have
        context.storage_state(path=str(out))
        status["saved"] = out.is_file()
        browser.close()

    return status
