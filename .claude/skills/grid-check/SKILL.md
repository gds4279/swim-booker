---
name: grid-check
description: Check that the club's real swim-lane grid still matches what swim-booker is configured to book (SCHEDULE in book_swim.py) — catches a re-timed grid, renamed lane labels or a retyped event title before they cost a booking. Use when the user asks to check the grid/schedule/times, after a run reports nothing bookable or the event not found, or as a routine weekly check.
---

# Swim-booker grid check

The club changes its grid and its titles by hand, and each change has surfaced as
a failed booking: the weekday morning moved 6:15→6:00 (2026-08-03), and Tuesday's
title was retyped `Tuesday August, 4` the same day. This check compares the live
grid against `SCHEDULE` so drift is found on a quiet day instead of at 08:00.

## What it can see

`--dry-run` reads **tomorrow only**, and tomorrow's event is published at **08:00
today**. So:

- **Run it after 08:00.** Between 07:45 and 08:00 the dry run holds for the bell
  (fine, just slow). Earlier than that it logs `beyond the 15 min hold cap –
  proceeding now` and then reports the event never appeared — that is "too early",
  not drift.
- **Which grid you get depends on today:** Fri/Sat check the weekend grid; Mon/Wed
  check the Tue/Thu grid the script books; Tue/Thu/Sun check an unbooked weekday,
  using the `DRY_RUN_PROBE` plan (6:00 AM, no fallback). Say which one was checked.
  To cover the weekend grid, run it on a Friday or Saturday.
- **Booked days work too.** When tomorrow is already held the wizard is closed, so
  the dry run reads the event page's `Ticket Prices` list instead and prints
  `Would book at 08:00 (if all open)`. Availability there is as of now — ignore
  `taken`; only the times and labels matter.

## Rules

- Never a live run — only `--check-session` and `--dry-run`. (The guard hook
  enforces this.)
- **Check the session first and stop if it is dead.** A dry run with an expired
  session falls back to the form login, which the site's reCAPTCHA refuses — and
  failed attempts plausibly hurt the server's reputation.
- **Do not edit `SCHEDULE` without the user's go-ahead.** It decides what real runs
  book. Propose the exact change and the evidence; let them decide.

## Steps

1. `python3 book_swim.py --check-session` — must print `Saved session is logged in`.
   If not: stop, and tell the user to refresh the session (README "Saved login session").
2. `python3 book_swim.py --dry-run 2>&1 | tail -40` — roughly 15–25s after 08:00.
3. Read the target weekday from the `Target:` line, and the plan for it from
   `SCHEDULE` (or `DRY_RUN_PROBE` if the weekday is not in `SCHEDULE`) in
   `book_swim.py`.
4. Judge it:

| Dry-run output | Verdict |
|---|---|
| `Would book…: <first preference>` (matched preference) | **OK** |
| `never appeared on the events page` (after 08:00) | **DRIFT — title.** The matcher could not find tomorrow's listing. Find the real title on the events page and compare against the matcher rules in CLAUDE.md ("The event matcher must not guess" / "must not be brittle about punctuation"). Never loosen it into guessing |
| `NOTHING`, and no row starts at the preferred time | **DRIFT — re-timed.** Propose the new time from the grid |
| `NOTHING` / fallback, but the preferred time is listed `taken` (live dropdown, not event page) | **OK — sold out**, not drift |
| Weekend: matched via `fallback`, preferred 8:00 absent from the grid | **DRIFT — re-timed**; the fallback is covering for it |
| Preferred time present but tagged `(not a lap lane – never booked)` | **DRIFT — label.** The lane's name no longer contains `LANE_TYPE` (`"indoor"`). Check whether it's genuinely a class, or a renamed lane |
| Rows tagged `(start time unreadable …)` | **WARN — format.** Only matters for weekend fallback; note it |
| A new row type that looks like lap swimming but lacks "Indoor" (e.g. `Lap Swim`) | **WARN — label.** Would never be booked |
| `Already registered` but `No ticket list found on the event page` | **COULD NOT CHECK** — the page layout changed; `parse_ticket_prices()` needs a look |

5. Compare the grid against the **last confirmed grids** below. A changed time or
   label anywhere in the morning is worth reporting even if today's verdict is OK —
   it often precedes a change to the slot that matters.
6. Report: which day/grid was checked, the verdict, the decisive lines, and — for
   drift — the proposed `SCHEDULE` (or matcher) change, plus the next scheduled run
   it would affect (cron `58 7 * * 1,3,5,6`: Mon→Tue, Wed→Thu, Fri→Sat, Sat→Sun).
7. If the user confirms a grid change, update the baseline below in the same commit
   as any `SCHEDULE` edit, and add a dated line to the timing notes in CLAUDE.md.

## Last confirmed grids

Update when a check confirms a change. Morning rows are the ones that matter.

**Weekday (Tue/Thu) — confirmed 2026-10-14** (Thu 10/15, event page):
`6:00am-6:40am Indoor Pool` · `6:45am-7:25am Indoor Pool` · `7:30am-8:10am Indoor Pool` ·
`8:15am-8:55am Indoor Pool` · `9:00am-9:40am Indoor Lap Pool` · `10:00am-11:00am Indoor Lap` · …
· `5:00pm - 8:10pm Adult Water VolleyBall` (not a lane). Title form: `Indoor Lap Pool Reservation…`
(singular). Earlier the same week (10/13) the dropdown also had `9:00am-10:00am` with no type.

**Weekend (Sat/Sun) — confirmed 2026-10-09** (Sat 10/10, live dropdown):
`8:00am - 8:40am Indoor Pool` · `8:45am - 9:25am Indoor Pool` · `9:30am - 10:10am Indoor Pool` ·
`10:15am - 10:55am Indoor Pool` · `11:00am - 11:40am Indoor Pool` · `11:45am-12:25pm Water Fitness`
(not a lane) · `12:30pm-1:25pm` (no type) · then hourly `Indoor Pool` to 8:30pm. Title form:
`Indoor Lap Pool Reservations…` (plural).
