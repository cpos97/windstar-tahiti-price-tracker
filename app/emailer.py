"""Send luxury-styled emails via Resend API or SMTP.

All credentials come from environment variables (see app.config / SECRETS.md).
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from html import escape

import httpx

from app import config

logger = logging.getLogger(__name__)

PRICE_DROP_SUBJECT = "YOUR TAHITI CRUISE DROPPED IN PRICE!"
MOM_INTRO_SUBJECT = "🌴 We're going to Tahiti! Check out our family trip tracker"


def _money(amount: float, currency: str = "USD") -> str:
    symbols = {"USD": "$", "CAD": "C$", "EUR": "€", "GBP": "£"}
    sym = symbols.get(currency or "USD", f"{currency} ")
    return f"{sym}{amount:,.2f}"


def _price_drop_html(
    greeting: str,
    cruise_name: str,
    cruise_url: str,
    old_price: float,
    new_price: float,
    currency: str,
    drop: float,
    drop_pct: float,
) -> str:
    greet = escape(greeting)
    name = escape(cruise_name)
    booking_url = escape(cruise_url)
    dashboard = escape(config.DASHBOARD_URL)
    was = escape(_money(old_price, currency))
    now = escape(_money(new_price, currency))
    saved = escape(_money(drop, currency))
    pct = f"{drop_pct:.1f}"

    return f"""\
<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <title>{escape(PRICE_DROP_SUBJECT)}</title>
</head>
<body style="margin:0;padding:0;background-color:#F5EFE6;-webkit-text-size-adjust:100%;">
  <div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">
    {greet} Your French Polynesia fare just dropped.
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#F5EFE6;width:100%;">
    <tr>
      <td align="center" style="padding:36px 16px 48px 16px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;width:100%;background-color:#FFFFFF;border-radius:20px;overflow:hidden;box-shadow:0 16px 48px rgba(10,45,66,0.12);">
          <tr>
            <td align="center" style="background-color:#0A3D5C;background:linear-gradient(145deg,#06283D 0%,#0A3D5C 40%,#1565A0 100%);padding:36px 28px 32px 28px;">
              <p style="margin:0 0 10px 0;font-family:Georgia,serif;font-size:11px;letter-spacing:0.22em;text-transform:uppercase;color:#7FDBDA;">
                Windstar Tahiti Price Tracker
              </p>
              <p style="margin:0 0 14px 0;font-size:32px;line-height:1;">🌴</p>
              <h1 style="margin:0;font-family:Georgia,serif;font-size:26px;line-height:1.35;color:#FFFFFF;font-weight:normal;">
                Great news! Your dream vacation to French Polynesia just got cheaper.
              </h1>
            </td>
          </tr>
          <tr>
            <td style="height:5px;background:linear-gradient(90deg,#2EC4B6 0%,#48CAE4 50%,#E07A5F 100%);font-size:0;line-height:0;">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:36px 32px 12px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
              <p style="margin:0 0 20px 0;font-size:22px;line-height:1.3;color:#0A3D5C;font-weight:600;font-family:Georgia,serif;">
                {greet}
              </p>
              <p style="margin:0 0 8px 0;font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:#8A7560;font-weight:700;">
                Which cruise
              </p>
              <p style="margin:0 0 28px 0;font-size:20px;line-height:1.4;color:#0A3D5C;font-weight:600;font-family:Georgia,serif;">
                {name}
              </p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 28px 0;">
                <tr>
                  <td align="center" style="background-color:#FFF8F0;border:1px solid #F0D9C4;border-radius:16px;padding:22px 20px;">
                    <p style="margin:0 0 6px 0;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:#B85C38;font-weight:700;">
                      The price cut
                    </p>
                    <p style="margin:0;font-size:26px;line-height:1.3;color:#0A3D5C;font-weight:700;font-family:Georgia,serif;">
                      💰 You save: {saved} per person!
                    </p>
                    <p style="margin:8px 0 0 0;font-size:14px;color:#8A7560;">
                      That’s {pct}% off the last price we saw
                    </p>
                  </td>
                </tr>
              </table>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td width="48%" valign="top" style="padding:0 6px 0 0;">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                      <tr>
                        <td align="center" style="background-color:#F7F3EE;border-radius:14px;padding:18px 12px;border:1px solid #E8DFD2;">
                          <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#8A7560;font-weight:700;">Previous price</p>
                          <p style="margin:0;font-size:22px;color:#94A3B8;text-decoration:line-through;font-weight:600;">{was}</p>
                        </td>
                      </tr>
                    </table>
                  </td>
                  <td width="4%" style="font-size:0;">&nbsp;</td>
                  <td width="48%" valign="top" style="padding:0 0 0 6px;">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                      <tr>
                        <td align="center" style="background-color:#E8FAF7;border-radius:14px;padding:18px 12px;border:1px solid #B8EBE4;">
                          <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#0D6B63;font-weight:700;">Current price</p>
                          <p style="margin:0;font-size:26px;color:#0A3D5C;font-weight:700;font-family:Georgia,serif;">{now}</p>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
              <p style="margin:20px 0 0 0;font-size:15px;line-height:1.55;color:#5A6D7E;text-align:center;">
                Rates can change quickly — open the live tracker for the latest reading.
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:24px 32px 36px 32px;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td align="center" style="border-radius:14px;background-color:#2EC4B6;">
                    <a href="{dashboard}" target="_blank"
                       style="display:inline-block;padding:16px 36px;font-size:16px;font-weight:700;color:#FFFFFF;text-decoration:none;border-radius:14px;">
                      View Live Pricing Tracker
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:18px 0 0 0;font-size:13px;">
                <a href="{booking_url}" target="_blank" style="color:#1565A0;text-decoration:underline;">
                  Or open the booking page directly
                </a>
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" style="background-color:#0A3D5C;padding:22px 28px;">
              <p style="margin:0 0 6px 0;font-size:14px;color:#7FDBDA;font-family:Georgia,serif;font-style:italic;">
                Tahiti · Moorea · Bora Bora await
              </p>
              <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.65);">
                Windstar Star Breeze · Postma &amp; Wozniak Family Voyage
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _price_drop_text(
    greeting: str,
    cruise_name: str,
    cruise_url: str,
    old_price: float,
    new_price: float,
    currency: str,
    drop: float,
    drop_pct: float,
) -> str:
    return f"""{PRICE_DROP_SUBJECT}

{greeting}

Great news! Your dream vacation to French Polynesia just got cheaper.

WHICH CRUISE
{cruise_name}

THE PRICE CUT
You save: {_money(drop, currency)} per person! ({drop_pct:.1f}% off)

Previous price: {_money(old_price, currency)}
Current price:  {_money(new_price, currency)}

View Live Pricing Tracker:
{config.DASHBOARD_URL}

Booking page:
{cruise_url}

— Windstar Tahiti Price Tracker
"""


