# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A single-file Python automation script (`book_swim.py`) that uses Playwright to book the 8:00 AM Indoor Lap Pool Reservations slot at MyTrilogyLife.com for the following day. Runs via cron at **7:58 AM** on Fridays (books Saturday) and Saturdays (books Sunday) — early on purpose, so login finishes before registration opens at 08:00:00 and the script is waiting at the line rather than arriving late. Retries up to 5 times on failure, then sends an email and Slack notification either way.

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
2. **Stage (best effort)** — probes the events list once. **Tomorrow's event is not published until 08:00**, so this normally finds nothing; it is wrapped in a `try` and can never break the run. It costs nothing (the script is waiting anyway) and warms the session.
3. **Hold for the opening bell** — `wait_until_open()` blocks until 08:00:00. A no-op when already past 08:00, so retries and ad-hoc runs pass straight through; capped by `MAX_PREOPEN_WAIT` so a stray early run never hangs. Then `open_event_page(..., patience=EVENT_LISTING_PATIENCE)` fetches the now-published event, re-checking for up to 20s because the publish can land a moment after 08:00:00 — failing there would cost a full ~20s re-login. `find_event_href()` does the whole DOM scan in **one** `page.evaluate()` round-trip (~1.4ms) rather than an `inner_text()` call per anchor, which used to cost seconds.
4. **Find 8 AM slot** — locates a Register/Book button near `"8:00 AM"` text variants; falls back to any Register button if the slot can't be isolated
5. **Ticket wizard step 1** — targets Gary's specific member row (`li.event-registration__members-row:has-text("Gary")`), then `choose_slot()` walks the caller's `preferences` in order (normally 8:00 AM → 8:45 AM) and falls back to the earliest still-bookable option, skipping anything in `avoid`. All of this happens on one page load, so falling back costs nothing. The actual selected slot text (e.g. `"8:00am - 8:40am"`) is captured in `booked_slot_text` by reading the chosen `<option>` text and stripping `" Indoor Pool - Free"`. Uses React's `__reactProps` onChange handler to properly update React state. Every path that ends without a slot selected — missing member row, missing dropdown, or no bookable option — calls `fail()`; the script must never report a booking it cannot name.
6. **Ticket wizard step 2 (payments/agreement)** — checks the "I Agree" checkbox via React props setter, then clicks the *last* Continue button (step 2's button appears after step 1's in the DOM)
7. **Confirm** — clicks any Confirm/Yes/Submit button if present
8. **Verify success** — `classify_result()` (a pure, unit-testable function) inspects the **registration modal's** text, not the whole `body`, since generic phrases live in page furniture. An error phrase (`"sold out"`, `"unable to complete registration"`, `"could not be completed"`) always beats a success phrase. Note: the wizard's `"3.CONFIRMATION"` breadcrumb is rendered on every step including failure pages, and `"thank you"` appears in unrelated furniture — neither is a success indicator.
9. **Independent verification** — reloads the event page and looks for the reservation. This is advisory only: it never fails the run (the booking is already made, and retrying could double-book), but a negative result is called out in the email and Slack message.

`main()` wraps `book_once(preferences, avoid)` in a retry loop (up to `MAX_ATTEMPTS=5`, `RETRY_DELAY=3`s). On any failure, `fail()` saves a screenshot to `last_run.png` and raises `BookingError`. A `BookingError` carrying `terminal=True` (nothing bookable at all) stops the loop immediately rather than burning ~30s per pointless retry. After the loop, sends a failure email + Slack notification and exits with code 1. On success, sends a success email + Slack notification. In both cases `trim_log()` runs last to prune log entries older than 30 days.

**Slot pivot.** The first attempt chases 8:00 exactly as before. Two different things can go wrong, and they are handled at different levels:

- *8:00 already disabled when the dropdown is read* — `choose_slot()` picks 8:45 in the same instant, same page, no reload. Zero delay. This is the common case and needs no retry.
- *8:00 looks open but the site refuses it at submission as sold out* — this is a race (observed 2026-07-24), and the dropdown may still claim 8:00 is open on the next attempt. The `BookingError` carries `sold_out_slot`, `main()` adds it to `avoid`, and every later attempt skips that time entirely rather than re-submitting a booking the site has already refused.

