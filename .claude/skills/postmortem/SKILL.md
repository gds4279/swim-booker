---
name: postmortem
description: Diagnose a swim-booker run — why it failed, or whether a reported failure (or success) is real. Use when the user says the booker failed, didn't book, sent a FAILED email/Slack, or asks to review the logs; optional argument is the run date (YYYY-MM-DD), default the latest scheduled run.
argument-hint: "[YYYY-MM-DD]"
---

# Swim-booker postmortem

Diagnose one scheduled run. The order matters: **establish whether a lane is
actually booked before deciding what went wrong** — this project's two worst
incidents were a false SUCCESS (2026-07-24) and a false FAILED (2026-07-29), and
"no member row for Gary" usually means *already booked*.

CLAUDE.md is the reference for every invariant mentioned here; read the relevant
section before proposing a code change.

## Hard rules

- **Never make a live run.** No `run.sh`, no `book_swim.py` without `--dry-run` /
  `--check-session`, no calling `main()`/`book_once()`/`send_email()`/`send_slack()`.
  The project's PreToolUse guard blocks these anyway; don't try to route around it.
  If a live run is genuinely needed, ask the user to run `! ./run.sh` themselves.
- **Never retry the login form to "see what happens".** The site's reCAPTCHA scores
  this server's reputation, and every failed attempt plausibly lowers it. On
  2026-10-14 ten extra attempts during diagnosis all failed and helped nothing. Use
  `--check-session` (opens one page, no login attempt) instead.
- **Never read credentials** — the dotenv file or the saved session. To inspect the
  session, use `--check-session`. To see which cookies an export holds, print names
  and domains only, never values.
- Commit messages that mention those files or `main()` trip the guard: write the
  message to a file in the scratchpad and `git commit -F` it.

## Steps

### 1. Pull the run

```bash
python3 .claude/skills/postmortem/scripts/run_log.py $ARGUMENTS
```

Prints the run's log block (tracebacks included), its outcome and attempt count,
and the last 10 runs' outcomes. A first failure after a long streak points at a
site-side change; repeated failures point at something persistent.

Note the **target date** (the `Booking … for YYYY-MM-DD` line) — it is the day
*after* the run.

### 2. Look at the screenshots

Read `first_failure.png` first — it is the attempt that actually went wrong.
`last_run.png` is the final attempt, which retries overwrite; on 2026-07-29 it showed
a *successful* booking under a FAILED email. Both are deleted at the start of every
run, so they only describe the most recent run. Read on-page messages literally: the
site's own error text beats the script's guess (`credentials may be wrong` on
2026-10-14 was really `Score does not meet threshold.`).

### 3. Establish ground truth: is the lane booked?

Do this before diagnosing. Any one of these settles it:

- **Same day as the run, after 08:00:** `python3 book_swim.py --dry-run` targets
  tomorrow = the run's target date. It never books or notifies. It prints
  `Already registered for <slot>` if a ticket is held, otherwise the slot grid. Before
  08:00 it holds until the bell (up to 15 min), so don't start it between 07:45 and 08:00.
  On a later day it checks the wrong date — use the next option.
- **Gmail** (if the Gmail tools are available): the club emails its own confirmation.
  Search `"Indoor Lap Pool" newer_than:3d -"[Swim Booker]"`, and `"[Swim Booker]"`
  for what the script itself reported.
- Otherwise ask the user to check the club's site.

If a lane is booked, the question becomes "why did the script report failure" (or
the user booked by hand — ask), not "why didn't it book".

### 4. Classify

Match the run's error text. Most specific first:

| Signature in the log / screenshot | What it means | What to do |
|---|---|---|
| `reCAPTCHA bot check` / `Score does not meet threshold` | Form login refused as a bot (since 2026-10-14). Only reached when the saved session failed | `--check-session`. If not logged in, the user re-exports cookies (README "Saved login session") and runs `--import-cookies` |
| `Saved session is no longer logged in` | Session expired or was revoked | Same as above. The Remember-Me cookie in the 2026-10-14 import lasts to 2027-11-18; much earlier expiry means the site revoked it (password change, logout everywhere) |
| `Timed out after submitting login – credentials may be wrong` | Form login failed for a reason other than reCAPTCHA | Screenshot shows the reason. Only now consider the password |
| `Page.goto: Timeout` on the first navigation | Site slow or down | Transient if other attempts got further. If every attempt: site outage — nothing to fix |
| `never appeared on the events page` | Matcher found no listing for the date | Title retyped? (2026-08-03: `Tuesday August, 4`.) Compare a real title against the matcher rules in CLAUDE.md. **Never loosen it to guess from neighbours** |
| `nothing suitable available` / terminal, no bookable slot | Preferred time not in the dropdown | Grid re-timed? (2026-08-03 moved 6:15→6:00.) `--dry-run` and compare against `SCHEDULE` |
| `no member row for Gary` | Usually: ticket already held | Step 3 decides. Only a broken wizard if no ticket is held |
| `sold out` / `sold_out_slot` | Lost the race at 08:00 | Expected occasionally. Check timings against the CLAUDE.md budget (dropdown ~08:00:18–20) before blaming code |
| `Not on confirmation page` / inconclusive wizard | Verification couldn't read a verdict | Step 3 decides; see "Never wait on a page-wide selector" in CLAUDE.md |
| Nothing in the log for the date | Cron didn't fire, or the machine was off | `crontab -l`, `last -x reboot`, `journalctl -u cron --since <date>` |

Not in the table? It's new — diagnose from the screenshot and log, and add a row
here once it's understood.

### 5. Fix, or hand back

- **Code fix**: follow CLAUDE.md's invariants. Verify with `--dry-run` /
  `--check-session` and offline tests of pure functions — never a live run. Record
  the incident in CLAUDE.md (the project keeps dated postmortems there) and add the
  signature to the table above.
- **User action** (re-import cookies, book by hand): give exact steps.
- **Nothing to fix** (lost race, site outage): say so plainly.

### 6. Report

Lead with the verdict, then:

1. **Booked or not**, and how that was established (step 3).
2. **Cause**, quoting the decisive log line or on-screen text with its timestamp.
3. **What was changed / what the user must do.**
4. **Next scheduled run** — cron is `58 7 * * 1,3,5,6` (Mon→Tue, Wed→Thu, Fri→Sat,
   Sat→Sun) — and whether it is at risk until the fix lands.
5. Anything unverified, stated as unverified.