def _family_invite_html(recipient_name: str, tracker_url: str) -> str:
    name = escape(recipient_name.strip())
    greeting = escape(f"Hi {recipient_name.strip()}!")
    url = escape(tracker_url)
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(MOM_INTRO_SUBJECT)}</title>
</head>
<body style="margin:0;padding:0;background-color:#F5EFE6;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#F5EFE6;">
    <tr>
      <td align="center" style="padding:36px 16px 48px 16px;">
        <table role="presentation" width="100%" style="max-width:600px;background:#FFFFFF;border-radius:20px;overflow:hidden;box-shadow:0 16px 48px rgba(10,45,66,0.12);">
          <tr>
            <td align="center" style="background:linear-gradient(145deg,#06283D 0%,#0A3D5C 45%,#1565A0 100%);padding:40px 28px;">
              <p style="margin:0 0 8px;font-size:11px;letter-spacing:0.2em;text-transform:uppercase;color:#7FDBDA;">
                Windstar Tahiti Price Tracker
              </p>
              <p style="margin:0 0 12px;font-size:36px;">🌴</p>
              <h1 style="margin:0;font-family:Georgia,serif;font-size:30px;color:#FFFFFF;font-weight:normal;">
                Ia Orana! (Welcome!)
              </h1>
            </td>
          </tr>
          <tr>
            <td style="height:5px;background:linear-gradient(90deg,#2EC4B6,#48CAE4,#E07A5F);font-size:0;">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:36px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1B2B3A;font-size:16px;line-height:1.65;">
              <p style="margin:0 0 16px;font-size:20px;color:#0A3D5C;font-family:Georgia,serif;font-weight:600;">
                {greeting}
              </p>
              <p style="margin:0 0 16px;">
                To get us excited for our upcoming Windstar family voyage, I built us our very own private Tahiti Cruise Tracker!
              </p>
              <p style="margin:0 0 16px;">
                This website tracks the current prices, visualizes our pricing history, and counts down the days until we set sail. I have set up an automated system that scans for price drops 24/7. If the price of either of our watched cruises drops, you will get an automated email alert from <strong>Windstar Tahiti Price Tracker</strong> immediately so we can rebook at the lower rate.
              </p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:28px 0;">
                <tr>
                  <td align="center">
                    <a href="{url}" target="_blank"
                       style="display:inline-block;padding:16px 28px;background:#2EC4B6;color:#FFFFFF;text-decoration:none;border-radius:14px;font-weight:700;font-size:16px;">
                      👉 Click here to view our Live Family Tracker
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 8px;color:#0A3D5C;font-family:Georgia,serif;font-size:18px;">
                Let the countdown begin!
              </p>
              <p style="margin:16px 0 0;color:#0A3D5C;">
                Love,<br/>
                <strong>Cameron</strong>
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" style="background:#0A3D5C;padding:20px 28px;">
              <p style="margin:0;font-size:13px;color:#7FDBDA;font-family:Georgia,serif;font-style:italic;">
                Postma &amp; Wozniak Family Voyage · Tahiti Expedition · Party of 4
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _family_invite_text(recipient_name: str, tracker_url: str) -> str:
    name = recipient_name.strip()
    return f"""Ia Orana! (Welcome!)

Hi {name}!

To get us excited for our upcoming Windstar family voyage, I built us our very own private Tahiti Cruise Tracker!

This website tracks the current prices, visualizes our pricing history, and counts down the days until we set sail. I have set up an automated system that scans for price drops 24/7. If the price of either of our watched cruises drops, you will get an automated email alert from 'Windstar Tahiti Price Tracker' immediately so we can rebook at the lower rate.

👉 Click here to view our Live Family Tracker:
{tracker_url}

Let the countdown begin!
Love,
Cameron

— Windstar Tahiti Price Tracker
"""


