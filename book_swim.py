import json
import os
import re
import sys
import logging
import smtplib
import time
import traceback
import urllib.request
from datetime import date, datetime, timedelta, time as time_of_day
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Iterable, NamedTuple

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

USERNAME = os.environ["MYTRILOGY_USERNAME"]
PASSWORD = os.environ["MYTRILOGY_PASSWORD"]
SMTP_USER = os.environ["SMTP_USER"]
SMTP_APP_PASSWORD = os.environ["SMTP_APP_PASSWORD"]
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", SMTP_USER)
CC_EMAIL = "dbutler06@comcast.net"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

LOGIN_URL = "https://www.mytrilogylife.com"
EVENTS_URL = "https://members.mytrilogylife.com/events"
# Singular on purpose. Weekday events are titled "Indoor Lap Pool Reservation |
# Tuesday, July 28" while the weekend ones say "…Reservations". The singular stem is
# a substring of both, so it matches either naming. Observed 2026-07-27, when the
# plural form matched nothing and the weekday dry run failed to find the event.
EVENT_NAME = "Indoor Lap Pool Reservation"

# What to book, keyed on the weekday of the day being BOOKED (Mon=0 … Sun=6).
# `any_open` says whether an unlisted time is acceptable when no preference is free:
# on weekends a later swim is still a swim, but a weekday lane is only useful at
# 6:15 — anything later misses the start of the work day, so we book nothing.
#
# NOTE: the times here are swim times. They are unrelated to OPEN_TIME below, which
# is when registration opens (08:00 every day). The two were the same number while
# this was weekend-only; they are not the same thing.
class DaySchedule(NamedTuple):
    preferences: list[str]   # tried in order; an exact match always wins
    any_open: bool           # settle for another slot if no preference is free?
    latest: time_of_day | None = None   # latest start time that fallback may accept


SCHEDULE: dict[int, DaySchedule] = {
    # Weekdays: the first lane of the day is the only one that still gets Gary to work
    # on time, so there is no fallback at all - any later lane is useless and a
    # "booked" email for one is worse than a failure notice.
    # The site re-gridded the weekday morning between 2026-07-29 and 2026-08-03: it
    # ran 6:15/7:00/7:45/8:30 and now runs 6:00/6:45/7:30/8:15. Confirmed 2026-08-03
    # against both Monday's and Tuesday's events. This time is site-side, not a
    # preference - re-read the grid before changing it.
    1: DaySchedule(["6:00 AM"], any_open=False),   # Tuesday
    3: DaySchedule(["6:00 AM"], any_open=False),   # Thursday
    # Weekends: a later swim is still a swim, but not at any hour - without the
    # cutoff a busy Saturday could quietly book an 8:30 PM lane and call it success.
    # The fallback floor is 8:00 AM: choose_slot derives it from preferences[0], so
    # the weekend never books a lane earlier than the target, only a later one.
    5: DaySchedule(["8:00 AM", "8:45 AM"], any_open=True, latest=time_of_day(9, 30)),
    6: DaySchedule(["8:00 AM", "8:45 AM"], any_open=True, latest=time_of_day(9, 30)),
}

# The plan --dry-run assumes for a day we do not book, so recon is possible on any
# day of the week. Not used by real runs: every day we actually book is in SCHEDULE.
DRY_RUN_PROBE = DaySchedule(["6:00 AM"], any_open=False)

LOG_FILE = BASE_DIR / "swim_booker.log"
SCREENSHOT_FILE = BASE_DIR / "last_run.png"
# The first failure is the one worth looking at; later attempts usually fail for a
# downstream reason. `last_run.png` is overwritten on every attempt, so on 2026-07-29
# the only picture of the attempt that mattered was destroyed by four retries and the
# failure email shipped attempt 5's screenshot - which showed the booking succeeding.
FIRST_FAILURE_FILE = BASE_DIR / "first_failure.png"
MAX_ATTEMPTS = 5
# The 8:00 slot has been observed selling out ~30-90s after it opens, and a retry
# costs ~25s of login and navigation on its own. Keep the extra idle wait short.
RETRY_DELAY = 3  # seconds between attempts

# Registration for the next day opens at 8:00 AM local. Logging in and locating the
# event takes ~23s, so the script starts early, does that work, and holds here until
# the window opens - otherwise it arrives ~25s late to a slot that can vanish in 7s.
OPEN_TIME = time_of_day(8, 0, 0)
MAX_PREOPEN_WAIT = 15 * 60  # never hold longer than this (guards odd manual runs)
# Tomorrow's event is only listed once the window opens, and the publish can land a
# moment after 08:00:00. Keep re-checking rather than failing into a ~20s re-login.
EVENT_LISTING_PATIENCE = 20.0  # seconds

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str,
               attachment_path: Path | Iterable[Path] | None = None) -> None:
    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_EMAIL
    msg["Cc"] = CC_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    # A failure can be worth two pictures - the first attempt's page and the last -
    # so this takes either one path or several. Missing files are skipped silently:
    # an absent attachment is clearer than a stale one.
    paths = [attachment_path] if isinstance(attachment_path, Path) else list(attachment_path or [])
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={path.name}")
        msg.attach(part)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_USER, [NOTIFY_EMAIL, CC_EMAIL], msg.as_string())
        log.info("Email sent: %s", subject)
    except Exception:
        log.error("Failed to send email:\n%s", traceback.format_exc())


