# 24/7 hourly price checks (PC can be off)

Your tracker uses **Playwright** (real browser) and **SQLite**. That does **not** fit classic Vercel/Netlify serverless well.

For static landing only on free Vercel/Netlify, see **[DEPLOY-VERCEL-NETLIFY.md](DEPLOY-VERCEL-NETLIFY.md)**  
(publish folder: **`public/`** — there is no `dist/` or `build/`).

For 24/7 checks use one of these options instead:

---

## Recommended (free): GitHub Actions hourly cron

Runs on GitHub’s cloud every hour — **works when your Mac is asleep or off**.

### Steps

1. Create a GitHub repo and push this project (do **not** commit `.env`).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|--------|--------|
| `RESEND_API_KEY` | your Resend key |
| `ALERT_EMAIL` | your email address |
| `PERX_USERNAME` | Perx login |
| `PERX_PASSWORD` | Perx password |
| `ID90_EMAIL` | optional |
| `ID90_PASSWORD` | optional |

3. Open **Actions → Hourly cruise price check → Run workflow** once to test.
4. Leave it enabled. Schedule: `0 * * * *` (every hour at :00 **UTC**).

On each run the workflow:

- Logs into Perx (using secrets)
- Scrapes ID90 + Perx
- Updates price history in SQLite (cached between runs)
- Sends **YOUR TAHITI CRUISE DROPPED IN PRICE!** if a fare drops

Workflow file: `.github/workflows/hourly-price-check.yml`

---

## Option B: Render (web UI in the cloud + cron)

Files included: `Dockerfile`, `render.yaml`, `scripts/cron_ping.py`

1. Push repo to GitHub.
2. [Render](https://render.com) → **New → Blueprint** → select repo.
3. Set env vars: `RESEND_API_KEY`, `ALERT_EMAIL`, `PERX_*`, `DASHBOARD_URL=https://your-service.onrender.com/`
4. Cron service calls:

```http
GET https://your-service.onrender.com/api/check-prices
Header: X-Cron-Secret: <CRON_SECRET>
```

Schedule: `0 * * * *`

---

## Option C: Keep app on any host + free external cron

1. Deploy the Docker image (Render, Railway, Fly.io, a VPS).
2. Set `CRON_SECRET` and `DISABLE_LOCAL_SCHEDULER=1`.
3. Create a job at [cron-job.org](https://cron-job.org) or [UptimeRobot](https://uptimerobot.com):

| Field | Value |
|-------|--------|
| URL | `https://YOUR-HOST/api/check-prices` |
| Schedule | every 60 minutes |
| Header | `X-Cron-Secret: YOUR_SECRET` |
| Timeout | at least 5 minutes (scrapes are slow) |

---

## Secured API endpoint

```http
GET|POST /api/check-prices
X-Cron-Secret: <CRON_SECRET>
```

Also accepted:

- `Authorization: Bearer <CRON_SECRET>`
- `?secret=<CRON_SECRET>` (avoid if you can; secrets in URLs get logged)

Sync response includes scrape results and how many price-drop emails fired.

Health check (public, no scrape):

```http
GET /api/health
```

---

## Why not Vercel alone?

`vercel.json` includes a cron path for completeness, but **this Python + Playwright app is not a Vercel serverless function**. Use GitHub Actions or Render/Docker.

---

## Local still works

While your Mac is on:

```bash
python run.py   # local scheduler every CHECK_INTERVAL_MINUTES
# or
python scripts/hourly_check.py --refresh-login
```

Manual secure API test:

```bash
curl -s -H "X-Cron-Secret: $CRON_SECRET" "http://127.0.0.1:8765/api/check-prices"
```