def _send_via_resend(
    subject: str,
    text: str,
    html: str | None = None,
    *,
    to: str,
    cc: list[str] | None = None,
) -> tuple[bool, str]:
    if not config.RESEND_API_KEY or config.RESEND_API_KEY == "re_xxxxxxxx":
        return False, "Set RESEND_API_KEY in environment / GitHub Secrets"

    payload: dict = {
        "from": config.EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html
    if cc:
        payload["cc"] = cc

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {config.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            logger.info("Resend email sent to %s (cc=%s)", to, cc)
            return True, f"Email sent to {to}" + (f" (cc {', '.join(cc)})" if cc else "")
        detail = resp.text
        try:
            detail = resp.json().get("message") or resp.text
        except Exception:  # noqa: BLE001
            pass
        return False, f"Resend error ({resp.status_code}): {detail}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Resend request failed")
        return False, str(exc)


def _send_via_smtp(
    subject: str,
    text: str,
    html: str | None = None,
    *,
    to: str,
    cc: list[str] | None = None,
) -> tuple[bool, str]:
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        return False, "SMTP_USER and SMTP_PASSWORD are not set in environment"

    # Gmail requires From to be the authenticated account (display name is fine)
    from_header = f"Windstar Tahiti Price Tracker <{config.SMTP_USER}>"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    recipients = [to] + (cc or [])
    # Gmail app passwords are often copied with spaces — strip them
    password = (config.SMTP_PASSWORD or "").replace(" ", "")
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USER, password)
            server.send_message(msg, to_addrs=recipients)
        logger.info("SMTP email sent to %s (cc=%s)", to, cc)
        return True, f"Email sent to {to}" + (f" (cc {', '.join(cc)})" if cc else "")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send SMTP email")
        err = str(exc)
        if "Username and Password not accepted" in err or "Application-specific password" in err:
            return (
                False,
                "Gmail rejected login. Create an App Password at "
                "https://myaccount.google.com/apppasswords and set SMTP_PASSWORD in .env",
            )
        return False, err