def send_slack(message: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        log.info("Slack notification sent")
    except Exception:
        log.error("Failed to send Slack notification:\n%s", traceback.format_exc())


class BookingError(Exception):
    """A booking attempt failed.

    `terminal` means retrying cannot help at all. `sold_out_slot` names a specific
    time the site refused as sold out, so later attempts can stop asking for it.
    """

    def __init__(self, reason: str, terminal: bool = False, sold_out_slot: str | None = None):
        super().__init__(reason)
        self.terminal = terminal
        self.sold_out_slot = sold_out_slot


def fail(page, reason: str, terminal: bool = False, sold_out_slot: str | None = None) -> None:
    log.error("FAILED: %s", reason)
    try:
        page.screenshot(path=str(SCREENSHOT_FILE))
        log.info("Screenshot saved to %s", SCREENSHOT_FILE)
        # Keep the first failure of the run as well. Retries overwrite last_run.png,
        # and the attempt that actually went wrong is nearly always the first one.
        if not FIRST_FAILURE_FILE.exists():
            FIRST_FAILURE_FILE.write_bytes(SCREENSHOT_FILE.read_bytes())
    except Exception:
        # Leave no stale screenshot behind - an absent attachment is clearer
        # evidence than one left over from a previous run.
        SCREENSHOT_FILE.unlink(missing_ok=True)
        log.error("Could not capture screenshot:\n%s", traceback.format_exc())
    raise BookingError(reason, terminal=terminal, sold_out_slot=sold_out_slot)


def seconds_until_open(now: datetime | None = None) -> float:
    """How long to hold before registration opens. 0 means proceed immediately.

    Returns 0 once past the opening time, so retries later in the run and ad-hoc
    runs at any other hour go straight through. Also returns 0 if the wait would
    exceed MAX_PREOPEN_WAIT, so a stray early run never hangs for hours.
    """
    now = now or datetime.now()
    delta = (datetime.combine(now.date(), OPEN_TIME) - now).total_seconds()
    if delta <= 0 or delta > MAX_PREOPEN_WAIT:
        return 0.0
    return delta


def wait_until_open() -> bool:
    """Block until the registration window opens. Returns True if it actually waited."""
    delta = seconds_until_open()
    if delta <= 0:
        raw = (datetime.combine(date.today(), OPEN_TIME) - datetime.now()).total_seconds()
        if raw > MAX_PREOPEN_WAIT:
            log.warning("Registration opens in %.0f min, beyond the %.0f min hold cap – proceeding now",
                        raw / 60, MAX_PREOPEN_WAIT / 60)
        return False

    target = datetime.now() + timedelta(seconds=delta)
    log.info("Logged in and staged %.1fs early – holding until registration opens at %s",
             delta, OPEN_TIME.strftime("%H:%M:%S"))
    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 0.25))
    log.info("Registration window open – going now")
    return True


# Scans the events list inside the browser and returns the matching href in a single
# round-trip. The previous version called inner_text() on every anchor from Python,
# which cost one round-trip each and seconds overall - time we no longer have, since
# the listing only publishes at 08:00 and the 8:00 slot can be gone by 08:00:07.
_FIND_EVENT_JS = r"""
([name, d1, d2]) => {
  // Event titles are hand-typed by the club, so their punctuation cannot be relied
  // on: Tuesday 8/4/2026 was published as "Tuesday August, 4" while Monday the same
  // morning read "Monday, August 3". Comparing raw substrings missed it entirely and
  // the 2026-08-03 run failed with "never appeared on the events page". Both sides
  // are reduced to lowercase words separated by single spaces before comparing, so
  // stray commas, pipes and double spaces stop mattering. The token boundaries are
  // what keep "august 4" from matching "august 14" or "august 40".
  const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const wanted = norm(name), dates = [norm(d1), norm(d2)];
  const hasDate = hay => dates.some(d => d && new RegExp('(^| )' + d + '( |$)').test(hay));

  // A match must come from a single anchor that names both the event and the date and
  // points at an event *detail* page. There used to be a broader fallback that
  // scanned td/li/div for a container mentioning both, then took an anchor from it.
  // It cannot work: querySelectorAll returns outermost-first, so the first "match" is
  // a whole-page wrapper, and the anchor it yields is unrelated to the date that
  // matched. It returned the top-nav "EVENTS" link (/events) for an unopened day -
  // and, once restricted to /events/<id> links, returned a *different day's event*,
  // which is worse: a plausible URL that books the wrong day. Verified 2026-07-28
  // against Thursday 7/30, which resolved to Tuesday's event 1849591.
  // If this ever stops matching, fail loudly rather than reinstating a guess.
  //
  // Every match is returned, not just the first: the club splits a single day across
  // more than one event (Tuesday 8/4/2026 ran 6:00AM-8:55AM and 10:00AM-5:00PM as
  // separate listings), and only one of them holds the slot we want. Which to open is
  // decided in Python by order_candidates, on the anchor's own text.
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a')) {
    const t = a.innerText || '';
    const n = norm(t);
    if (!n.includes(wanted)) continue;
    if (!hasDate(n)) continue;
    if (!a.href || !/\/events\/\d+/.test(a.href)) continue;
    if (seen.has(a.href)) continue;
    seen.add(a.href);
    out.push({href: a.href, text: t.replace(/\s+/g, ' ').trim()});
  }
  return out;
}
"""


# An event detail page is /events/<numeric id>. The bare listing URL is not one, and
# telling them apart is load-bearing - see open_event_page.
_EVENT_DETAIL_RE = re.compile(r"/events/\d+")


def find_event_candidates(page, target_date: date) -> list[dict]:
    """Load the events list and return every listing for tomorrow's event.

    Usually one. The club has published a single day as two separate events, so the
    caller must choose - see `order_candidates`.
    """
    page.goto(EVENTS_URL, wait_until="networkidle")
    return page.evaluate(_FIND_EVENT_JS, [
        EVENT_NAME,
        target_date.strftime("%-m/%-d/%Y"),   # e.g. "7/26/2026"
        target_date.strftime("%B %-d"),       # e.g. "July 26"
    ])


