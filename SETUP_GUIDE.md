# Stock Screener: Setup Guide

One-time setup, about 20 minutes. No coding needed. After this, the website
updates itself every weekday at about 6 PM IST.

Your website will be at: **https://paltrivedi04.github.io/stock-screener/**

Never paste a password or key into a chat. Keys only go into GitHub's
**Secrets** page (Step 4), where nobody can see them, even though the website is public.

---

## Step 1: Create the project on GitHub (2 min)

1. Sign in to https://github.com as **Paltrivedi04**.
2. Open https://github.com/new
3. **Repository name:** `stock-screener` (exactly this, lowercase)
4. Choose **Public**.
5. Tick **Add a README file**.
6. Click **Create repository**.

## Step 2: Upload the files (3 min)

1. Unzip `stock-screener.zip` on your computer. You'll see a folder `stock-screener`.
2. On your new GitHub page, click **Add file → Upload files**.
3. Open the unzipped folder and drag these into the GitHub page, all together:
   - the `screener` folder
   - the `docs` folder
   - `requirements.txt`
   - `README.md`
   - `SETUP_GUIDE.md`
4. Wait until every file shows in the list, then click **Commit changes**.

## Step 3: Add the daily timer file (2 min)

Folders starting with a dot are often skipped when dragging, so create this one by hand.

1. Click **Add file → Create new file**.
2. In the name box, type exactly: `.github/workflows/daily.yml`
   (typing each `/` creates a folder automatically).
3. Open `daily.yml` from the unzipped folder (inside `.github/workflows`) with Notepad,
   copy everything, and paste it into the big box on GitHub.
   If you can't see that folder, copy the text from the end of this guide instead.
4. Click **Commit changes**, then **Commit changes** again.

## Step 4: Add your InvestSights key (3 min)

1. Go to https://investsights.in/account/connected-apps and create a new key.
   It starts with `usk_`. Copy it.
2. On GitHub, open your `stock-screener` project → **Settings** (top menu).
3. Left menu: **Secrets and variables → Actions**.
4. Click **New repository secret**.
   - **Name:** `INVESTSIGHTS_KEY`
   - **Secret:** paste the key
   - Click **Add secret**.

## Step 5: Switch on the website (1 min)

1. Still in **Settings**, left menu: **Pages**.
2. Under **Build and deployment → Source**, choose **Deploy from a branch**.
3. Branch: **main**, folder: **/docs**. Click **Save**.

## Step 6: Run it for the first time (10 min)

1. Click the **Actions** tab at the top.
2. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
3. On the left, click **Daily screener update**.
4. Click **Run workflow** (right side) → **Run workflow**.
5. Wait for the green tick (about 8 to 12 minutes; it runs slowly on purpose to stay
   within InvestSights' free limits).
6. Open **https://paltrivedi04.github.io/stock-screener/**
   (the very first time, the website can take a few extra minutes to appear).

If you see a red cross instead of a green tick, click it, take a screenshot,
and share it with Claude.

## Step 7: Email alerts (5 min, do this last)

1. Turn on **2-Step Verification** for paltrivedi04@gmail.com:
   https://myaccount.google.com/security
2. Create an app password: https://myaccount.google.com/apppasswords
   Name it `Stock Screener` and copy the 16-letter password.
3. On GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
   Add these three, one by one:

   | Name | Secret |
   |---|---|
   | `GMAIL_USER` | `paltrivedi04@gmail.com` |
   | `GMAIL_APP_PASSWORD` | the 16-letter app password |
   | `EMAIL_TO` | `paltrivedi04@gmail.com` |

4. Emails arrive on days when a company is added or removed.
   (The first run sends the starting list once email is set up.)

---

## Changing a rule later

1. On GitHub, open `screener` → `config.py`.
2. Click the pencil icon (Edit).
3. Change a number, for example `ROE_MIN = 18` to `ROE_MIN = 20`.
4. Click **Commit changes**. The next daily run uses the new rule.

## What the website shows

- **Shortlist:** companies passing every rule, with price, ratios, cash flow,
  concall and presentation links, and a link to each company's Screener.in page.
- **Today's changes:** added and removed companies, with the reason for each removal.
- **History:** every day's additions and removals.
- **Check manually:** companies where a number is missing, so they aren't wrongly dropped.
- **Near misses:** companies failing just one detailed rule.
- **Data checks:** a daily sample compared against NSE and Yahoo Finance.

## Good to know

- GitHub sometimes starts scheduled runs 5 to 20 minutes late.
- On market holidays, the list simply stays the same.
- If NSE or Yahoo doesn't respond on a day, the run continues with the other sources
  and the website shows a note.
- InvestSights' free plan allows 100 requests a day. The screener stops at 95 and
  reuses recent data if it ever gets close.

---

## Text of `.github/workflows/daily.yml` (for Step 3)

```yaml
name: Daily screener update

on:
  schedule:
    # 12:30 UTC = 6:00 PM IST, Monday to Friday
    - cron: "30 12 * * 1-5"
  workflow_dispatch: {}   # lets you run it any time with the "Run workflow" button

permissions:
  contents: write

concurrency:
  group: daily-screener
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install
        run: pip install -r requirements.txt

      - name: Run the screener
        env:
          INVESTSIGHTS_KEY: ${{ secrets.INVESTSIGHTS_KEY }}
          GMAIL_USER: ${{ secrets.GMAIL_USER }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          EMAIL_TO: ${{ secrets.EMAIL_TO }}
          SITE_URL: https://${{ github.repository_owner }}.github.io/${{ github.event.repository.name }}/
        run: python -m screener.main

      - name: Save results to the website
        run: |
          git config user.name "stock-screener-bot"
          git config user.email "actions@users.noreply.github.com"
          git add docs/data data
          if git diff --cached --quiet; then
            echo "Nothing changed"
          else
            git commit -m "Daily update $(date -u +%Y-%m-%d)"
            git push
          fi
```
