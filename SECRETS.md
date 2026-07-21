# GitHub Secrets checklist

**Never commit `.env` or put API keys / passwords in source code.**

Local secrets live in `.env` (gitignored).  
Cloud hourly runs read **GitHub Actions Secrets**.

## Required for hourly checks + family emails (Gmail)

| Secret name | Purpose | Example |
|-------------|---------|---------|
| `EMAIL_PROVIDER` | Use Gmail so anyone can receive mail | `smtp` |
| `SMTP_USER` | Your Gmail address | `you@gmail.com` |
| `SMTP_PASSWORD` | Gmail **App Password** (16 chars) | *(from Google App Passwords)* |
| `PERX_USERNAME` | Log in to Perx for rates | your Perx email |
| `PERX_PASSWORD` | Perx password | *(secret)* |

Optional SMTP extras (defaults work for Gmail):

| Secret | Default |
|--------|---------|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `EMAIL_FROM` | `Windstar Tahiti Price Tracker <you@gmail.com>` |

## Recommended

| Secret name | Purpose |
|-------------|---------|
| `ALERT_RECIPIENTS` | `you@gmail.com:You,family1@example.com:Name1,family2@example.com:Name2` |
| `DEPARTURE_DATE` | `2027-05-20` |
| `DASHBOARD_URL` | Live tracker link in emails |
| `ID90_EMAIL` / `ID90_PASSWORD` | ID90 login if needed |
| `INTRO_CC_EMAIL` | CC on invites (defaults to `ALERT_EMAIL`) |
| `SITE_PASSWORD` | HTTP Basic Auth password protecting the whole site |
| `EMAIL_SANDBOX` | `0` for real delivery (`1` redirects all mail to you for testing) |

## Optional

| Secret name | Purpose |
|-------------|---------|
| `RESEND_API_KEY` | Only if you verify a domain on Resend later |
| `ALERT_EMAIL` | Legacy single recipient |
| `CRON_SECRET` | Only if hosting `/api/check-prices` |

## How to add secrets

1. https://github.com/cpos97/windstar-tahiti-price-tracker/settings/secrets/actions  
2. **New repository secret** for each row  
3. **Actions → Hourly cruise price check → Run workflow** to test  

## Local `.env` (Mac dashboard)

Same variable names as above. File must stay **uncommitted**.
