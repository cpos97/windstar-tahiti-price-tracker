"""FastAPI web dashboard for Cruise Price Tracker."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app import config
from app.database import get_db, init_db
from app.jobs import get_status as get_check_status
from app.jobs import start_check_all
from app.models import AlertLog, CabinAvailability, Cruise, PriceHistory
from app.emailer import send_family_invite_email
from app.notifier import send_test_alert
from app.scheduler import start_scheduler, stop_scheduler
from app.tracker import check_all_active, check_cabin_availability, check_cruise

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


def money(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "—"
    symbols = {"USD": "$", "CAD": "C$", "EUR": "€", "GBP": "£"}
    sym = symbols.get(currency or "USD", f"{currency} ")
    return f"{sym}{value:,.2f}"


def fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "Never"
    # SQLite may return naive UTC
    if value.tzinfo is None:
        return value.strftime("%b %d, %Y %H:%M UTC")
    return value.strftime("%b %d, %Y %H:%M %Z")


templates.env.filters["money"] = money
templates.env.filters["fmt_dt"] = fmt_dt


def _static_version() -> int:
    """Mtime-based cache-buster so browsers pick up CSS/JS changes immediately."""
    try:
        return int((BASE / "static" / "style.css").stat().st_mtime)
    except OSError:
        return 0


templates.env.globals["static_version"] = _static_version


def departure_context() -> dict:
    """Shared countdown fields for templates."""
    raw = (config.DEPARTURE_DATE or "2027-05-20").strip()
    try:
        if "T" in raw:
            dep = datetime.fromisoformat(raw)
            if dep.tzinfo is None:
                dep_iso = dep.isoformat()
            else:
                dep_iso = dep.isoformat()
            label = dep.strftime("%B %d, %Y")
        else:
            d = date.fromisoformat(raw[:10])
            dep_iso = f"{d.isoformat()}T00:00:00"
            label = d.strftime("%B %d, %Y")
    except ValueError:
        dep_iso = "2027-05-20T00:00:00"
        label = "May 20, 2027"
    return {
        "departure_iso": dep_iso,
        "departure_label": label,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    start_scheduler()
    logger.info(
        "Windstar Tahiti Price Tracker ready at http://%s:%s",
        config.HOST,
        config.PORT,
    )
    yield
    stop_scheduler()


app = FastAPI(title="Windstar Tahiti Price Tracker", lifespan=lifespan)

# Routes that must stay reachable without the site password: health checks,
# the cron endpoint (which has its own CRON_SECRET check), the login page
# itself, and static assets (so the login page can load its CSS).
_AUTH_EXEMPT_PATHS = {"/api/health", "/api/check-prices", "/login"}
AUTH_COOKIE_NAME = "site_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year — "stay logged in"


def _auth_cookie_value() -> str:
    import hashlib

    return hashlib.sha256(config.SITE_PASSWORD.encode()).hexdigest()


class SitePasswordMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not config.SITE_PASSWORD or path in _AUTH_EXEMPT_PATHS or path.startswith("/static/"):
            return await call_next(request)

        cookie = request.cookies.get(AUTH_COOKIE_NAME, "")
        if secrets.compare_digest(cookie, _auth_cookie_value()):
            return await call_next(request)

        from urllib.parse import quote

        from fastapi.responses import RedirectResponse as _Redirect

        return _Redirect(f"/login?next={quote(path)}", status_code=303)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SitePasswordMiddleware)

app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

DbDep = Annotated[Session, Depends(get_db)]

CABIN_CATEGORY_ORDER = ["S", "S1", "SS1", "S2", "S3"]


def latest_cabin_availability(db: Session, cruise_id: int) -> list[CabinAvailability]:
    """Most recent reading per category, in a fixed display order."""
    rows = (
        db.query(CabinAvailability)
        .filter(CabinAvailability.cruise_id == cruise_id)
        .order_by(CabinAvailability.checked_at.desc())
        .all()
    )
    latest: dict[str, CabinAvailability] = {}
    for row in rows:
        latest.setdefault(row.category_code, row)
    return sorted(
        latest.values(),
        key=lambda r: (
            CABIN_CATEGORY_ORDER.index(r.category_code)
            if r.category_code in CABIN_CATEGORY_ORDER
            else 99
        ),
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not config.SITE_PASSWORD:
        return RedirectResponse("/", status_code=303)
    next_path = request.query_params.get("next") or "/"
    if not next_path.startswith("/"):
        next_path = "/"
    error = request.query_params.get("error") == "1"
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next_path": next_path,
            "error": error,
            "check_interval": config.CHECK_INTERVAL_MINUTES,
            "email_ok": config.email_configured(),
        },
    )


@app.post("/login")
def login_submit(request: Request, password: Annotated[str, Form()], next: Annotated[str, Form()] = "/"):
    if not next.startswith("/"):
        next = "/"
    if config.SITE_PASSWORD and secrets.compare_digest(password, config.SITE_PASSWORD):
        resp = RedirectResponse(next, status_code=303)
        resp.set_cookie(
            AUTH_COOKIE_NAME,
            _auth_cookie_value(),
            max_age=AUTH_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return resp
    from urllib.parse import quote

    return RedirectResponse(f"/login?next={quote(next)}&error=1", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbDep):
    cruises = db.query(Cruise).order_by(Cruise.created_at.desc()).all()
    recent_alerts = (
        db.query(AlertLog).order_by(AlertLog.created_at.desc()).limit(10).all()
    )

    # Price history for charts/timelines (chronological, oldest → newest)
    history_by_cruise: dict[int, list[dict]] = {}
    for c in cruises:
        rows = (
            db.query(PriceHistory)
            .filter(PriceHistory.cruise_id == c.id)
            .order_by(PriceHistory.checked_at.asc())
            .all()
        )
        # Backfill a single point if we have a price but no history yet
        if not rows and c.current_price is not None:
            seed = PriceHistory(
                cruise_id=c.id,
                price=float(c.current_price),
                raw_text=None,
                checked_at=c.last_checked or c.created_at or datetime.now(timezone.utc),
            )
            db.add(seed)
            db.commit()
            db.refresh(seed)
            rows = [seed]
        history_by_cruise[c.id] = [
            {
                "price": h.price,
                "checked_at": h.checked_at.isoformat() if h.checked_at else "",
                "label": (
                    h.checked_at.strftime("%b %d, %Y · %H:%M")
                    if h.checked_at
                    else "—"
                ),
                "date_short": (
                    h.checked_at.strftime("%b %d")
                    if h.checked_at
                    else "—"
                ),
            }
            for h in rows
        ]

    cabin_availability_by_cruise: dict[int, list[CabinAvailability]] = {}
    for c in cruises:
        rows = latest_cabin_availability(db, c.id)
        if rows:
            cabin_availability_by_cruise[c.id] = rows

    check_status = get_check_status()
    flash = None
    if request.query_params.get("checking") == "1":
        flash = {
            "type": "ok" if not check_status.get("error") else "warn",
            "msg": check_status.get("message")
            or "Price check started — this can take a few minutes.",
        }
    elif request.query_params.get("checked") == "1":
        flash = {
            "type": "ok" if not check_status.get("error") else "warn",
            "msg": check_status.get("message") or "Price check finished.",
        }
    elif request.query_params.get("busy") == "1":
        flash = {
            "type": "warn",
            "msg": "A price check is already running — wait a moment, then refresh.",
        }

    ctx = {
        "cruises": cruises,
        "history_by_cruise": history_by_cruise,
        "cabin_availability_by_cruise": cabin_availability_by_cruise,
        "recent_alerts": recent_alerts,
        "email_ok": config.email_configured(),
        "alert_email": config.ALERT_EMAIL,
        "alert_recipients": config.alert_recipients(),
        "check_interval": config.CHECK_INTERVAL_MINUTES,
        "check_running": check_status.get("running"),
        "check_message": check_status.get("message"),
        "flash": flash,
        "dashboard_url": config.DASHBOARD_URL,
    }
    ctx.update(departure_context())
    return templates.TemplateResponse(request, "index.html", ctx)


@app.post("/family/intro-email")
def family_intro_email(
    recipient_name: str = Form(...),
    recipient_email: str = Form(...),
    tracker_url: str = Form(""),
):
    """Send a personalized family invitation (CC Cameron)."""
    ok, message = send_family_invite_email(
        recipient_name=recipient_name,
        recipient_email=recipient_email,
        tracker_url=tracker_url.strip() or None,
    )
    from urllib.parse import quote

    short = quote(message[:200])
    return RedirectResponse(
        f"/settings?intro={'ok' if ok else 'fail'}&msg={short}",
        status_code=303,
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    intro_flash = None
    if request.query_params.get("intro") == "ok":
        intro_flash = {
            "type": "ok",
            "msg": request.query_params.get("msg") or "Invitation sent successfully!",
        }
    elif request.query_params.get("intro") == "fail":
        intro_flash = {
            "type": "warn",
            "msg": request.query_params.get("msg") or "Invitation failed to send.",
        }

    ctx = {
        "email_ok": config.email_configured(),
        "email_provider": config.EMAIL_PROVIDER,
        "email_provider_label": config.email_provider_label(),
        "email_from": config.EMAIL_FROM,
        "resend_key_set": bool(
            config.RESEND_API_KEY and config.RESEND_API_KEY != "re_xxxxxxxx"
        ),
        "smtp_host": config.SMTP_HOST,
        "smtp_port": config.SMTP_PORT,
        "smtp_user": config.SMTP_USER,
        "alert_email": config.ALERT_EMAIL,
        "check_interval": config.CHECK_INTERVAL_MINUTES,
        "env_path": str(config.BASE_DIR / ".env"),
        "session_ok": config.browser_session_exists(),
        "session_path": str(config.PLAYWRIGHT_STORAGE_STATE),
        "dashboard_url": config.DASHBOARD_URL,
        "intro_flash": intro_flash,
    }
    ctx.update(departure_context())
    return templates.TemplateResponse(request, "settings.html", ctx)


@app.post("/cruises")
def add_cruise(
    db: DbDep,
    name: str = Form(...),
    url: str = Form(...),
    css_selector: str = Form(""),
    check_now: str = Form("yes"),
):
    name = name.strip() or "Untitled cruise"
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")

    cruise = Cruise(
        name=name,
        url=url,
        css_selector=css_selector.strip() or None,
        active=True,
    )
    db.add(cruise)
    db.commit()
    db.refresh(cruise)

    if check_now == "yes":
        check_cruise(db, cruise)

    return RedirectResponse(f"/cruises/{cruise.id}", status_code=303)


@app.get("/cruises/{cruise_id}", response_class=HTMLResponse)
def cruise_detail(request: Request, cruise_id: int, db: DbDep):
    cruise = db.get(Cruise, cruise_id)
    if not cruise:
        raise HTTPException(404, "Cruise not found")
    history = (
        db.query(PriceHistory)
        .filter(PriceHistory.cruise_id == cruise_id)
        .order_by(PriceHistory.checked_at.asc())
        .all()
    )
    alerts = (
        db.query(AlertLog)
        .filter(AlertLog.cruise_id == cruise_id)
        .order_by(AlertLog.created_at.desc())
        .limit(20)
        .all()
    )
    cabin_rows = latest_cabin_availability(db, cruise_id)

    cabin_history = (
        db.query(CabinAvailability)
        .filter(CabinAvailability.cruise_id == cruise_id)
        .order_by(CabinAvailability.checked_at.asc())
        .all()
    )
    cabin_series: dict[str, dict] = {}
    for row in cabin_history:
        series = cabin_series.setdefault(
            row.category_code, {"label": f"{row.category_code} · {row.category_name}", "points": []}
        )
        series["points"].append({"x": row.checked_at.isoformat(), "y": row.available})
    cabin_chart_series = [
        cabin_series[code]
        for code in CABIN_CATEGORY_ORDER
        if code in cabin_series
    ]

    ctx = {
        "cruise": cruise,
        "history": list(reversed(history[-50:])),
        "alerts": alerts,
        "email_ok": config.email_configured(),
        "check_interval": config.CHECK_INTERVAL_MINUTES,
        "cabin_availability": cabin_rows,
        "cabin_checked_at": cruise.cabin_last_checked,
        "cabin_chart_series": cabin_chart_series,
    }
    ctx.update(departure_context())
    return templates.TemplateResponse(request, "detail.html", ctx)


CHECK_COOLDOWN_SECONDS = 60


@app.post("/cruises/{cruise_id}/check")
def check_one(cruise_id: int, db: DbDep):
    cruise = db.get(Cruise, cruise_id)
    if not cruise:
        raise HTTPException(404, "Cruise not found")
    if (
        cruise.last_checked
        and (datetime.now(timezone.utc) - cruise.last_checked.replace(tzinfo=timezone.utc)).total_seconds()
        < CHECK_COOLDOWN_SECONDS
    ):
        return RedirectResponse(f"/cruises/{cruise_id}", status_code=303)
    check_cruise(db, cruise)
    return RedirectResponse(f"/cruises/{cruise_id}", status_code=303)


@app.post("/cruises/{cruise_id}/check-cabins")
def check_cabins(cruise_id: int, db: DbDep):
    cruise = db.get(Cruise, cruise_id)
    if not cruise:
        raise HTTPException(404, "Cruise not found")
    if (
        cruise.cabin_last_checked
        and (
            datetime.now(timezone.utc) - cruise.cabin_last_checked.replace(tzinfo=timezone.utc)
        ).total_seconds()
        < CHECK_COOLDOWN_SECONDS
    ):
        return RedirectResponse(f"/cruises/{cruise_id}", status_code=303)
    check_cabin_availability(db, cruise)
    return RedirectResponse(f"/cruises/{cruise_id}", status_code=303)


@app.post("/cruises/{cruise_id}/toggle")
def toggle_cruise(cruise_id: int, db: DbDep):
    cruise = db.get(Cruise, cruise_id)
    if not cruise:
        raise HTTPException(404, "Cruise not found")
    cruise.active = not cruise.active
    db.commit()
    return RedirectResponse(f"/cruises/{cruise_id}", status_code=303)


@app.post("/cruises/{cruise_id}/update")
def update_cruise(
    cruise_id: int,
    db: DbDep,
    name: str = Form(...),
    url: str = Form(...),
    css_selector: str = Form(""),
):
    cruise = db.get(Cruise, cruise_id)
    if not cruise:
        raise HTTPException(404, "Cruise not found")
    cruise.name = name.strip() or cruise.name
    cruise.url = url.strip()
    cruise.css_selector = css_selector.strip() or None
    db.commit()
    return RedirectResponse(f"/cruises/{cruise_id}", status_code=303)


@app.post("/cruises/{cruise_id}/delete")
def delete_cruise(cruise_id: int, db: DbDep):
    cruise = db.get(Cruise, cruise_id)
    if not cruise:
        raise HTTPException(404, "Cruise not found")
    db.delete(cruise)
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/check-all")
def check_all():
    """Start a background price check (Playwright scrapes take 1–2+ minutes)."""
    started, message = start_check_all()
    if not started:
        return RedirectResponse("/?busy=1", status_code=303)
    return RedirectResponse("/?checking=1", status_code=303)


@app.get("/api/check-status")
def api_check_status():
    return get_check_status()


def _extract_cron_secret(
    request: Request,
    x_cron_secret: str | None,
    authorization: str | None,
    secret_q: str | None,
) -> str | None:
    if x_cron_secret:
        return x_cron_secret
    if secret_q:
        return secret_q
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    # Also accept custom header without x-
    return request.headers.get("cron-secret")


def _require_cron_auth(
    request: Request,
    x_cron_secret: str | None = None,
    authorization: str | None = None,
    secret: str | None = None,
) -> None:
    if not config.cron_secret_configured():
        raise HTTPException(
            status_code=503,
            detail="CRON_SECRET is not configured on the server. Set it in .env / environment.",
        )
    provided = _extract_cron_secret(request, x_cron_secret, authorization, secret)
    if not config.verify_cron_secret(provided):
        raise HTTPException(status_code=401, detail="Invalid or missing cron secret")


@app.api_route("/api/check-prices", methods=["GET", "POST"])
def api_check_prices(
    request: Request,
    db: DbDep,
    secret: str | None = Query(None, description="Cron secret (prefer header instead)"),
    x_cron_secret: str | None = Header(None, alias="X-Cron-Secret"),
    authorization: str | None = Header(None),
    async_mode: bool = Query(
        False,
        alias="async",
        description="If true, start background job and return immediately",
    ),
):
    """
    Hourly cron target: scrape all active cruises, update SQLite history,
    and send price-drop emails (YOUR TAHITI CRUISE DROPPED IN PRICE!).

    Auth (any one):
      Header:  X-Cron-Secret: <CRON_SECRET>
      Header:  Authorization: Bearer <CRON_SECRET>
      Query:   ?secret=<CRON_SECRET>   (less secure; avoid if possible)
    """
    _require_cron_auth(request, x_cron_secret, authorization, secret)

    if async_mode:
        started, message = start_check_all()
        return JSONResponse(
            {
                "ok": started,
                "mode": "async",
                "message": message,
                "status_url": "/api/check-status",
            },
            status_code=202 if started else 409,
        )

    # Synchronous run — best for external cron (waits until scrapes finish)
    results = check_all_active(db)
    ok_n = sum(1 for r in results if r.get("ok"))
    drops = [r for r in results if r.get("alert")]
    return {
        "ok": True,
        "mode": "sync",
        "checked": len(results),
        "succeeded": ok_n,
        "failed": len(results) - ok_n,
        "price_drops": len(drops),
        "results": results,
        "message": (
            f"Checked {len(results)} source(s): {ok_n} ok, "
            f"{len(drops)} price-drop alert(s) fired."
        ),
    }


@app.get("/api/health")
def api_health():
    """Lightweight uptime ping (no secret, no scraping)."""
    return {
        "ok": True,
        "service": "windstar-tahiti-price-tracker",
        "cron_secret_configured": config.cron_secret_configured(),
        "email_configured": config.email_configured(),
        "local_scheduler": not config.DISABLE_LOCAL_SCHEDULER,
    }


@app.post("/settings/test-email")
def test_email():
    ok, message = send_test_alert()
    # Keep msg short for query string
    short = message[:200].replace("\n", " ")
    return RedirectResponse(
        f"/settings?test={'ok' if ok else 'fail'}&msg={short}",
        status_code=303,
    )


@app.get("/api/cruises")
def api_list(db: DbDep):
    cruises = db.query(Cruise).order_by(Cruise.id).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "url": c.url,
            "current_price": c.current_price,
            "previous_price": c.previous_price,
            "lowest_price": c.lowest_price,
            "currency": c.currency,
            "active": c.active,
            "last_checked": c.last_checked.isoformat() if c.last_checked else None,
            "last_error": c.last_error,
        }
        for c in cruises
    ]


@app.post("/api/cruises/{cruise_id}/check")
def api_check(cruise_id: int, db: DbDep):
    cruise = db.get(Cruise, cruise_id)
    if not cruise:
        raise HTTPException(404, "Cruise not found")
    return check_cruise(db, cruise)
