# Swim Booker

Automated script to book an indoor lap pool lane at MyTrilogyLife.com for the next day.

## How It Works

1. Logs into MyTrilogyLife.com using stored credentials
2. Navigates to the Events page and finds the "Indoor Lap Pool Reservation" event for tomorrow
3. Clicks Register, selects the preferred slot for Gary's member row
4. Completes the 3-step wizard (Tickets → Payments → Confirmation)
5. Verifies the booking — first from the wizard, then from the event page, which prints the ticket you hold and is the authority if the two disagree
6. Sends an email (with CC) and a Slack notification with the result and a screenshot
7. Prunes log entries older than 30 days

Runs automatically at **7:58 AM every Monday, Wednesday, Friday and Saturday** via cron, booking the next day's slot. It starts early on purpose: logging in takes ~16s, so the script gets that done first and then waits at the line until registration opens at exactly 08:00:00. (The event only becomes reachable at 8:00, so that fetch cannot be done early — the script re-checks for it for up to 20s.) Retries up to 5 times on failure.

### What gets booked

`SCHEDULE` in `book_swim.py` is keyed on the weekday of the day being **booked**, not the day the script runs:

| Run day | Day booked | Prefers | If unavailable |
|---|---|---|---|
| Monday | Tuesday | 6:15 AM | Books **nothing** — a later weekday lane misses the start of the work day |
| Wednesday | Thursday | 6:15 AM | Books **nothing** |
| Friday | Saturday | 8:00 AM, then 8:45 AM | The next open lane, **no earlier than 8:00 AM** and **no later than 9:30 AM** |
| Saturday | Sunday | 8:00 AM, then 8:45 AM | Same |

Only lanes count. The dropdown also lists `Water Fitness`, which is a class, not a lane; it is never booked, even at a preferred time.

### Dry run

```bash
python3 book_swim.py --dry-run
```

Logs in, opens the wizard far enough to read the slot grid, prints it along with what the current config *would* book, and exits without selecting anything. Start it before 07:58 and it goes through the full opening-bell hold — the only way to see a grid before the day sells out.

## Setup

### Prerequisites

- Python 3.8+
- A Gmail account with an [App Password](https://support.google.com/accounts/answer/185833) for notifications
- A Slack Incoming Webhook URL for the target channel

### Install

```bash
git clone https://github.com/gds4279/swim-booker.git
cd swim-booker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Configure

Create a `.env` file in the project root:

```env
MYTRILOGY_USERNAME=your_username
MYTRILOGY_PASSWORD=your_password
SMTP_USER=your_gmail@gmail.com
SMTP_APP_PASSWORD=your_gmail_app_password
NOTIFY_EMAIL=notification_email@gmail.com  # optional, defaults to SMTP_USER
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

## Usage

Run manually:

```bash
python3 book_swim.py
# or
./run.sh
```

The cron schedule (`crontab -e`) is:

```
58 7 * * 1,3,5,6 /home/gary/projects/swim-booker/run.sh
```

This runs at 7:58 AM on Monday (books Tuesday), Wednesday (books Thursday), Friday (books Saturday) and Saturday (books Sunday). `run.sh` activates the `.venv` and launches the script immediately — it needs to be *running* before 8:00, not started at 8:00. `book_swim.py` handles the precise timing itself, holding until 08:00:00 before it clicks Register.

## Notifications

On every run (success or failure) the script sends:

- **Email** — to `NOTIFY_EMAIL`, CC'd to `dbutler06@comcast.net`, with a screenshot attached
- **Slack** — a message to the configured webhook channel

## Files

| File | Purpose |
|------|---------|
| `book_swim.py` | Main script |
| `run.sh` | Shell wrapper (used by cron; activates venv and launches immediately — the script does its own timing) |
| `requirements.txt` | Python dependencies |
| `.env` | Credentials (not committed) |
| `swim_booker.log` | Rolling log of all runs (pruned to 30 days) |
| `last_run.png` | Screenshot of the final page, success or failure (removed at the start of each run) |
| `first_failure.png` | Screenshot of the *first* failed attempt, kept intact while retries overwrite `last_run.png`. Failure emails attach both |

## Configuration

Key constants at the top of `book_swim.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULE` | Tue/Thu 6:15; Sat/Sun 8:00 → 8:45 | What to book per day, keyed on the weekday being booked (Mon=0 … Sun=6) |
| `DRY_RUN_PROBE` | `6:15 AM`, no fallback | The plan `--dry-run` assumes for a day not in `SCHEDULE`; never used by a real run |
| `EVENT_NAME` | `"Indoor Lap Pool Reservation"` | Event to search for. Singular on purpose — weekday events use the singular, weekend ones the plural, and this stem matches both |
| `LANE_TYPE` | `"indoor"` | A slot must contain this to count as a lap lane (filters out `Water Fitness`) |
| `MAX_ATTEMPTS` | `5` | Retry attempts before giving up (terminal errors stop immediately) |
| `RETRY_DELAY` | `3` | Seconds between retries |
| `OPEN_TIME` | `08:00:00` | When registration opens — not a swim time. The script holds until this moment |
| `MAX_PREOPEN_WAIT` | `900` | Longest it will ever hold before opening (safety cap) |
| `EVENT_LISTING_PATIENCE` | `20` | Seconds to keep re-checking for the event listing after 8:00 |

## Troubleshooting

- **Wrong slot booked**: Check `SCHEDULE` in `book_swim.py`. Booking a later slot than expected is normal
  when 8:00 and 8:45 were both gone — the log names the slot it settled on and why.
- **Nothing booked but slots looked open**: Likely the lane filter. `Water Fitness` entries are classes and
  are never booked; neither is anything starting earlier than the first preference or after the cutoff.
  Run `--dry-run` — it annotates each row with why it was ignored.
- **`never appeared on the events page`**: The matcher requires a single link naming both the event and the
  date. This is deliberately strict; it will not fall back to guessing a nearby link, because doing so was
  found to return a *different day's* event. Check whether the site renamed the event series.
- **FAILED email but Trilogy says you're booked**: This was the 2026-07-29 bug and is now handled — the script
  checks the event page for a `Ticket Purchased` heading before failing, and again before every retry, so a
  misread confirmation no longer becomes a false failure or a duplicate booking. If it recurs, the event page
  is what to trust; check `first_failure.png` rather than `last_run.png`, which retries overwrite.
- **`no member row for Gary`**: Usually means the ticket is already held — the wizard stops offering a member
  row once a member has one. The script now verifies that against the event page instead of retrying.
- **Login fails**: Check credentials in `.env`
- **Booking fails**: Review `swim_booker.log` and `last_run.png`. The failure email carries the real
  traceback; if it ever reads `NoneType: None`, `traceback.format_exc()` has been moved outside its
  `except` block again.
- **Missed the 8:00 slot**: Check how late the log's dropdown read is versus 08:00:00. The job starts at
  7:58 so only the event-listing fetch happens after the bell; arriving much past ~08:00:05 means the
  listing was slow, and `EVENT_LISTING_PATIENCE` is the knob.
- **Email not sent**: Confirm `SMTP_APP_PASSWORD` is a Gmail App Password, not your login password
- **Slack not posting**: Confirm `SLACK_WEBHOOK_URL` is set in `.env` and the webhook is active
- **All slots disabled**: The 8 AM slot may already be taken; the script falls back to 8:45 AM, then the
  next available lane within the cutoff. If nothing acceptable remains it stops immediately rather than
  retrying, since retrying cannot bring a slot back.

## Security

- Never commit `.env` to version control (it's in `.gitignore`)
- Use a Gmail App Password, not your account password