def order_candidates(candidates: list[dict], preferences: list[str]) -> list[dict]:
    """Put the listings that advertise a preferred time first, DOM order otherwise.

    On 2026-08-03 Tuesday was published as two events - "6:00AM-8:55AM" and
    "10:00AM-5:00PM" - and only the first holds the 6:00 lane. Each listing's own
    text carries its window, so the choice costs nothing: no extra page load, and a
    day published as one event is unaffected.

    This only decides which page to open. What actually gets booked is still decided
    by `choose_slot` against the real dropdown, so a wrong guess here cannot book the
    wrong time - it can only fail to find the right one. That is why an unrecognised
    set of windows keeps DOM order rather than dropping anything.
    """
    if len(candidates) < 2 or not preferences:
        return candidates
    wanted = [_norm(p) for p in preferences]
    return sorted(candidates,
                  key=lambda c: not any(w in _norm(c.get("text", "")) for w in wanted))


def open_event_page(page, target_date: date, patience: float = 0.0,
                    preferences: list[str] | None = None) -> str | None:
    """Open tomorrow's event detail page. Returns its URL, or None if not listed.

    The listing only publishes at 08:00:00, and the publish may land a moment after
    our first look, so `patience` keeps re-checking for that many seconds rather than
    giving up and forcing a full re-login. Returns None rather than failing so the
    caller can decide whether being early is acceptable.

    A link alone is not proof the event is open: tomorrow's event is *listed* before
    08:00 but its detail page is not reachable yet, and the site quietly redirects
    back to the listing. So the landing URL is checked, not just the href. Returning
    page.url unverified is what broke the 2026-07-28 run - `event_url` became the bare
    listing URL, which is not None, so the caller treated the *listing* as the event
    page, refreshed it at 08:00:00 and clicked a Register button belonging to an
    unrelated event.
    """
    deadline = time.monotonic() + patience
    checks = 0
    while True:
        checks += 1
        candidates = order_candidates(find_event_candidates(page, target_date),
                                      preferences or [])
        if candidates:
            if len(candidates) > 1:
                log.info("%d listings match %s – trying them in order: %s",
                         len(candidates), target_date.isoformat(),
                         ", ".join(c["href"] for c in candidates))
            # Every candidate already names the event and the date, so any of them is
            # a legitimate page for this day; try the next only when one redirects.
            for candidate in candidates:
                page.goto(candidate["href"], wait_until="networkidle")
                if _EVENT_DETAIL_RE.search(page.url):
                    if checks > 1:
                        log.info("Event appeared on check %d", checks)
                    return page.url
                reason = f"link redirected to {page.url} – not open yet"
        else:
            reason = "not listed yet"
        if time.monotonic() >= deadline:
            return None
        log.info("Event %s (check %d) – re-checking", reason, checks)
        page.wait_for_timeout(400)


def _norm(s: str) -> str:
    return s.lower().replace(" ", "")


# Not every ticket in a lap-pool event is lap swimming. The weekday grid read on
# 2026-07-29 offered "Water Fitness" (a class) in the same dropdown as "Indoor Pool",
# "Indoor Lap" and "Indoor Lap Pool", plus one slot with no type at all. Only book
# something that names itself Indoor: showing up to a water aerobics class is a wrong
# booking, not a lesser one.
LANE_TYPE = "indoor"


def is_lane(option_text: str) -> bool:
    return LANE_TYPE in option_text.lower()


_SLOT_START_RE = re.compile(r'\s*(\d{1,2}):(\d{2})\s*([ap])\.?m', re.I)


def slot_start(option_text: str) -> time_of_day | None:
    """Start time of a slot like '9:30am - 10:10am Indoor Pool - Free'.

    None when the text does not begin with a parseable time — callers treat that
    as "cannot confirm", which for the cutoff means skipping the slot rather than
    risking a booking at an unknown hour.
    """
    m = _SLOT_START_RE.match(option_text)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if hour == 12:
        hour = 0
    if meridiem == "p":
        hour += 12
    return time_of_day(hour, minute)


def choose_slot(options: list[dict], preferences: list[str], avoid: set[str],
                any_open: bool = True, latest: time_of_day | None = None):
    """Pick a bookable ticket option.

    Walks `preferences` in order; an exact match always wins and is never subject to
    the fallback bounds. If no preference is free and `any_open` is set, settles for
    the earliest remaining lane that starts no earlier than the first preference and
    no later than `latest`. When `any_open` is not set, books nothing at all: a
    weekday lane outside 6:15 is useless, so no swim beats the wrong swim.

    Both bounds matter. Without `latest` a busy day silently books an evening lane.
    Without the lower bound, a grid that starts before the preferred time answers a
    missing 8:00 with a 6:15 lane — weekday grids demonstrably do start at 6:15, so
    "earliest open" alone is only safe while every grid happens to start at 8:00.

    Only slots that name themselves as lanes are eligible, in both loops — see
    `is_lane`. A slot whose start time cannot be parsed is skipped rather than
    guessed at.

    Times in `avoid` are skipped entirely: the site has already refused them, so
    asking again just burns an attempt.

    Returns (value, option_text, matched_preference) or (None, None, None).
    """
    avoid_n = [_norm(a) for a in avoid]

    def blocked(text: str) -> bool:
        return any(a in _norm(text) for a in avoid_n)

    for pref in preferences:
        for o in options:
            if o["disabled"] or blocked(o["text"]) or _norm(pref) not in _norm(o["text"]):
                continue
            if not is_lane(o["text"]):
                # Right time, wrong activity. Logged rather than passed over quietly:
                # if the site renames its lane tickets this is why booking stopped.
                log.warning("%s is offered as %r, not a lap lane – skipping",
                            pref, o["text"].strip())
                continue
            return o["value"], o["text"], pref

    if not any_open:
        return None, None, None

    earliest = slot_start(preferences[0]) if preferences else None

    for o in options:
        if o["disabled"] or not o["value"] or o["value"] == "0" or blocked(o["text"]):
            continue
        if not is_lane(o["text"]):
            continue
        start = slot_start(o["text"])
        if start is None:
            continue
        if earliest is not None and start < earliest:
            continue
        if latest is not None and start > latest:
            continue
        return o["value"], o["text"], None

    return None, None, None