def _sandbox_banner_text(original_to: str, original_cc: list[str] | None) -> str:
    cc_part = f" (CC: {', '.join(original_cc)})" if original_cc else ""
    return (
        f"[SANDBOX MODE: This email was originally addressed to: {original_to}{cc_part}]\n\n"
    )


def _sandbox_banner_html(original_to: str, original_cc: list[str] | None) -> str:
    cc_part = f" (CC: {escape(', '.join(original_cc))})" if original_cc else ""
    return f"""\
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 0 0;">
  <tr>
    <td style="background:#FEF3C7;border-bottom:2px solid #F59E0B;padding:12px 18px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;line-height:1.4;color:#92400E;font-weight:600;">
      [SANDBOX MODE: This email was originally addressed to: {escape(original_to)}{cc_part}]
    </td>
  </tr>
</table>
"""


def _apply_sandbox(
    subject: str,
    text: str,
    html: str | None,
    *,
    to: str,
    cc: list[str] | None,
) -> tuple[str, str, str | None, str, list[str] | None, str]:
    """
    Redirect delivery to SANDBOX_INBOX and prepend original-recipient banner.
    Returns (subject, text, html, to, cc, note).
    """
    if not config.EMAIL_SANDBOX:
        return subject, text, html, to, cc, ""

    inbox = config.SANDBOX_INBOX
    # If already going only to the sandbox inbox with no external intent, still banner if "to" differs
    original_to = to
    original_cc = list(cc) if cc else None

    banner_t = _sandbox_banner_text(original_to, original_cc)
    new_text = banner_t + (text or "")

    new_html = html
    if html:
        banner_h = _sandbox_banner_html(original_to, original_cc)
        # Prefer insert right after <body ...>
        lower = html.lower()
        idx = lower.find("<body")
        if idx != -1:
            close = html.find(">", idx)
            if close != -1:
                new_html = html[: close + 1] + banner_h + html[close + 1 :]
            else:
                new_html = banner_h + html
        else:
            new_html = banner_h + html
    else:
        new_html = (
            f"<html><body>{_sandbox_banner_html(original_to, original_cc)}"
            f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{escape(text or '')}</pre>"
            f"</body></html>"
        )

    # Drop CC in sandbox so Resend doesn't try unverified CC addresses
    note = (
        f" [sandbox → {inbox}; was to {original_to}"
        + (f", cc {', '.join(original_cc)}" if original_cc else "")
        + "]"
    )
    logger.info(
        "EMAIL SANDBOX: redirecting To=%s CC=%s → %s",
        original_to,
        original_cc,
        inbox,
    )
    return subject, new_text, new_html, inbox, None, note


def _smtp_ready() -> bool:
    return bool(
        config.SMTP_USER
        and config.SMTP_PASSWORD
        and config.SMTP_PASSWORD not in {"", "your-app-password"}
    )


