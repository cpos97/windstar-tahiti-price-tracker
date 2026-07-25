"""Log into Perx / ID90 and save a shared Playwright browser session."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app import config

logger = logging.getLogger(__name__)

PERX_LOGIN = "https://perx.com/accounts/login/"
PERX_CRUISE = (
    "https://perx.com/cruises/windstar-cruises/star-breeze/"
    "itineraries/223329/sailings/2027-05-20/"
)
ID90_LOGIN = "https://www.id90travel.com/login"
ID90_CRUISE = (
    "https://cruise.id90travel.com/cs/forms/CruiseDetails.aspx"
    "?skin=636&did=-1&mon=5%2F1%2F2027&vid=664&pid=9476"
    "&pin=W8-1386879-1401&iid=3675695&sno=1"
)


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
    if "/login" in url or "/up-auth/login" in url:
        return False
    try:
        text = page.inner_text("body").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(
        x in text
        for x in ("log out", "logout", "sign out", "my trips", "my account", "dashboard")
    )


def login_perx(page: Page, username: str, password: str) -> tuple[bool, str]:
    page.goto(PERX_LOGIN, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(1500)
    _dismiss_cookies(page)
    page.wait_for_timeout(800)

    # Expand login accordion if needed (page has a hidden mobile form + desktop form)
    for sel in ("a[href='#login-accordion']", "text=Log In", "#login-accordion"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=400):
                loc.click(timeout=2000)
                page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001
            pass

    # Use the visible username/password fields (not the first hidden ones)
    try:
        user = page.locator("input[name='username']:visible").first
        pwd = page.locator("input[name='password']:visible").first
        user.wait_for(state="visible", timeout=12_000)
        user.click()
        user.fill("")
        user.fill(username)
        pwd.click()
        pwd.fill("")
        pwd.fill(password)
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not fill Perx login form: {exc}"

    # Submit the visible form
    submitted = False
    try:
        form = page.locator("form[action='/login']:visible").first
        if form.count():
            btn = form.locator("button[type='submit'], input[type='submit'], button:has-text('Login'), button:has-text('Log In')").first
            if btn.count() and btn.is_visible(timeout=500):
                btn.click()
                submitted = True
    except Exception:  # noqa: BLE001
        pass

    if not submitted:
        for sel in (
            "form[action='/login']:visible button",
            "button:has-text('Login'):visible",
            "button:has-text('Log In'):visible",
            "input[type='submit']:visible",
        ):
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=500):
                    btn.click()
                    submitted = True
                    break
            except Exception:  # noqa: BLE001
                continue
    if not submitted:
        page.keyboard.press("Enter")

    page.wait_for_timeout(5000)
    # Follow redirects / land somewhere non-login
    for _ in range(20):
        url = page.url.lower()
        if "/accounts/login" not in url and "/login" not in url:
            break
        if perx_looks_logged_in(page):
            break
        # Wrong password message?
        try:
            body_l = page.inner_text("body").lower()
            if any(x in body_l for x in ("invalid", "incorrect", "wrong password", "please enter a correct")):
                return False, "Perx rejected credentials (invalid username/password)"
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1000)

    # Verify rates page
    page.goto(PERX_CRUISE, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(5000)
    _dismiss_cookies(page)
    body = page.inner_text("body")
    body_l = body.lower()
    if "log in for rates" in body_l:
        return False, "Perx still shows 'Log in for rates' — check username/password"
    # Prefer seeing a real fare
    if "$" in body or "USD" in body:
        return True, "Perx login OK (cruise page loaded without login wall)"
    if perx_looks_logged_in(page):
        return True, "Perx login OK"
    return False, "Perx login may have failed (could not confirm session)"


def login_id90(page: Page, email: str, password: str) -> tuple[bool, str]:
    """
    ID90 airline login: email/username first, then password.
    Usernames like name@company (no full domain) are supported.
    """
    page.goto(ID90_LOGIN, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2000)
    _dismiss_cookies(page)
    page.wait_for_timeout(800)

    # Some accounts use "Log in with Company Name" instead of email-only
    try:
        company_link = page.get_by_text("Log in with Company Name", exact=False)
        if company_link.count() and company_link.first.is_visible(timeout=800):
            # Keep default email flow first; company link is fallback later
            pass
    except Exception:  # noqa: BLE001
        pass

    # Username / email step — accept email or text inputs
    try:
        user_box = page.locator(
            "input#email:visible, input[type='email']:visible, "
            "input[name='email']:visible, input[type='text']:visible, "
            "input[name='username']:visible"
        ).first
        user_box.wait_for(state="visible", timeout=12_000)
        # type=email may block incomplete domains; remove validation via JS fill
        page.evaluate(
            """([sel, val]) => {
              const el = document.querySelector(sel) ||
                document.querySelector('input#email') ||
                document.querySelector('input[type=email]') ||
                document.querySelector('input[type=text]');
              if (!el) return;
              el.removeAttribute('type');
              el.setAttribute('type', 'text');
              el.value = val;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            ["input#email", email],
        )
        try:
            user_box.fill(email)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(400)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        for label in ("Continue", "Next", "Log in", "Login", "Submit", "Sign in"):
            try:
                b = page.get_by_role("button", name=label)
                if b.count() and b.first.is_visible(timeout=400):
                    b.first.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:  # noqa: BLE001
                pass
        # Also try submit inputs
        try:
            page.locator("button[type='submit']:visible, input[type='submit']:visible").first.click(timeout=1500)
            page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        return False, f"ID90 username step failed: {exc}"

    # Password step (if shown)
    try:
        pwd = page.locator("input[type='password']:visible").first
        if pwd.is_visible(timeout=10_000):
            pwd.fill(password)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2500)
            for label in ("Log in", "Login", "Continue", "Sign in", "Submit"):
                try:
                    b = page.get_by_role("button", name=label)
                    if b.count() and b.first.is_visible(timeout=400):
                        b.first.click()
                        page.wait_for_timeout(3000)
                        break
                except Exception:  # noqa: BLE001
                    pass
        else:
            return (
                False,
                "ID90 password field did not appear after username. "
                "Company SSO may be required — try interactive login.",
            )
    except Exception:
        return (
            False,
            "ID90 needs interactive login (SSO / company step). "
            "Use: python scripts/login_sites.py --interactive",
        )

    for _ in range(20):
        if id90_looks_logged_in(page):
            break
        try:
            body_l = page.inner_text("body").lower()
            if any(
                x in body_l
                for x in (
                    "invalid",
                    "incorrect",
                    "wrong password",
                    "couldn't find",
                    "not found",
                    "try again",
                )
            ):
                # Still attempt cruise page — public rates may work
                break
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1000)

    # Cruise page (often works without login; confirm price still loads)
    try:
        page.goto(ID90_CRUISE, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        if "USD" in body or "$" in body or "6216" in body or "price" in body.lower():
            if id90_looks_logged_in(page):
                return True, "ID90 login OK + cruise page loads"
            return True, "ID90 cruise price page loads (session saved)"
    except Exception as exc:  # noqa: BLE001
        return False, f"ID90 cruise page failed: {exc}"

    if id90_looks_logged_in(page):
        return True, "ID90 login OK"
    return (
        False,
        "ID90 login not fully confirmed. Cruise rates often work without login; "
        "re-try interactive setup if needed.",
    )


def save_session_with_credentials(
    perx_user: str | None = None,
    perx_pass: str | None = None,
    id90_email: str | None = None,
    id90_pass: str | None = None,
    headless: bool = True,
) -> dict:
    """
    Log into available sites using credentials and write browser_session.json.
    """
    perx_user = perx_user or config.PERX_USERNAME
    perx_pass = perx_pass or config.PERX_PASSWORD
    id90_email = id90_email or config.ID90_EMAIL
    id90_pass = id90_pass or config.ID90_PASSWORD

    out = config.PLAYWRIGHT_STORAGE_STATE
    out.parent.mkdir(parents=True, exist_ok=True)
    results: dict = {"session_path": str(out), "perx": None, "id90": None}

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

        # Only persist when a login actually succeeded. This function used to
        # write unconditionally, which meant a failed login (expired password,
        # site outage, a CAPTCHA) would overwrite a perfectly good session
        # with a logged-out one and silently break all scraping.
        attempted = [r for r in (results["perx"], results["id90"]) if r]
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