def report_slots(options: list[dict], target_date: date, preferences: list[str],
                 any_open: bool, latest: time_of_day | None = None) -> None:
    """Print the real slot grid and what the current config would do with it.

    Used by --dry-run to validate a schedule against the live site before any
    booking is attempted.
    """
    lines = ["", "=" * 62,
             f"DRY RUN – nothing was booked",
             f"Target: {target_date.strftime('%A, %B %-d, %Y')}",
             "=" * 62, "", "Slots offered to Gary:"]
    for o in options:
        if not o["value"] or o["value"] == "0":
            continue
        note = "" if is_lane(o["text"]) else "   (not a lap lane – never booked)"
        lines.append(f"   {'OPEN ' if not o['disabled'] else 'taken'}  {o['text']}{note}")

    want = " → ".join(preferences) if preferences else "(none configured)"
    if not any_open:
        fb = "NO — book nothing if unavailable"
    else:
        bounds = []
        if preferences:
            bounds.append(f"nothing earlier than {preferences[0]}")
        if latest is not None:
            bounds.append(f"nothing starting after {latest.strftime('%-I:%M %p')}")
        fb = "yes — " + ("; ".join(bounds) if bounds else "any open lane")
    lines += ["", f"Configured preference: {want}", f"Fallback: {fb}"]

    value, text, matched = choose_slot(options, preferences, set(),
                                       any_open=any_open, latest=latest)
    if value is None:
        lines.append("Would book: NOTHING — no acceptable slot"
                     + ("" if any_open else " (fallback disabled for this day)"))
    else:
        how = f"matched preference {matched}" if matched else "fallback to earliest acceptable lane"
        lines.append(f"Would book: {text.split(' Indoor')[0].strip()}   ({how})")
    lines += ["=" * 62, ""]
    log.info("\n".join(lines))


def classify_result(page_text: str) -> tuple[str | None, bool]:
    """Decide whether the wizard's final page represents a real booking.

    Returns (matched_error, succeeded). An error phrase always wins over a success
    phrase: the site renders errors on the same "3.CONFIRMATION" step as successes.
    """
    text = page_text.lower().replace("’", "'").replace("‘", "'")
    error_indicators = ["unable to complete registration", "could not be completed", "sold out"]
    # "3.confirmation" is deliberately NOT a success indicator - it is a wizard
    # breadcrumb ("1.TICKETS 2.PAYMENTS 3.CONFIRMATION") rendered on every step,
    # including failures, so matching it reports success for a booking that never
    # happened. Likewise "thank you", which appears in unrelated page furniture.
    success_indicators = ["success! you're going", "you are registered", "ticket purchased",
                          "booking confirmed", "you have been registered"]
    matched_error = next((kw for kw in error_indicators if kw in text), None)
    succeeded = matched_error is None and any(kw in text for kw in success_indicators)
    return matched_error, succeeded


# Every element the registration wizard renders carries an `event-registration*`
# class. Scoping to it is not a nicety: the event page rendered around the wizard
# lists the entire ticket grid - "(Sold Out)" beside every gone slot, and a "Ticket
# Purchased" heading once you hold a ticket - so a page-wide wait for those words is
# answered by furniture. Measured 2026-07-29 on the plain event page: 15 visible
# matches, 13 of them "(Sold Out)" price rows.
REGISTRATION_SCOPE = ".event-registration, [class*='event-registration']"


def registration_text(page) -> str:
    """All text inside the registration wizard, or "" when it is not on the page.

    Reads every matching node instead of `.first`. That union selector matched 41
    elements on a live event page and the first in DOM order was
    `.event-registration__title` - a heading holding the event name and nothing
    else, which contains no verdict and so reads as failure no matter what
    happened. Joining them all also keeps this working whichever wrapper class the
    site happens to use for a given wizard step.
    """
    try:
        return "\n".join(page.locator(REGISTRATION_SCOPE).all_inner_texts()).strip()
    except Exception:
        return ""


def await_verdict(page, budget_ms: int = 8000) -> str:
    """Poll the wizard until its own text says something decisive, then return it.

    An empty or still-rendering wizard is *not* a verdict. On 2026-07-29 the result
    was read 822ms after submitting, because the page-wide wait it replaced was
    satisfied instantly by the ticket grid behind the modal. The wizard had not
    painted its confirmation yet, so a booking the site had already accepted - and
    emailed about - was reported as "Not on confirmation page".

    Returns whatever the wizard says once the budget runs out, so an inconclusive
    read still reaches the event-page check rather than blocking.
    """
    deadline = time.monotonic() + budget_ms / 1000
    while True:
        text = registration_text(page)
        if classify_result(text) != (None, False):
            return text
        if time.monotonic() >= deadline:
            log.warning("Wizard gave no verdict within %.1fs – falling back to the event page",
                        budget_ms / 1000)
            return text
        page.wait_for_timeout(250)


# The site prints the ticket a member holds on the event page itself, as a heading
# followed by the slot:
#
#     Ticket Purchased
#     6:15am-6:55am Indoor Pool
#     $0.00
#
# This is the authoritative record - it survives the wizard, and it names the slot.
_PURCHASED_RE = re.compile(r"^[ \t]*Ticket Purchased[ \t]*$\n+[ \t]*(\S.*?)[ \t]*$",
                           re.IGNORECASE | re.MULTILINE)


