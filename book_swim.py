import os
import sys
import logging
import smtplib
import traceback
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

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

LOGIN_URL = "https://www.mytrilogylife.com"
EVENTS_URL = "https://members.mytrilogylife.com/events"
TARGET_TIME = "8:00 AM"
EVENT_NAME = "Indoor Lap Pool Reservations"

LOG_FILE = BASE_DIR / "swim_booker.log"
SCREENSHOT_FILE = BASE_DIR / "failure_screenshot.png"

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

def send_email(subject: str, body: str, attachment_path: Path | None = None) -> None:
    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if attachment_path and attachment_path.exists():
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={attachment_path.name}")
        msg.attach(part)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_USER, NOTIFY_EMAIL, msg.as_string())
        log.info("Email sent: %s", subject)
    except Exception:
        log.error("Failed to send email:\n%s", traceback.format_exc())


def fail(page, reason: str) -> None:
    log.error("FAILED: %s", reason)
    try:
        page.screenshot(path=str(SCREENSHOT_FILE))
        log.info("Screenshot saved to %s", SCREENSHOT_FILE)
    except Exception:
        pass
    send_email(
        subject=f"[Swim Booker] FAILED – {reason[:60]}",
        body=f"Swim lane booking failed.\n\nReason: {reason}\n\n{traceback.format_exc()}",
        attachment_path=SCREENSHOT_FILE,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    target_date = date.today() + timedelta(days=1)
    day_name = target_date.strftime("%A")   # e.g. "Saturday"
    log.info("Booking %s %s slot for %s", TARGET_TIME, EVENT_NAME, target_date.isoformat())

    with sync_playwright() as p:
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
        # 2. Events page
        # ------------------------------------------------------------------
        log.info("Navigating to %s", EVENTS_URL)
        page.goto(EVENTS_URL, wait_until="networkidle")

        # ------------------------------------------------------------------
        # 3. Find Indoor Lap Pool Reservations for tomorrow
        # ------------------------------------------------------------------
        target_date_str = target_date.strftime("%-m/%-d/%Y")   # e.g. "4/26/2026"
        alt_date_str    = target_date.strftime("%B %-d")         # e.g. "April 26"

        log.info("Looking for '%s' on %s", EVENT_NAME, target_date_str)

        event_link = None

        # Strategy A: find an <a> tag whose text contains event name + tomorrow's date
        for link in page.locator("a").all():
            try:
                text = link.inner_text(timeout=2000)
            except Exception:
                continue
            if EVENT_NAME.lower() in text.lower() and (target_date_str in text or alt_date_str in text):
                event_link = link
                log.info("Found event link (Strategy A): %s", text.strip()[:80])
                break

        # Strategy B: find a row/cell containing the event name, then grab its nearest <a>
        if event_link is None:
            cells = page.locator(f"td:has-text('{EVENT_NAME}'), li:has-text('{EVENT_NAME}'), div:has-text('{EVENT_NAME}')").all()
            for cell in cells:
                try:
                    cell_text = cell.inner_text(timeout=2000)
                except Exception:
                    continue
                if target_date_str in cell_text or alt_date_str in cell_text:
                    # Try to find a link in the same row/container
                    row_link = cell.locator("xpath=ancestor::tr//a | ancestor::li//a | a").first
                    if row_link.count() > 0:
                        event_link = row_link
                        log.info("Found event link (Strategy B row link)")
                        break
                    # Fallback: click the href of the row if the row itself is a link
                    href = cell.evaluate("el => el.closest('a')?.href || ''")
                    if href:
                        page.goto(href, wait_until="networkidle")
                        log.info("Navigated directly to event href (Strategy B href): %s", href)
                        break

        if event_link is None and "Indoor Lap Pool" not in page.url:
            fail(page, f"Could not find a clickable link for '{EVENT_NAME}' on {target_date_str}")

        if event_link is not None:
            event_link.scroll_into_view_if_needed()
            event_link.click()
            page.wait_for_load_state("networkidle")

        log.info("Opened event detail page: %s", page.url)

        # ------------------------------------------------------------------
        # 4. Find 8 AM slot and register
        # ------------------------------------------------------------------
        log.info("Looking for %s slot", TARGET_TIME)

        # Look for a register/book button near the "8:00 AM" / "8:00am" text
        time_variants = ["8:00 AM", "8:00am", "8:00 am", "8AM", "8 AM"]
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

        # Fallback: any Register button on the page
        if register_btn is None:
            btn = page.locator(
                "button:has-text('Register'), a:has-text('Register'), input[value='Register']"
            ).first
            if btn.count() > 0:
                register_btn = btn
                log.warning("Using fallback register button (could not isolate 8 AM slot)")

        if register_btn is None:
            fail(page, f"Could not find a Register button for the {TARGET_TIME} slot")

        register_btn.click()
        page.wait_for_load_state("networkidle")

        # ------------------------------------------------------------------
        # 5. Confirm if a confirmation dialog/button appears
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
        # 6. Verify success
        # ------------------------------------------------------------------
        page_text = page.inner_text("body").lower()
        success_indicators = ["registered", "confirmed", "success", "thank you", "you are registered"]
        if not any(kw in page_text for kw in success_indicators):
            fail(page, "Registration submitted but no success confirmation found on page")

        log.info("SUCCESS – registered for %s %s on %s", TARGET_TIME, EVENT_NAME, target_date.isoformat())

        browser.close()

    send_email(
        subject=f"[Swim Booker] SUCCESS – {day_name} {TARGET_TIME} booked",
        body=(
            f"Swim lane successfully booked!\n\n"
            f"Event:  {EVENT_NAME}\n"
            f"Date:   {target_date.strftime('%A, %B %-d, %Y')}\n"
            f"Time:   {TARGET_TIME}\n"
        ),
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log.critical("Unhandled exception:\n%s", traceback.format_exc())
        send_email(
            subject="[Swim Booker] FAILED – unhandled exception",
            body=traceback.format_exc(),
        )
        sys.exit(1)