**Gotcha:** the failure email's traceback must be captured with `traceback.format_exc()` *inside* the `except` block. Called after the loop it returns the literal string `"NoneType: None"`, since no exception is being handled at that point.

## Key Constants (top of `book_swim.py`)

| Variable | Value | Notes |
|---|---|---|
| `TARGET_TIME` | `"8:00 AM"` | Preferred slot |
| `FALLBACK_TIME` | `"8:45 AM"` | Second choice, used if 8:00 is unavailable or refused |
| `EVENT_NAME` | `"Indoor Lap Pool Reservations"` | Event name to find |
| `MAX_ATTEMPTS` | `5` | Total tries before giving up |
| `RETRY_DELAY` | `3` | Seconds between retries (kept short — the 8:00 slot sells out 30–90s after opening) |
| `OPEN_TIME` | `08:00:00` | When registration opens; the script stages early and holds until this moment |
| `MAX_PREOPEN_WAIT` | `900` | Safety cap on that hold, so an ad-hoc early run never blocks for hours |
| `EVENT_LISTING_PATIENCE` | `20.0` | Seconds to keep re-checking for the event listing after 08:00, since it publishes at the bell |

## Cron Schedule

```cron
58 7 * * 5,6 /home/gary/projects/swim-booker/run.sh
```

Runs at 7:58 AM Friday and Saturday (books next day's 8 AM slot). Edit with `crontab -e`.

**The job starts early on purpose.** Registration opens at 08:00:00 and the 8:00 slot has been seen selling out within ~7s of that. Logging in takes ~16s and used to happen *after* the window opened, so the script reached the ticket dropdown a median of **25s late** (measured across 10 logged runs).

The events list does not publish tomorrow's event until 08:00 either, so that fetch cannot be front-loaded — but the login can. The script now logs in by ~7:58:16, holds at the gate, and at 08:00:00 has only the listing fetch and Register click left, reaching the dropdown around 08:00:05.

Event IDs are **not** predictable, so the listing fetch cannot be skipped by guessing the URL: consecutive weekend days were +1 apart three times but +5 on 2026-07-04→07-05, and week-to-week jumps run into the thousands.

`run.sh` no longer sleeps; the script needs to be *running* early, not started late.

## React State Workaround

The MyTrilogyLife booking wizard is a React app. Normal DOM events (`.click()`, `.select_option()`) don't reliably update React state. The script works around this by:
- Using `__reactProps` to invoke React's own `onChange`/`onClick` handlers directly via `evaluate()`
- Using the native `HTMLInputElement.prototype.checked` setter before firing events
- Force-clicking disabled Continue buttons via JS when they don't enable within the timeout

## Notifications

Both email and Slack are sent on every run (success and failure).

**Email** — sent via Gmail SMTP (`smtp.gmail.com:587`). `SMTP_USER` is the sender; `NOTIFY_EMAIL` is the primary recipient (defaults to `SMTP_USER`); `dbutler06@comcast.net` is always CC'd. The subject line and body both show the **actual booked slot** (e.g. `Saturday 3:30pm - 4:25pm booked`), not the target time.

**Slack** — posts to the `#swim-booker` channel via an Incoming Webhook. The webhook URL is stored in `.env` as `SLACK_WEBHOOK_URL`. Uses only stdlib (`json` + `urllib.request`) — no additional dependency. Skipped silently if `SLACK_WEBHOOK_URL` is unset. The success message shows the **actual booked slot**, not the target time — e.g. `Swim lane booked! Saturday, June 20 3:30pm - 4:25pm`.

## Logs and Artifacts

- `swim_booker.log` — appended on every run; entries older than 30 days are pruned at the end of each run by `trim_log()`
- `last_run.png` — the final page, whether success or failure. Deleted at the start of every run and if capture fails, so a notification never carries a stale screenshot from a previous run as if it were evidence.
