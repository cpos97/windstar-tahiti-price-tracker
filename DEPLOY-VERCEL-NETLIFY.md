# Vercel / Netlify — what free tiers can do

## Important (please read)

This project is **not** a Node/React static SPA with `process.env`.

| Piece | Technology | Free Vercel/Netlify? |
|-------|------------|----------------------|
| Landing page | HTML in `public/` | **Yes** |
| Dashboard + charts + invite form | Python FastAPI + Jinja | **No** (needs a real server) |
| Price scraping | Playwright (real browser) | **No** (too heavy / long for serverless) |
| SQLite history | Local/cloud disk | **No** on pure serverless |
| Hourly cron | GitHub Actions (already set up) | **Yes** (already running) |

**You do not need Vercel/Netlify for 24/7 price emails.** That already works via GitHub Actions.

---

## 1. Config files added

| File | Purpose |
|------|---------|
| `vercel.json` | Publish static site from `public/` |
| `netlify.toml` | Publish static site from `public/` |
| `public/index.html` | Beautiful family landing / status page |

---

## 2. Environment variables (`process.env` vs Python)

This backend is **Python**, so secrets are read with:

```python
os.getenv("SMTP_PASSWORD")   # same idea as process.env.SMTP_PASSWORD in Node
```

Configured in `app/config.py` via `.env` (local) or platform env / GitHub Secrets (cloud).

**Never put keys in source files.**  
See `SECRETS.md` for the full list (`SMTP_*`, `PERX_*`, `RESEND_API_KEY`, etc.).

If you deploy the **static** `public/` page only, it has **no secrets** (safe on free static hosts).

---

## 3. Where is the “build” / deploy folder?

| What you want | Folder to deploy | How to run |
|---------------|------------------|------------|
| Free static landing on Vercel/Netlify | **`public/`** | Deploy that directory (configs already point here) |
| Full interactive tracker | **project root** (not a `dist/` or `build/`) | `python run.py` or Docker (`Dockerfile`) |
| 24/7 price scrape + email | **no deploy folder** | GitHub Actions workflow (already live) |

There is **no** `dist/`, `build/`, or `out/` folder.  
This app is not a compiled frontend; the runnable app is the **repository root**.

---

## 4. How to deploy the static landing (optional)

### Vercel
1. Import the GitHub repo  
2. Framework: Other  
3. Output directory: `public`  
4. Deploy  

### Netlify
1. Import the GitHub repo  
2. Publish directory: `public`  
3. Deploy  

---

## 5. Full tracker in the cloud (not Vercel free)

Use **Render** + `Dockerfile` / `render.yaml` (see `DEPLOY.md`), or keep the dashboard on your Mac and let **GitHub Actions** handle hourly checks.

---

## Summary

- **Vercel/Netlify free** → deploy **`public/`** only (landing page).  
- **Full app** → **root** of this project (Python), not a build folder.  
- **Hourly emails when Mac is off** → already handled by **GitHub Actions** (no Vercel needed).
