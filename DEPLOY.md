# Deploy Rep Dashboard to Render

Step-by-step guide to put this tool on the internet so your SMB team can
share one URL instead of running an .exe locally. End result:
`https://your-app-name.onrender.com` gated by a single shared password.

Total setup time: ~20 minutes the first time.

---

## Step 1 — Create the GitHub repo (5 min)

1. Go to https://github.com/new
2. Repo name: `hubspot-rep-dashboard` (anything you want)
3. **Private** (so the code stays internal)
4. Do NOT add a README / .gitignore / license — we already have those
5. Click "Create repository"
6. On the next screen, copy the URL under **"…push an existing repository from the command line"** — it looks like:
   ```
   git@github.com:YourUser/hubspot-rep-dashboard.git
   ```
   (or the `https://...` variant)

---

## Step 2 — Push your code (3 min)

The repo is already initialized locally. Open a terminal in
`Desktop\hubspot-rep-dashboard\` and run:

```
git remote add origin <paste-the-URL-from-step-1>
git branch -M main
git push -u origin main
```

If git asks you to sign in, use a Personal Access Token (Settings → Developer
settings → Personal access tokens → Tokens (classic) → "repo" scope).

> **Double-check that `.env` is NOT pushed.** It's in `.gitignore`, so it
> should be ignored. Confirm by visiting your repo on github.com — you should
> see `app.py`, `templates/`, `static/`, `requirements.txt`, etc., but no
> `.env` file and no `dist/` or `.venv/` folder.

---

## Step 3 — Connect Render (5 min)

1. Sign up / log in at https://render.com (use your work email; GitHub login is fastest).
2. Top right → **New +** → **Blueprint**
3. Connect your GitHub account if asked, then pick `hubspot-rep-dashboard`.
4. Render reads `render.yaml` and shows a preview of the service it'll create.
5. Click **Apply**.

Render starts building. Don't navigate away — the next step needs the new service.

---

## Step 4 — Set the secret env vars (2 min)

Render needs two secrets that we deliberately kept out of the repo:

1. In the Render dashboard, click on your new **rep-dashboard** service.
2. Left sidebar → **Environment**.
3. You'll see `HUBSPOT_TOKEN` and `APP_PASSWORD` listed with empty values.
4. Click each one and paste:
   - **HUBSPOT_TOKEN**: `pat-na1-...` (the same token from your local `.env`)
   - **APP_PASSWORD**: pick a password your team will share. Something like
     `smb-team-2026!` — at least 12 chars, no need to memorize.
5. **Save changes**. Render auto-redeploys.

(Username defaults to `team`. If you want to change it, set the `APP_USERNAME`
env var too.)

---

## Step 5 — Open it (1 min)

After the deploy finishes (~2 minutes), the top of the service page shows a
URL like `https://rep-dashboard-xxxx.onrender.com`. Click it.

You'll see a browser auth popup:
- Username: `team`
- Password: whatever you set in step 4

Then the dashboard loads. **First time:** the backend is already warming up
the cache in the background (logs in Render → "Logs" tab show progress). The
"Refresh data" button works as usual.

---

## Step 6 — Share with your team

Send teammates:
1. The URL: `https://rep-dashboard-xxxx.onrender.com`
2. Username: `team`
3. Password: the one you set

That's it. They open the link, sign in once (browsers remember basic auth),
and use the dashboard.

---

## Free tier vs paid

Render's free tier:
- ✅ Free forever
- ⚠️ Service **sleeps after 15 min of inactivity**. First page load after sleep
  takes ~30 sec to wake up, then it's fast again. Cache survives sleep.
- ⚠️ 512 MB RAM — fine for this tool with the SMB dataset.

If the sleep is annoying, upgrade to **Starter ($7/month)** in the service's
Settings → **Instance Type**:
- Always on (no sleep)
- 512 MB RAM stays the same; bump if you need more

---

## How to deploy updates later

Any time you change the code:

```
git add -A
git commit -m "describe what changed"
git push
```

Render auto-detects the push, rebuilds, and redeploys in ~2 min. Zero downtime
for users.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Auth popup keeps appearing | You typed the wrong password. Check Render → Environment. |
| `HUBSPOT_TOKEN not set` shown | You forgot Step 4, or the env var has whitespace. Re-save. |
| 502 Bad Gateway on first load after sleep | Free tier waking up. Refresh in 30 sec. |
| Refresh data hangs at >10 min | Free tier RAM/CPU is too small. Upgrade to Starter, or pre-warm by setting `WARMUP_ON_STARTUP=true` (already on). |
| "Refresh data" button hits the same person's slow pull every morning | Expected. Or set up a Render cron job (see below) so it runs at 6am automatically. |

---

## Optional: scheduled overnight refresh

If you want the cache automatically refreshed every morning (so even the
first person of the day sees instant data):

1. Render dashboard → **New +** → **Cron Job**
2. Name: `nightly-refresh`
3. Schedule: `0 5 * * *` (5am UTC = midnight CDT)
4. Command:
   ```
   curl -u team:YOUR_PASSWORD -X POST https://rep-dashboard-xxxx.onrender.com/api/refresh?view=all_outbound
   ```
5. Plan: free is fine for cron.

This hits the same refresh endpoint your UI uses, but unattended.