def purchased_slot(page) -> str | None:
    """The slot Gary already holds on the event page currently loaded, if any.

    Trusted over anything the wizard says. The wizard reports on one submission and
    can be misread mid-render; this is the site's own answer to "what do I hold?",
    read from a settled page, and it is what proves a retry would double-book.
    """
    try:
        m = _PURCHASED_RE.search(page.inner_text("body"))
    except Exception:
        return None
    return m.group(1).strip() if m else None


def capture(page) -> None:
    """Screenshot the current page as this run's evidence, best effort.

    Used by the success paths that never reach the wizard's confirmation page, so
    the email still carries a picture - here, the event page showing the ticket.
    """
    try:
        page.screenshot(path=str(SCREENSHOT_FILE))
    except Exception:
        log.warning("Could not capture screenshot:\n%s", traceback.format_exc())


def check_event_page(page, event_url: str) -> str | None:
    """Reload the event page and return the ticket Gary holds there, or None.

    Wraps `purchased_slot` with the navigation and the "never raise" contract its
    callers need: this runs on paths where the booking may already be made, so a
    thrown exception here must not turn a real reservation into a retry.
    """
    try:
        page.goto(event_url, wait_until="networkidle")
        held = purchased_slot(page)
    except Exception:
        log.warning("Event-page check failed to run:\n%s", traceback.format_exc())
        return None
    if held:
        log.info("Event page confirms a ticket: %s", held)
    else:
        log.warning("Event page shows no ticket for this event")
    return held


