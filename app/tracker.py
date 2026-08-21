"""Core price-check logic: scrape, store history, fire alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AlertLog, CabinAvailability, Cruise, PriceHistory
from app.notifier import notify_price_drop
from app.scraper import scrape_cruise

logger = logging.getLogger(__name__)


def _try_refresh_id90_url(cruise: Cruise) -> bool:
    """Re-search ID90 for the expected sailing and swap in a fresh URL. Returns True on success."""
    from app import config
    from app.id90_search import find_current_url

    if "id90travel" not in cruise.url.lower() or not cruise.expected_date:
        return False
    # The benchmark's url is a search-results page on purpose; the drift
    # self-heal only understands CruiseDetails URLs and would clobber it.
    if "cruiseresultpage.aspx" in cruise.url.lower():
        return False

    storage_state = (
        str(config.PLAYWRIGHT_STORAGE_STATE) if config.browser_session_exists() else None
    )
    fresh_url = find_current_url(cruise.url, cruise.expected_date, storage_state)
    if not fresh_url:
        return False

    logger.info("Refreshed drifted ID90 URL for cruise %s: %s", cruise.id, fresh_url)
    cruise.url = fresh_url
    return True


def cabin_source_url(cruise: Cruise) -> str | None:
    """Which URL drives the cabin flow for this cruise.

    The benchmark is priced off an ID90 *results* page, but cabin counts need
    a *CruiseDetails* page, so it carries a separate cabin_url. Normal sources
    use their single url for both.
    """
    return cruise.cabin_url or cruise.url


def cabin_check_supported(cruise: Cruise) -> bool:
    """Cabin counts only exist on an ID90 CruiseDetails page."""
    url = (cabin_source_url(cruise) or "").lower()
    return "id90travel" in url and "cruisedetails.aspx" in url


def _looks_login_gated(error: str | None) -> bool:
    """Does this scrape error look like an expired/missing login session?"""
    if not error:
        return False
    e = error.lower()
    return (
        "log in for rates" in e
        or "login for rates" in e
        or "without login" in e
        or "members page without login" in e
    )


def refresh_login_session() -> bool:
    """Re-login with stored credentials and rewrite the saved session."""
    from app import auth_sites

    try:
        res = auth_sites.save_session_with_credentials(headless=True)
    except Exception:  # noqa: BLE001
        logger.exception("Automatic login refresh failed")
        return False
    ok = bool(res.get("any_ok"))
    logger.info(
        "Automatic login refresh: perx=%s id90=%s vtg=%s",
        (res.get("perx") or {}).get("ok"),
        (res.get("id90") or {}).get("ok"),
        (res.get("vtg") or {}).get("ok"),
    )
    return ok


def check_cruise(db: Session, cruise: Cruise) -> dict:
    """Check one cruise, update DB, email on drop. Returns a status dict."""
    result = scrape_cruise(cruise.url, cruise.css_selector, cruise.expected_date)

    if result.error and "Departure date mismatch" in result.error:
        if _try_refresh_id90_url(cruise):
            db.commit()  # persist the new URL even if the retry below still fails
            result = scrape_cruise(cruise.url, cruise.css_selector, cruise.expected_date)

    # Saved login session expired — re-login and retry once
    if _looks_login_gated(result.error):
        logger.info("Cruise %s looks login-gated; refreshing session", cruise.id)
        if refresh_login_session():
            result = scrape_cruise(cruise.url, cruise.css_selector, cruise.expected_date)

    now = datetime.now(timezone.utc)
    cruise.last_checked = now

    if result.error or result.price is None:
        cruise.last_error = result.error or "No price found"
        db.commit()
        return {
            "cruise_id": cruise.id,
            "ok": False,
            "error": cruise.last_error,
            "price": None,
        }

    new_price = float(result.price)
    old_price = cruise.current_price
    cruise.currency = result.currency or cruise.currency or "USD"
    cruise.last_error = None

    # Compare against last historical entry (date/time + price log)
    last_hist = (
        db.query(PriceHistory)
        .filter(PriceHistory.cruise_id == cruise.id)
        .order_by(PriceHistory.checked_at.desc())
        .first()
    )
    last_recorded = last_hist.price if last_hist is not None else None

    # Append a new history row only when the price actually changes (or first reading)
    should_record = last_recorded is None or abs(last_recorded - new_price) >= 0.01
    if should_record:
        history = PriceHistory(
            cruise_id=cruise.id,
            price=new_price,
            raw_text=result.raw_text,
            checked_at=now,  # date & time of this price
        )
        db.add(history)
        logger.info(
            "Price history #%s: %s → %s at %s",
            cruise.id,
            last_recorded,
            new_price,
            now.isoformat(),
        )

    # Update aggregates
    if cruise.lowest_price is None or new_price < cruise.lowest_price:
        cruise.lowest_price = new_price
    if cruise.highest_price is None or new_price > cruise.highest_price:
        cruise.highest_price = new_price

    alert_info = None
    if old_price is not None and new_price < old_price - 0.009:
        cruise.previous_price = old_price
        cruise.current_price = new_price
        notify = notify_price_drop(
            cruise_name=cruise.name,
            cruise_url=cruise.url,
            old_price=old_price,
            new_price=new_price,
            currency=cruise.currency,
        )
        from app import config

        recipients = ", ".join(p["email"] for p in config.alert_recipients())
        alert = AlertLog(
            cruise_id=cruise.id,
            old_price=old_price,
            new_price=new_price,
            sent_to=recipients or "Mac notification",
            success=bool(notify.get("ok")),
            message=notify.get("message"),
        )
        db.add(alert)
        alert_info = {
            "sent": notify.get("ok"),
            "message": notify.get("message"),
            "old": old_price,
            "new": new_price,
            "mac_ok": notify.get("mac_ok"),
            "email_ok": notify.get("email_ok"),
        }
        logger.info(
            "Price drop on %s: %.2f -> %.2f (%s)",
            cruise.name,
            old_price,
            new_price,
            notify.get("message"),
        )
    else:
        if old_price is not None and abs(old_price - new_price) >= 0.01:
            cruise.previous_price = old_price
        cruise.current_price = new_price

    # Auto-fill name from page title if still placeholder-ish
    if result.page_title and (not cruise.name or cruise.name.strip().lower() in {"new cruise", "untitled"}):
        cruise.name = result.page_title[:120]

    db.commit()
    return {
        "cruise_id": cruise.id,
        "ok": True,
        "error": None,
        "price": new_price,
        "currency": cruise.currency,
        "raw_text": result.raw_text,
        "alert": alert_info,
        "changed": should_record and old_price is not None,
    }


def check_all_active(db: Session) -> list[dict]:
    cruises = db.query(Cruise).filter(Cruise.active.is_(True)).all()
    results = []
    for cruise in cruises:
        try:
            results.append(check_cruise(db, cruise))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error checking cruise %s", cruise.id)
            cruise.last_error = str(exc)
            cruise.last_checked = datetime.now(timezone.utc)
            db.commit()
            results.append({"cruise_id": cruise.id, "ok": False, "error": str(exc)})
    return results


def check_cabin_availability(db: Session, cruise: Cruise) -> dict:
    """Check remaining cabins for the tracked Ocean View Suite categories (ID90 only)."""
    from app import config
    from app.cabin_scraper import check_categories

    if not cabin_check_supported(cruise):
        return {
            "cruise_id": cruise.id,
            "ok": False,
            "error": "Cabin availability needs an ID90 CruiseDetails URL",
        }
    url = cabin_source_url(cruise)

    storage_state = (
        str(config.PLAYWRIGHT_STORAGE_STATE) if config.browser_session_exists() else None
    )
    try:
        results = check_categories(url, storage_state, expected_date=cruise.expected_date)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Cabin availability check failed for cruise %s", cruise.id)
        return {"cruise_id": cruise.id, "ok": False, "error": str(exc)}

    now = datetime.now(timezone.utc)

    # Distinguish "absent from ID90's list" (sold out → record 0) from
    # "couldn't read the page" (unknown → leave the last known value alone).
    # Conflating them would either strand a stale count on the dashboard
    # forever, or falsely report a sell-out on a mere timeout.
    if not any(r.get("status") == "ok" for r in results):
        return {
            "cruise_id": cruise.id,
            "ok": False,
            "error": "No categories read successfully — treating as a failed scrape",
        }

    for r in results:
        status = r.get("status")
        if status == "error":
            logger.warning(
                "Category %s could not be read — keeping last known count", r["code"]
            )
            continue

        last = (
            db.query(CabinAvailability)
            .filter(
                CabinAvailability.cruise_id == cruise.id,
                CabinAvailability.category_code == r["code"],
            )
            .order_by(CabinAvailability.checked_at.desc())
            .first()
        )

        if status == "absent":
            # Sold out. Keep the previously-known display name — an absent
            # category has no name on the page, and falling back to the bare
            # code renders as "S · S" on the dashboard.
            prior_name = last.category_name if last is not None else None
            r = {**r, "available": 0, "name": prior_name or r["code"]}
            logger.info(
                "Category %s absent from ID90 list — recording as sold out (0)", r["code"]
            )
        # Only append a new history row when the count actually changed (or first reading)
        if last is not None and last.available == r["available"]:
            continue
        db.add(
            CabinAvailability(
                cruise_id=cruise.id,
                category_code=r["code"],
                category_name=r["name"],
                available=r["available"],
                checked_at=now,
            )
        )
    # Track when we last actually ran a check, separate from the history
    # rows above — those only get a new entry when a count *changes*, so
    # they can't be used to show "last checked" for unchanged categories.
    cruise.cabin_last_checked = now
    db.commit()

    return {"cruise_id": cruise.id, "ok": True, "results": results}
