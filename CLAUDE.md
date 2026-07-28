# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A single-file Python automation script (`book_swim.py`) that uses Playwright to book an Indoor Lap Pool lane at MyTrilogyLife.com for the following day. Runs via cron at **7:58 AM** on Monday, Wednesday, Friday and Saturday — early on purpose, so login finishes before registration opens at 08:00:00 and the script is waiting at the line rather than arriving late. Retries up to 5 times on failure, then sends an email and Slack notification either way.

What it books is driven by `SCHEDULE`, keyed on the weekday of the day being **booked** (Mon=0 … Sun=6), not the day the script runs:

| Day booked | Prefers | If unavailable |
|---|---|---|
| Tuesday, Thursday | 6:15 AM | Books **nothing**. Any later lane misses the start of the work day, so a "booked" email for one is worse than a failure notice |
| Saturday, Sunday | 8:00 AM → 8:45 AM | A later lane, **no earlier than 8:00 AM** and **no later than 9:30 AM** |

`--dry-run` logs in, opens the wizard far enough to read Gary's slot dropdown, prints the grid and what the current config *would* book, then exits without selecting anything. It never books. Started before 08:00 it goes through the full opening-bell hold, which is the only way to see a grid before the day sells out.

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
2. **Stage (best effort)** — probes the events list once. **Tomorrow's event is not open until 08:00**, so this normally finds nothing; it is wrapped in a `try` and can never break the run. It costs nothing (the script is waiting anyway) and warms the session.
3. **Hold for the opening bell** — `wait_until_open()` blocks until 08:00:00. A no-op when already past 08:00, so retries and ad-hoc runs pass straight through; capped by `MAX_PREOPEN_WAIT` so a stray early run never hangs. (`seconds_until_open()` is the pure decision half and must stay free of logging — it is called more than once per run.) Then either branch runs, never neither:
   - anything staged in step 2 was fetched *before* the window opened, so it is **always** re-fetched here. Do not gate this on whether the wait actually happened: staging can straddle 08:00:00, and the earlier `and waited` version fell through both branches and proceeded on a stale pre-open page with a dead Register button.
   - otherwise `open_event_page(..., patience=EVENT_LISTING_PATIENCE)` fetches the now-published event, re-checking for up to 20s because the publish can land a moment after 08:00:00 — failing there would cost a full ~20s re-login.

   `find_event_href()` does the whole DOM scan in **one** `page.evaluate()` round-trip (~1.4ms) rather than an `inner_text()` call per anchor, which used to cost seconds.
4. **Find the target slot** — locates a Register/Book button near the first preference's text variants; falls back to any Register button **only while on an `/events/<id>` page**. On the listing, "any Register button" belongs to a different event entirely.
5. **Ticket wizard step 1** — targets Gary's specific member row (`li.event-registration__members-row:has-text("Gary")`), then `choose_slot()` walks the caller's `preferences` in order and, if `any_open`, falls back within bounds (see below), skipping anything in `avoid`. All of this happens on one page load, so falling back costs nothing. The actual selected slot text (e.g. `"8:00am - 8:40am"`) is captured in `booked_slot_text` by reading the chosen `<option>` text and stripping `" Indoor Pool - Free"`. Uses React's `__reactProps` onChange handler to properly update React state. Every path that ends without a slot selected — missing member row, missing dropdown, or no bookable option — calls `fail()`; the script must never report a booking it cannot name.
6. **Ticket wizard step 2 (payments/agreement)** — checks the "I Agree" checkbox via React props setter, then clicks the *last* Continue button (step 2's button appears after step 1's in the DOM)
7. **Confirm** — clicks any Confirm/Yes/Submit button if present
8. **Verify success** — `classify_result()` (a pure, unit-testable function) inspects the **registration modal's** text, not the whole `body`, since generic phrases live in page furniture. An error phrase (`"sold out"`, `"unable to complete registration"`, `"could not be completed"`) always beats a success phrase. Note: the wizard's `"3.CONFIRMATION"` breadcrumb is rendered on every step including failure pages, and `"thank you"` appears in unrelated furniture — neither is a success indicator.
9. **Independent verification** — reloads the event page and looks for the reservation. This is advisory only: it never fails the run (the booking is already made, and retrying could double-book), but a negative result is called out in the email and Slack message.

`main()` wraps `book_once(preferences, avoid)` in a retry loop (up to `MAX_ATTEMPTS=5`, `RETRY_DELAY=3`s). On any failure, `fail()` saves a screenshot to `last_run.png` and raises `BookingError`. A `BookingError` carrying `terminal=True` (nothing bookable at all) stops the loop immediately rather than burning ~30s per pointless retry. After the loop, sends a failure email + Slack notification and exits with code 1. On success, sends a success email + Slack notification. In both cases `trim_log()` runs last to prune log entries older than 30 days.

**Slot pivot.** The first attempt chases 8:00 exactly as before. Two different things can go wrong, and they are handled at different levels:

- *8:00 already disabled when the dropdown is read* — `choose_slot()` picks 8:45 in the same instant, same page, no reload. Zero delay. This is the common case and needs no retry.
- *8:00 looks open but the site refuses it at submission as sold out* — this is a race (observed 2026-07-24), and the dropdown may still claim 8:00 is open on the next attempt. The `BookingError` carries `sold_out_slot`, `main()` adds it to `avoid`, and every later attempt skips that time entirely rather than re-submitting a booking the site has already refused.

**Choosing a slot.** `choose_slot()` enforces three rules, all of which have cost a real run:

- **Lap lanes only.** Not every ticket in a lap-pool event is lap swimming — the weekday grid also offers `Water Fitness` (a class) in the same dropdown, plus one slot with no type at all. `is_lane()` requires `LANE_TYPE` (`"indoor"`) in the option text, and it applies to preference matches too, not just fallbacks: a `Water Fitness` ticket at exactly 8:00 AM is the wrong booking, not a lesser one. A preference rejected on type logs a warning, so a site-side rename surfaces as a reason rather than a silence.
- **Fallback never goes earlier than the first preference.** Weekday grids start at 6:15 AM, so an unbounded "earliest open slot" answers a gone 8:00 with a 6:15 lane. The lower bound is derived from `preferences[0]`; it preserves what "earliest open" always meant back when every grid happened to start at 8:00.
- **Fallback never goes later than `latest`** (9:30 AM on weekends). Without it a busy Saturday quietly books an 8:30 PM lane and calls it success.

A slot whose start time will not parse is skipped, never guessed at. When `any_open` is false (weekdays) the fallback loop does not run at all.

**The event matcher must not guess.** `_FIND_EVENT_JS` matches only a **single anchor** whose own text names both the event and the date and whose href is `/events/<id>`. There used to be a broader fallback that scanned `td, li, div` for a container mentioning both and took an anchor from it. It cannot work: `querySelectorAll` returns outermost-first, so the first "match" is a whole-page wrapper and the anchor it yields has nothing to do with the date that matched. It returned the top-nav **EVENTS** link (`/events`) for an unopened day — and once restricted to `/events/<id>` links it returned *a different day's event*, which is worse, because a plausible URL books the wrong day. Verified 2026-07-28: Thursday 7/30 resolved to Tuesday's event `1849591`. **If this ever stops matching, fail loudly rather than reinstating a guess.**

`open_event_page()` independently re-checks that it actually landed on `/events/<id>` before returning a URL. Returning `page.url` unvalidated is what broke the 2026-07-28 run: `event_url` became the bare listing URL, which is not `None`, so the caller treated the listing as the event page, refreshed *it* at 08:00:00 and clicked a Register button belonging to an unrelated event. Both layers are deliberate — one stops a wrong URL being produced, the other stops a wrong URL being used.

**Gotcha:** the failure email's traceback must be captured with `traceback.format_exc()` *inside* the `except` block. Called after the loop it returns the literal string `"NoneType: None"`, since no exception is being handled at that point.

## Key Constants (top of `book_swim.py`)

| Variable | Value | Notes |
|---|---|---|
| `SCHEDULE` | `{1, 3, 5, 6}` | What to book, keyed on the weekday **being booked** (Mon=0 … Sun=6). Tue/Thu: `["6:15 AM"]`, `any_open=False`. Sat/Sun: `["8:00 AM", "8:45 AM"]`, `any_open=True`, `latest=9:30 AM` |
| `DRY_RUN_PROBE` | `["6:15 AM"]`, `any_open=False` | The plan `--dry-run` assumes for a day not in `SCHEDULE`, so recon works on any day. Never used by a real run |
| `EVENT_NAME` | `"Indoor Lap Pool Reservation"` | **Singular on purpose** — weekday events are titled `…Reservation`, weekend ones `…Reservations`. The singular stem is a substring of both. Do not "correct" it |
| `LANE_TYPE` | `"indoor"` | Substring an option must contain to count as a lap lane, filtering out `Water Fitness` |
| `MAX_ATTEMPTS` | `5` | Total tries before giving up |
| `RETRY_DELAY` | `3` | Seconds between retries (kept short — the 8:00 slot sells out 30–90s after opening) |
| `OPEN_TIME` | `08:00:00` | When registration opens — *not* a swim time. Confirmed from the site's own text: "Reservations may be made at 8:00am the day prior." The two were the same number while this was weekend-only; 6:15 decoupled them. Never "fix" it to a swim time |
| `MAX_PREOPEN_WAIT` | `900` | Safety cap on that hold, so an ad-hoc early run never blocks for hours |
| `EVENT_LISTING_PATIENCE` | `20.0` | Seconds to keep re-checking for the event listing after 08:00, since it publishes at the bell |

## Cron Schedule

```cron
58 7 * * 1,3,5,6 /home/gary/projects/swim-booker/run.sh
```

Runs at 7:58 AM Monday, Wednesday, Friday and Saturday — each booking the *next* day: Mon→Tue, Wed→Thu, Fri→Sat, Sat→Sun. Edit with `crontab -e`.

The cron day numbers and the `SCHEDULE` keys are the same digits (1, 3, 5, 6), which is a coincidence worth understanding before changing either: cron numbering is Python's `weekday()` plus one, and the day booked is always the day after the run, so the two offsets cancel. Change one and you must re-derive the other rather than copying it.

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