def settle(page, selector: str, budget_ms: int) -> None:
    """Wait for `selector` to appear, giving up after `budget_ms`.

    Returns as soon as the next step renders instead of always burning the full
    budget; falls back to sleeping it out so a missed selector behaves as before.
    """
    try:
        page.wait_for_selector(selector, timeout=budget_ms)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(budget_ms)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def book_once(preferences: list[str], avoid: set[str] | None = None,
              any_open: bool = True, latest: time_of_day | None = None,
              dry_run: bool = False) -> None:
    avoid = avoid or set()
    target_date = date.today() + timedelta(days=1)
    booked_slot_text = None  # set only once a specific slot option is actually selected
    attempted_slot = None    # which preference we committed to, for sold-out reporting
    verified = False         # set if the reservation is confirmed on a fresh page load
    log.info("Booking %s %s slot for %s (preferring %s%s)",
             preferences[0] if preferences else "any available", EVENT_NAME,
             target_date.isoformat(), " → ".join(preferences) or "any",
             f", avoiding {', '.join(sorted(avoid))}" if avoid else "")

    with sync_playwright() as p:
        # sync_playwright()'s __exit__ stops the driver and tears down any browser it
        # launched, on the exception path too - no explicit close needed for cleanup.
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # ------------------------------------------------------------------
        # 1. Login
        # ------------------------------------------------------------------
        log.info("Navigating to %s", LOGIN_URL)
        page.goto(LOGIN_URL, wait_until="networkidle")

        try:
            # Click the "LOG IN HERE" button on the homepage
            page.click('a:has-text("LOG IN HERE"), a:has-text("Log In Here"), button:has-text("LOG IN HERE")')
            page.wait_for_load_state("networkidle")
        except PlaywrightTimeoutError:
            fail(page, "Could not find or click the LOG IN HERE button")

        try:
            # Fill username and password fields
            page.wait_for_selector('input[type="password"]', timeout=15000)
            page.fill('input[name="username"], input[id*="user"], input[placeholder*="Username" i], input[type="text"]', USERNAME)
            page.fill('input[name="password"], input[type="password"]', PASSWORD)
            login_url = page.url
            page.click('button[type="submit"], input[type="submit"], button:has-text("Log In"), button:has-text("Sign In"), button:has-text("Submit")')
            # Wait for navigation away from the login page
            page.wait_for_url(lambda url: url != login_url, timeout=20000)
            page.wait_for_load_state("networkidle")
        except PlaywrightTimeoutError:
            fail(page, "Timed out after submitting login – credentials may be wrong or page did not redirect")

        log.info("Login successful")

        # ------------------------------------------------------------------
        # 2/3. Stage, hold for the opening bell, then grab the event page
        # ------------------------------------------------------------------
        # The events list only publishes tomorrow's event at 08:00, so this early
        # look usually finds nothing. It costs nothing either (we are waiting anyway)
        # and it warms the session, so we try — but never let it break the run.
        event_url = None
        if seconds_until_open() > 0:
            try:
                event_url = open_event_page(page, target_date, preferences=preferences)
                if event_url is None:
                    log.info("Event not published yet, as expected – will fetch when the window opens")
            except Exception:
                log.warning("Pre-open staging failed harmlessly:\n%s", traceback.format_exc())

        wait_until_open()

        # Anything fetched above was fetched before the window opened, so it is always
        # refreshed here - never branch on whether we actually waited, or a run that
        # straddles 08:00:00 during staging would proceed on a stale pre-open page.
        if event_url is not None:
            log.info("Refreshing the event page now that registration is open")
            page.goto(event_url, wait_until="networkidle")
        else:
            event_url = open_event_page(page, target_date, patience=EVENT_LISTING_PATIENCE,
                                        preferences=preferences)

        if event_url is None:
            fail(page, f"'{EVENT_NAME}' for {target_date.strftime('%-m/%-d/%Y')} never appeared "
                       f"on the events page within {EVENT_LISTING_PATIENCE:.0f}s of opening")

        log.info("On event detail page: %s", event_url)

        # ------------------------------------------------------------------
        # 4. Stop if this event is already booked
        # ------------------------------------------------------------------
        # Free: we are standing on the event page anyway. This is what keeps a
        # misread confirmation from becoming four more bookings. On 2026-07-29 the
        # site itself refused the duplicates - but only because the wizard drops
        # Gary's member row once he holds a ticket. On a weekend, where a fallback
        # is enabled, there is no such guarantee: a retry could book a second lane
        # at a different time and the earlier one would never be released.
        held = purchased_slot(page)
        if held:
            log.info("Already registered for %s – not re-entering the wizard", held)
            if dry_run:
                browser.close()
                return
            capture(page)
            report_success(target_date, held, verified=True,
                           note="\nNOTE: the ticket was already held when this attempt started, "
                                "so an earlier attempt booked it.\n")
            browser.close()
            return

        # ------------------------------------------------------------------
        # 5. Find the target slot and open the registration wizard
        # ------------------------------------------------------------------
        wanted = preferences[0] if preferences else "8:00 AM"
        log.info("Looking for the %s slot", wanted)

        # Anchor the button search on the time we actually want - it is no longer
        # always 8:00. This only picks which Register button opens the wizard; the
        # booking itself is decided by the dropdown in step 5, and there is a
        # catch-all fallback below, so a miss here is not fatal.
        time_variants = [wanted, wanted.lower().replace(" ", ""),
                         wanted.lower(), wanted.upper().replace(" ", "")]
        register_btn = None

        for variant in time_variants:
            slot = page.locator(f"text={variant}").first
            if slot.count() == 0:
                continue
            # Look for a nearby button: sibling, parent row, or nearby element
            # Try: closest row/li/div that also has a Register button
            row_html = slot.evaluate(
                "el => el.closest('tr, li, .slot, .time-slot, div[class*=\"slot\"], div[class*=\"time\"]')?.innerHTML || ''"
            )
            if row_html:
                # Find a register/book button inside that container
                container = page.locator(
                    f"tr:has-text('{variant}'), li:has-text('{variant}'), "
                    f"div:has-text('{variant}')"
                ).first
                btn = container.locator(
                    "button:has-text('Register'), button:has-text('Book'), "
                    "a:has-text('Register'), a:has-text('Book'), input[type='submit']"
                ).first
                if btn.count() > 0:
                    register_btn = btn
                    log.info("Found register button near '%s'", variant)
                    break

        # Fallback: any Register button on the page - but only while we are genuinely
        # on an event detail page. On the listing, "any Register button" belongs to
        # some other event entirely; that is how the 2026-07-28 redirect bug got as
        # far as opening a stranger's registration wizard instead of failing.
        if register_btn is None and _EVENT_DETAIL_RE.search(page.url):
            btn = page.locator(
                "button:has-text('Register'), a:has-text('Register'), input[value='Register']"
            ).first
            if btn.count() > 0:
                register_btn = btn
                log.warning("Using fallback register button (could not isolate the %s slot)", wanted)

        if register_btn is None:
            fail(page, f"Could not find a Register button for the {wanted} slot")

        register_btn.click()
        page.wait_for_load_state("networkidle")

        # ------------------------------------------------------------------
        # 6. Handle ticket selection wizard (1.Tickets → 2.Payments → 3.Confirmation)
        # ------------------------------------------------------------------
        # Target Gary's specific member row (not the whole form, not Dawn's row)
        gary_row = page.locator("li.event-registration__members-row").filter(has_text="Gary").first
        if gary_row.count() == 0:
            # The wizard stops offering a member row once that member holds a ticket,
            # so "no row" is as often proof of success as it is a failure. Ask the
            # event page which it is before reporting anything. (Contrast 2026-07-03,
            # where attempt 1 genuinely failed and attempt 2 still found the row.)
            held = check_event_page(page, event_url)
            if held and not dry_run:
                log.warning("No member row because the ticket is already held: %s", held)
                capture(page)
                report_success(target_date, held, verified=True,
                               note="\nNOTE: the wizard offered no member row because this "
                                    "ticket was already booked.\n")
                browser.close()
                return
            fail(page, "Ticket selection step never appeared – no member row for Gary")

        log.info("Ticket selection step detected – targeting Gary's member row")
        gary_select = gary_row.locator("select").first
        if gary_select.count() == 0:
            fail(page, "Gary's member row has no time-slot dropdown")

        options = gary_select.evaluate(
            "el => [...el.options].map(o => ({value: o.value, text: o.text, disabled: o.disabled}))"
        )
        log.info("Gary's ticket options: %s", options)

        if dry_run:
            # Stop here: the wizard is open but nothing is selected and no Continue
            # has been clicked, so no reservation can result.
            report_slots(options, target_date, preferences, any_open, latest)
            browser.close()
            return

        selected_value, raw_text, attempted_slot = choose_slot(
            options, preferences, avoid, any_open=any_open, latest=latest)
        if selected_value is None:
            if not any_open:
                # Deliberate: a lane outside the preferred time is no use on this day.
                fail(page, f"{' / '.join(preferences)} unavailable for {target_date.isoformat()} – "
                           f"not booking a fallback", terminal=True)
            # Nothing acceptable left. Either everything is gone, or all that remains
            # starts after the cutoff - both mean retrying cannot help.
            bound = f" starting by {latest.strftime('%-I:%M %p')}" if latest else ""
            fail(page, f"No bookable slot{bound} for {target_date.isoformat()} – "
                       f"nothing suitable available", terminal=True)

        booked_slot_text = raw_text.split(" Indoor")[0].strip()
        if attempted_slot is None:
            log.warning("No preferred slot available – falling back to %s", booked_slot_text)
        elif preferences and attempted_slot != preferences[0]:
            log.warning("%s unavailable – falling back to %s", preferences[0], attempted_slot)
        log.info("Selecting Gary's slot: %s (%s)", selected_value, booked_slot_text)
        try:
            gary_select.select_option(value=selected_value, timeout=5000)
            log.info("Selected via Playwright select_option")
        except Exception as e:
            log.warning("select_option failed (%s) – trying JS setter", e)
        # Re-trigger React's change event so its state updates
        gary_select.evaluate(
            "(el, val) => { const s = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set; s.call(el,val); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); }",
            selected_value,
        )

        next_btn = page.locator(
            "#continue-button, button:has-text('Continue'), button:has-text('Next'), "
            "a:has-text('Next'), a:has-text('Continue')"
        ).first
        if next_btn.count() > 0:
            log.info("Waiting for Continue to enable after slot selection")
            try:
                next_btn.wait_for(state="enabled", timeout=15000)
                log.info("Continue enabled – clicking")
                next_btn.click()
            except Exception:
                log.warning("Continue still disabled – force-clicking via JS")
                next_btn.evaluate(
                    "el => { el.disabled = false; el.classList.remove('cta--disabled'); el.click(); }"
                )
            page.wait_for_load_state("networkidle")
            # Step 2 is the agreement checkbox - proceed as soon as it renders
            settle(page, "input[type='checkbox']", 1500)

        # ------------------------------------------------------------------
        # 7. Handle payments/agreement step if present (step 2 of wizard)
        # ------------------------------------------------------------------
        agree_cb = page.locator("input[type='checkbox']").first
        if agree_cb.count() > 0:
            log.info("Payment agreement step detected – checking 'I Agree' via React props")
            # Call React's onChange directly via __reactProps (React 17+), falling back to native events.
            # Do NOT dispatch 'click' — that would toggle the checkbox back to unchecked.
            cb_result = agree_cb.evaluate("""el => {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set;
                setter.call(el, true);
                const propsKey = Object.keys(el).find(k => k.startsWith('__reactProps'));
                if (propsKey && el[propsKey].onChange) {
                    el[propsKey].onChange({target: el, currentTarget: el, type: 'change', bubbles: true,
                                          preventDefault: () => {}, stopPropagation: () => {}});
                    return 'react_props';
                }
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return 'native_events';
            }""")
            log.info("'I Agree' checked via %s", cb_result)
            page.wait_for_timeout(1500)

            # Use the LAST visible Continue button — step 2's button comes after step 1's in the DOM
            pay_btn = page.locator("button:has-text('Continue'), #continue-button, a:has-text('Continue')").last
            if pay_btn.count() > 0:
                log.info("Waiting for Continue button to enable after checkbox check")
                try:
                    pay_btn.wait_for(state="enabled", timeout=15000)
                    log.info("Continue button enabled – clicking naturally")
                    pay_btn.scroll_into_view_if_needed()
                    pay_btn.click()
                except Exception:
                    log.warning("Continue still disabled – force-clicking last Continue button")
                    pay_btn.evaluate("""el => {
                        el.disabled = false;
                        el.classList.remove('cta--disabled');
                        const propsKey = Object.keys(el).find(k => k.startsWith('__reactProps'));
                        if (propsKey && el[propsKey].onClick) {
                            el[propsKey].onClick({target: el, preventDefault: () => {}, stopPropagation: () => {}});
                        } else {
                            el.click();
                        }
                    }""")
                page.wait_for_load_state("networkidle")
                # Step 3 renders either the confirmation or an error. Do NOT wait on a
                # page-wide selector here either - the same furniture that defeated the
                # old wait in step 8 defeats it here. await_verdict in step 8 is the
                # real wait; this is only a short head start before the confirm button
                # is looked for.
                page.wait_for_timeout(500)

        # ------------------------------------------------------------------
        # 8. Confirm if a confirmation dialog/button appears
        # ------------------------------------------------------------------
        confirm_btn = page.locator(
            "button:has-text('Confirm'), button:has-text('Yes'), "
            "button:has-text('Submit'), a:has-text('Confirm')"
        ).first
        if confirm_btn.count() > 0:
            log.info("Clicking confirmation button")
            confirm_btn.click()
            page.wait_for_load_state("networkidle")

        # ------------------------------------------------------------------
        # 9. Verify success
        # ------------------------------------------------------------------
        # Wait on the wizard's own text, never a page-wide selector - see await_verdict.
        page_text = await_verdict(page)
        log.info("Final page URL: %s", page.url)

        matched_error, succeeded = classify_result(page_text)
        if not succeeded:
            reason = f"Registration error: {matched_error}" if matched_error else "Not on confirmation page"
            # The wizard is not the last word. Before failing, ask the site what Gary
            # actually holds: on 2026-07-29 the booking had gone through and been
            # confirmed by email while this path was reporting failure. An explicit
            # error phrase is trusted as-is - it is a real answer, not a silence.
            if matched_error is None:
                held = check_event_page(page, event_url)
                if held:
                    log.warning("Wizard page was unreadable, but the event page shows "
                                "a ticket for %s – treating as booked", held)
                    booked_slot_text = booked_slot_text or held
                    capture(page)
                    report_success(target_date, booked_slot_text, verified=True,
                                   note="\nNOTE: the wizard's confirmation page could not be read; "
                                        "this booking was confirmed from the event page instead.\n")
                    browser.close()
                    return
            # A slot that looked open in the dropdown can still be gone by submission -
            # report which one so the retry stops asking for it. Not terminal: another
            # time may well still be free.
            refused = (attempted_slot or booked_slot_text) if "sold out" in page_text.lower() else None
            if refused:
                log.warning("Site refused %s as sold out – it will be skipped on retry", refused)
            fail(page, reason, sold_out_slot=refused)

        log.info("SUCCESS – registered for %s %s on %s", booked_slot_text, EVENT_NAME, target_date.isoformat())
        page.screenshot(path=str(SCREENSHOT_FILE))

        # ------------------------------------------------------------------
        # 10. Independently confirm the reservation actually persisted
        # ------------------------------------------------------------------
        # The wizard rendering a confirmation is not proof the booking stuck - reload
        # the event page and look for the ticket. This never fails the run: the
        # booking is already made, and a retry here could double-book.
        held = check_event_page(page, event_url)
        verified = held is not None
        if verified and booked_slot_text and _norm(held).split("indoor")[0] != _norm(booked_slot_text):
            log.warning("Event page shows %r but %r was selected – reporting what the site says",
                        held, booked_slot_text)
            booked_slot_text = held

        browser.close()

    report_success(target_date, booked_slot_text, verified)