def send_email(
    subject: str,
    text: str,
    html: str | None = None,
    *,
    to: str,
    cc: list[str] | None = None,
) -> tuple[bool, str]:
    if not to:
        return False, "Recipient email is required"
    if not config.email_configured() and not _smtp_ready():
        return False, "Email is not configured (set RESEND_API_KEY or Gmail SMTP_PASSWORD)"

    subject, text, html, to, cc, sandbox_note = _apply_sandbox(
        subject, text, html, to=to, cc=cc
    )

    provider = (config.EMAIL_PROVIDER or "resend").lower()
    ok, msg = False, "No email provider configured"

    # Prefer explicit provider; fall back Resend→Gmail on free-tier 403
    if provider == "smtp" and _smtp_ready():
        ok, msg = _send_via_smtp(subject, text, html, to=to, cc=cc)
    elif provider == "resend" and config.RESEND_API_KEY:
        ok, msg = _send_via_resend(subject, text, html, to=to, cc=cc)
        # Resend sandbox/free tier blocks non-owner recipients — use Gmail instead
        if (not ok) and _smtp_ready() and (
            "403" in msg
            or "only send testing emails" in msg.lower()
            or "verify a domain" in msg.lower()
        ):
            logger.warning("Resend blocked recipient; falling back to Gmail SMTP for %s", to)
            ok, msg = _send_via_smtp(subject, text, html, to=to, cc=cc)
            if ok:
                msg = f"{msg} (via Gmail SMTP; Resend needs a verified domain for other recipients)"
    elif _smtp_ready():
        ok, msg = _send_via_smtp(subject, text, html, to=to, cc=cc)
    elif config.RESEND_API_KEY:
        ok, msg = _send_via_resend(subject, text, html, to=to, cc=cc)
    else:
        return False, f"Unknown EMAIL_PROVIDER: {config.EMAIL_PROVIDER}"

    if not ok and "verify a domain" in msg.lower():
        msg += (
            " | Fix: set EMAIL_PROVIDER=smtp and SMTP_PASSWORD to a Gmail App Password "
            "(https://myaccount.google.com/apppasswords), or verify a domain at resend.com/domains."
        )

    if ok and sandbox_note:
        return True, msg + sandbox_note
    return ok, msg


def send_price_drop_email(
    cruise_name: str,
    cruise_url: str,
    old_price: float,
    new_price: float,
    currency: str = "USD",
) -> tuple[bool, str]:
    """
    Send personalized price-drop emails to every configured recipient
    (Cameron, Heather, …) with a matching greeting.
    """
    drop = old_price - new_price
    drop_pct = (drop / old_price * 100) if old_price else 0
    recipients = config.alert_recipients()
    if not recipients:
        return False, "No alert recipients configured"

    messages: list[str] = []
    any_ok = False
    for person in recipients:
        greeting = person["greeting"]
        text = _price_drop_text(
            greeting, cruise_name, cruise_url, old_price, new_price, currency, drop, drop_pct
        )
        html = _price_drop_html(
            greeting, cruise_name, cruise_url, old_price, new_price, currency, drop, drop_pct
        )
        ok, msg = send_email(PRICE_DROP_SUBJECT, text, html, to=person["email"])
        any_ok = any_ok or ok
        messages.append(f"{person['name']}<{person['email']}>: {msg}")
        logger.info("Price-drop to %s ok=%s", person["email"], ok)

    return any_ok, "; ".join(messages)


def send_family_invite_email(
    recipient_name: str,
    recipient_email: str,
    tracker_url: str | None = None,
) -> tuple[bool, str]:
    """
    Welcome invitation for any family member / guest.
    Greeting: Hi [Name]!  ·  To: typed email  ·  always CC: config.INTRO_CC_EMAIL
    """
    name = (recipient_name or "").strip()
    email = (recipient_email or "").strip()
    if not name:
        return False, "Please enter the recipient’s name"
    if not email or "@" not in email:
        return False, "Please enter a valid email address"

    url = (tracker_url or config.DASHBOARD_URL).strip()

    # Always CC the owner (INTRO_CC_EMAIL, set in .env) so they receive a
    # copy of every invitation sent
    cc_set: list[str] = []
    env_cc = (config.INTRO_CC_EMAIL or "").strip()
    if env_cc:
        cc_set.append(env_cc)
    # Don't CC the recipient to themselves
    cc = [c for c in cc_set if c.lower() != email.lower()]

    ok, msg = send_email(
        MOM_INTRO_SUBJECT,
        _family_invite_text(name, url),
        _family_invite_html(name, url),
        to=email,
        cc=cc if cc else None,
    )
    if ok:
        if cc:
            return True, f"Invitation sent successfully to {name} (CC'd to Cameron)!"
        # Edge case: inviting Cameron himself (no self-CC)
        return True, f"Invitation sent successfully to {name}!"
    return False, msg


def send_test_email() -> tuple[bool, str]:
    """Preview dual personalized price-drop emails."""
    return send_price_drop_email(
        cruise_name="Windstar Star Breeze · French Polynesia · May 20, 2027",
        cruise_url=config.DASHBOARD_URL,
        old_price=6516.00,
        new_price=6216.00,
        currency="USD",
    )


def settings_help_url() -> str:
    return "https://resend.com/api-keys"
