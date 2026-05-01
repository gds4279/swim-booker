# Swim Booker

Automated script to book the 8:00 AM indoor lap pool lane at MyTrilogyLife.com for the next day.

## How It Works

1. Logs into MyTrilogyLife.com using stored credentials
2. Navigates to the Events page and finds the "Indoor Lap Pool Reservations" event for tomorrow
3. Clicks Register, selects the 8:00 AM slot for Gary's member row
4. Completes the 3-step wizard (Tickets → Payments → Confirmation)
5. Verifies the confirmation page was reached
6. Sends an email with the result and a screenshot

Runs automatically at **7:59 AM every Friday and Saturday** via cron, booking the next day's 8:00 AM slot. Retries up to 5 times on failure.

## Setup

### Prerequisites

- Python 3.8+
- A Gmail account with an [App Password](https://support.google.com/accounts/answer/185833) for notifications

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
59 19 * * 5,6 /home/gary/projects/swim-booker/run.sh
```

This runs at 7:59 AM on Fridays (books Saturday) and Saturdays (books Sunday).

## Files

| File | Purpose |
|------|---------|
| `book_swim.py` | Main script |
| `run.sh` | Shell wrapper (used by cron) |
| `requirements.txt` | Python dependencies |
| `.env` | Credentials (not committed) |
| `swim_booker.log` | Rolling log of all runs |
| `failure_screenshot.png` | Screenshot captured on failure or success confirmation |
| `debug_after_payment_continue.png` | Debug screenshot after the payments wizard step |

## Configuration

Key constants at the top of `book_swim.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `TARGET_TIME` | `"8:00 AM"` | Time slot to book |
| `EVENT_NAME` | `"Indoor Lap Pool Reservations"` | Event to search for |
| `MAX_ATTEMPTS` | `5` | Retry attempts before giving up |
| `RETRY_DELAY` | `10` | Seconds between retries |

## Troubleshooting

- **Wrong slot booked**: Verify `TARGET_TIME` in `book_swim.py`
- **Login fails**: Check credentials in `.env`
- **Booking fails**: Review `swim_booker.log` and `failure_screenshot.png`
- **Email not sent**: Confirm `SMTP_APP_PASSWORD` is a Gmail App Password, not your login password
- **All slots disabled**: The 8 AM slot may already be taken; script falls back to the next available slot

## Security

- Never commit `.env` to version control (it's in `.gitignore`)
- Use a Gmail App Password, not your account password