def report_success(target_date: date, booked_slot_text: str | None,
                   verified: bool, note: str = "") -> None:
    """Send the success email and Slack message.

    Its own function because success can now be reached three ways: the wizard
    confirming, the event page showing the ticket after an unreadable wizard, and
    finding the ticket already held before a retry re-enters the wizard.
    """
    # Reaching here without a slot means verification let something through that it
    # should not have - refuse to claim a booking we cannot name.
    if not booked_slot_text:
        raise BookingError("Reported success but no time slot was ever selected")

    day_name = target_date.strftime("%A")
    verify_note = ("" if verified else
                   "\nNOTE: the confirmation page was shown, but reloading the event page "
                   "did not show the reservation. Worth checking manually.\n")

    send_email(
        subject=f"[Swim Booker] SUCCESS – {day_name} {booked_slot_text} booked",
        body=(
            f"Swim lane successfully booked!\n\n"
            f"Event:  {EVENT_NAME}\n"
            f"Date:   {target_date.strftime('%A, %B %-d, %Y')}\n"
            f"Time:   {booked_slot_text}\n"
            f"{note}{verify_note}"
        ),
        attachment_path=SCREENSHOT_FILE,
    )
    send_slack(
        f":white_check_mark: *Swim lane booked!* {target_date.strftime('%A, %B %-d')} {booked_slot_text}"
        + ("" if verified else " _(could not independently verify — please check)_")
    )


