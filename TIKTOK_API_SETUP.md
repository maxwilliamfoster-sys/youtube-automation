# TikTok official API posting — setup (optional, the fully PC-free path)

This unlocks **server-to-server publishing**: TikTok receives the video and posts it
from their own servers. No browser, no PC uptime — it can run entirely from the cloud
(GitHub Actions). It also has **zero browser-automation fingerprint**, so it's the
safest option for not being flagged.

> ⚠️ **The one catch — app audit.**
> Until your developer app passes TikTok's audit, the Content Posting API can only
> publish videos as **SELF_ONLY (private — visible to just you)**. Public posting
> turns on *after* you submit the app for review and it's approved. So this path is
> only worth setting up if you're willing to apply for audit. Until then, the
> **stealth browser poster + morning batch scheduling is the recommended method**
> (already set up, no extra steps).

---

## What you already have without this (recommended default)

- **Stealth poster** — drives a real, persistent Brave profile that looks human.
- **Morning batch** — the PC wakes once (~06:30), generates both videos, and hands
  them to TikTok's native scheduler. TikTok publishes them at 07:30 and 20:00, so the
  PC can sleep the rest of the day.
- One-time login: `python tiktok_poster.py --login`

That covers "don't get shadowbanned" and "PC mostly off." Only do the steps below if
you want the PC **completely** off / cloud posting.

---

## Steps to enable the official API

1. **Create a developer app**
   - Go to https://developers.tiktok.com/ → log in → **Manage apps** → **Connect an app**.
   - Add the **Content Posting API** product. Add the **Login Kit** product.
   - Under scopes, request: `user.info.basic`, `video.publish`, `video.upload`.

2. **Add a redirect URI** (exactly this, in the app's Login Kit settings):
   ```
   http://localhost:8723/callback
   ```

3. **Copy your credentials** into `.env`:
   ```
   TIKTOK_CLIENT_KEY=your_client_key
   TIKTOK_CLIENT_SECRET=your_client_secret
   TIKTOK_REDIRECT_URI=http://localhost:8723/callback
   TIKTOK_API_ENABLED=1
   # Keep SELF_ONLY until your app is audited, then switch to PUBLIC_TO_EVERYONE:
   TIKTOK_API_PRIVACY=SELF_ONLY
   ```

4. **Authorise once**:
   ```
   python tiktok_oauth.py
   ```
   A TikTok consent screen opens; approve it. Tokens are saved to `tiktok_tokens.json`
   and refresh automatically from then on.

5. **Test a (private) post**:
   ```
   python tiktok_api_poster.py output\your_video.mp4 "Test #truecrime"
   ```
   It should appear in your profile as **private**. Confirm it worked.

6. **Apply for audit** (to allow public posts)
   - In the developer portal, submit the app for review. Once approved, set
     `TIKTOK_API_PRIVACY=PUBLIC_TO_EVERYONE` in `.env`.

7. **Cloud (optional)** — once audited, add these as GitHub Secrets and the API path
   works from GitHub Actions with the PC fully off:
   `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`,
   `TIKTOK_API_ENABLED`, plus the contents of `tiktok_tokens.json` (as a secret the
   workflow writes to disk before running).

---

## How the code decides which method to use

`main_documentary._post()`:
1. If `TIKTOK_API_ENABLED=1` **and** valid tokens exist → **official API**.
2. Otherwise → **stealth browser poster** (`tiktok_poster.py`), using native
   scheduling when run via `--batch`.

So enabling the API is non-destructive: if anything about it isn't ready, the pipeline
automatically falls back to the working browser method.
