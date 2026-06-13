# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A single-file Python automation script (`book_swim.py`) that uses Playwright to book the 8:00 AM Indoor Lap Pool Reservations slot at MyTrilogyLife.com for the following day. Runs via cron at 7:59 AM on Fridays (books Saturday) and Saturdays (books Sunday). Retries up to 5 times on failure, then sends an email and Slack notification either way.

## Running

```bash
# Activate the venv first
source .venv/bin/activate

# Run directly
python3 book_swim.py

# Or via the shell wrapper (what cron calls)
./run.sh
```

The script requires a `.env` file (see `.env.template`). All credentials are loaded from environment variables at startup — the script exits immediately if any required variable is missing.

## Environment Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Architecture

Everything lives in `book_swim.py`. The flow inside `book_once()`:

1. **Login** — navigates to `mytrilogylife.com`, clicks "LOG IN HERE", fills credentials, waits for redirect
2. **Events page** — navigates directly to `/events`
3. **Find tomorrow's event** — two strategies: (A) find `<a>` tag with event name + tomorrow's date string, (B) find a containing cell/row and extract the nearest link
4. **Find 8 AM slot** — locates a Register/Book button near `"8:00 AM"` text variants; falls back to any Register button if the slot can't be isolated
5. **Ticket wizard step 1** — targets Gary's specific member row (`li.event-registration__members-row:has-text("Gary")`), selects the matching time slot via React's `__reactProps` onChange handler to properly update React state
6. **Ticket wizard step 2 (payments/agreement)** — checks the "I Agree" checkbox via React props setter, then clicks the *last* Continue button (step 2's button appears after step 1's in the DOM)
7. **Confirm** — clicks any Confirm/Yes/Submit button if present
8. **Verify success** — checks `body` text for success keywords; fails if still on step 2

`main()` wraps `book_once()` in a retry loop (up to `MAX_ATTEMPTS=5`, `RETRY_DELAY=10`s). On any failure, `fail()` saves a screenshot to `failure_screenshot.png` and raises `BookingError`. After all retries exhausted, sends a failure email + Slack notification and exits with code 1. On success, sends a success email + Slack notification. In both cases `trim_log()` runs last to prune log entries older than 30 days.

## Key Constants (top of `book_swim.py`)

| Variable | Value | Notes |
|---|---|---|
| `TARGET_TIME` | `"8:00 AM"` | Slot to book |
| `EVENT_NAME` | `"Indoor Lap Pool Reservations"` | Event name to find |
| `MAX_ATTEMPTS` | `5` | Total tries before giving up |
| `RETRY_DELAY` | `10` | Seconds between retries |

## Cron Schedule

```cron
59 7 * * 5,6 /home/gary/projects/swim-booker/run.sh
```

Runs at 7:59 AM Friday and Saturday (books next day's 8 AM slot). Edit with `crontab -e`.

`run.sh` contains `sleep 45` so the script effectively starts at **7:59:45 AM**. Cron itself can't schedule below minute precision; the sleep handles the seconds offset.

## React State Workaround

The MyTrilogyLife booking wizard is a React app. Normal DOM events (`.click()`, `.select_option()`) don't reliably update React state. The script works around this by:
- Using `__reactProps` to invoke React's own `onChange`/`onClick` handlers directly via `evaluate()`
- Using the native `HTMLInputElement.prototype.checked` setter before firing events
- Force-clicking disabled Continue buttons via JS when they don't enable within the timeout

## Notifications

Both email and Slack are sent on every run (success and failure).

**Email** — sent via Gmail SMTP (`smtp.gmail.com:587`). `SMTP_USER` is the sender; `NOTIFY_EMAIL` is the primary recipient (defaults to `SMTP_USER`); `dbutler06@comcast.net` is always CC'd.

**Slack** — posts to the `#swim-booker` channel via an Incoming Webhook. The webhook URL is stored in `.env` as `SLACK_WEBHOOK_URL`. Uses only stdlib (`json` + `urllib.request`) — no additional dependency. Skipped silently if `SLACK_WEBHOOK_URL` is unset.

## Logs and Artifacts

- `swim_booker.log` — appended on every run; entries older than 30 days are pruned at the end of each run by `trim_log()`
- `failure_screenshot.png` — overwritten on every run with either a failure state or the success confirmation page