def trim_log() -> None:
    if not LOG_FILE.exists():
        return
    cutoff = date.today() - timedelta(days=30)
    lines = LOG_FILE.read_text().splitlines(keepends=True)
    kept = []
    keep_block = False
    for line in lines:
        m = re.match(r'^(\d{4}-\d{2}-\d{2})', line)
        if m:
            keep_block = date.fromisoformat(m.group(1)) >= cutoff
        if keep_block:
            kept.append(line)
    LOG_FILE.write_text("".join(kept))


def schedule_for(target_date: date, probe: bool = False) -> DaySchedule | None:
    """The plan for the day being booked, or None if we do not swim that day.

    `probe` is for --dry-run only: it lets recon inspect a day that is not on the
    schedule at all, rather than refusing to look. A real run must never pass it, or
    it would book on a day Gary does not swim.
    """
    if probe and target_date.weekday() not in SCHEDULE:
        return DRY_RUN_PROBE
    return SCHEDULE.get(target_date.weekday())


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    target_date = date.today() + timedelta(days=1)
    # A dry run is for inspecting any day, including ones we do not book.
    plan = schedule_for(target_date, probe=dry_run)

    if plan is None:
        log.info("Nothing scheduled for %s – exiting without booking",
                 target_date.strftime("%A, %B %-d"))
        return

    if dry_run:
        log.info("DRY RUN – will read the slot grid for %s and book nothing",
                 target_date.strftime("%A, %B %-d"))
        book_once(list(plan.preferences), any_open=plan.any_open,
                  latest=plan.latest, dry_run=True)
        return

    # Start clean so a screenshot from an earlier run can never be attached as if it
    # were evidence from this one.
    SCREENSHOT_FILE.unlink(missing_ok=True)
    FIRST_FAILURE_FILE.unlink(missing_ok=True)

    last_exc: BaseException | None = None
    last_tb = ""
    attempts_used = 0
    # First attempt chases the day's first preference. Once the site confirms a time
    # is sold out, it is dropped so the remaining attempts pivot to the next choice
    # instead of re-submitting a booking the site has already refused.
    preferences = list(plan.preferences)
    avoid: set[str] = set()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts_used = attempt
        try:
            log.info("Attempt %d/%d", attempt, MAX_ATTEMPTS)
            book_once(preferences, avoid, any_open=plan.any_open, latest=plan.latest)
            trim_log()
            return
        except Exception as exc:
            last_exc = exc
            # Must be captured inside the handler - outside it, format_exc() has no
            # active exception to report and returns "NoneType: None".
            last_tb = traceback.format_exc()
            log.warning("Attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc)
            if isinstance(exc, BookingError) and exc.terminal:
                log.error("Condition will not clear on retry – not attempting again")
                break
            refused = getattr(exc, "sold_out_slot", None)
            if refused and refused not in avoid:
                avoid.add(refused)
                preferences = [p for p in preferences if p not in avoid]
                log.warning("%s confirmed sold out – remaining attempts will target %s",
                            refused, " → ".join(preferences) or "the earliest open slot")
            if attempt < MAX_ATTEMPTS:
                log.info("Retrying in %d seconds…", RETRY_DELAY)
                time.sleep(RETRY_DELAY)

    reason = str(last_exc) if last_exc else "unknown error"
    log.error("Giving up after %d attempt(s)", attempts_used)
    # Both pictures: the first attempt's page is where the cause usually is, the last
    # one is what the site ended up showing. Sending only the last is how the
    # 2026-07-29 failure email came to carry a screenshot of a successful booking.
    send_email(
        subject=f"[Swim Booker] FAILED – {reason[:60]}",
        body=(
            f"Swim lane booking failed after {attempts_used} attempt(s).\n\n"
            f"Last error: {reason}\n\n"
            f"Attached: first_failure.png (attempt 1) and last_run.png (attempt "
            f"{attempts_used}).\n\n{last_tb}"
        ),
        attachment_path=[FIRST_FAILURE_FILE, SCREENSHOT_FILE],
    )
    send_slack(f":x: *Swim booking FAILED* after {attempts_used} attempt(s) — {reason[:120]}")
    trim_log()
    sys.exit(1)


if __name__ == "__main__":
    main()
