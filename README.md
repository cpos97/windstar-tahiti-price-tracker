# Cruise Price Tracker

Local web app that tracks cruise booking page prices, stores history, and **emails you automatically when a price drops**.

## Features

- Dashboard to add cruise booking URLs
- Headless browser scraping (works with JS-heavy sites)
- Auto price detection + optional CSS selector override
- SQLite price history + chart
- Background checks on a schedule
- Email alert on any price drop

## Quick start

```bash
cd ~/cruise-price-tracker
source .venv/bin/activate   # already created if you used the setup below

# Configure email alerts
cp .env.example .env
# edit .env with your SMTP settings

python run.py
```

Open **http://127.0.0.1:8765**

### First-time setup (if starting fresh)

```bash
# Install uv if needed: https://docs.astral.sh/uv/
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

cd ~/cruise-price-tracker
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env
python run.py
```

## Email setup (required for drop alerts)

Edit `.env`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password
ALERT_EMAIL=you@gmail.com
CHECK_INTERVAL_MINUTES=60
```

**Gmail:** turn on 2FA, then create an [App Password](https://myaccount.google.com/apppasswords) and use that as `SMTP_PASSWORD`.

Then open **Settings** in the app and click **Send test email**.

## Usage

1. Copy the cruise booking URL from your browser.
2. On the dashboard, paste it with a short name (e.g. “RC Caribbean Mar 2027”).
3. Leave CSS selector blank first — auto-detect usually works.
4. Click **Add cruise** (it checks immediately).
5. Leave the app running so scheduled checks can run and email you.

If auto-detect fails:

1. Open the booking page in Chrome.
2. Right-click the price → **Inspect**.
3. Right-click the element in DevTools → **Copy → Copy selector**.
4. Paste that into the cruise’s CSS selector field and save.
5. **Check price now**.

## 24/7 hourly checks (PC off)

See **[DEPLOY.md](DEPLOY.md)** for full setup.

**Best free option:** GitHub Actions workflow  
`.github/workflows/hourly-price-check.yml` runs `0 * * * *` and emails on drops.

**API for external cron:**

```bash
curl -H "X-Cron-Secret: $CRON_SECRET" https://YOUR-HOST/api/check-prices
```

Local one-shot:

```bash
python scripts/hourly_check.py --refresh-login
```

## Keep it running

The scheduler only runs while the app is up. Options:

```bash
# Simple: leave this terminal open
python run.py

# Or use launchd / a terminal multiplexer on Mac
# Example with nohup:
nohup python run.py > data/tracker.log 2>&1 &
```

## Notes

- Respect the cruise site’s terms of use; this is for personal tracking.
- Some sites block bots or require login — a CSS selector and slower interval help.
- Prices are only emailed on **drops** (not increases).

## Project layout

```
cruise-price-tracker/
  app/           # FastAPI app, scraper, email, scheduler
  templates/     # Dashboard HTML
  static/        # CSS
  data/          # SQLite database
  run.py         # Start server
  .env           # Your secrets (not committed)
```
