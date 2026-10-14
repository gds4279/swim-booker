"""Offline tests for book_swim's pure functions and the rules CLAUDE.md says must hold.

    .venv/bin/python -m unittest discover -s tests

No network, no login, nothing booked, nothing sent. Credentials are dummies set
before import (load_dotenv never overrides an existing variable), SMTP and HTTP are
stubbed to fail loudly, and logging is disabled so nothing lands in swim_booker.log.
The event-matcher tests run the real _FIND_EVENT_JS in a local Chromium page built
with set_content; they skip if Playwright's browser is not installed.

Most cases are named after the incident that made the rule - keep them that way.
"""
import json
import logging
import os
import smtplib
import sys
import unittest
import urllib.request
from datetime import date, datetime, time
from pathlib import Path

for _k, _v in {"MYTRILOGY_USERNAME": "test-user", "MYTRILOGY_PASSWORD": "test-pass",
               "SMTP_USER": "test@example.invalid", "SMTP_APP_PASSWORD": "x",
               "NOTIFY_EMAIL": "test@example.invalid", "SLACK_WEBHOOK_URL": ""}.items():
    os.environ[_k] = _v

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import book_swim as b  # noqa: E402

logging.disable(logging.CRITICAL)


def _refuse(*_a, **_k):
    raise AssertionError("a test tried to talk to the network")


smtplib.SMTP = _refuse
urllib.request.urlopen = _refuse


def opt(text, disabled=False, value=None):
    return {"value": value or text, "text": text, "disabled": disabled}


WEEKDAY = [opt("6:00am-6:40am Indoor Pool - Free"), opt("6:45am-7:25am Indoor Pool - Free"),
           opt("7:30am-8:10am Indoor Pool - Free"), opt("9:00am-10:00am - Free"),
           opt("10:45am-11:25am Indoor Lap - Free")]
WEEKEND = [opt("8:00am - 8:40am Indoor Pool - Free"), opt("8:45am - 9:25am Indoor Pool - Free"),
           opt("9:30am - 10:10am Indoor Pool - Free"), opt("10:15am - 10:55am Indoor Pool - Free"),
           opt("11:45am-12:25pm Water Fitness - Free"), opt("12:30pm-1:25pm - Free"),
           opt("8:30pm - 9:00pm Indoor Pool - Free")]


def closed(options, *starts):
    return [dict(o, disabled=o["text"].startswith(starts)) for o in options]


class Constants(unittest.TestCase):
    def test_event_name_is_singular(self):
        # Weekday titles say "...Reservation", weekend "...Reservations"; only the
        # singular stem matches both (2026-07-27).
        self.assertEqual(b.EVENT_NAME, "Indoor Lap Pool Reservation")

    def test_open_time_is_registration_not_a_swim_time(self):
        self.assertEqual(b.OPEN_TIME, time(8, 0, 0))

    def test_lane_type(self):
        self.assertEqual(b.LANE_TYPE, "indoor")


class Schedule(unittest.TestCase):
    def test_weekdays_book_6am_with_no_fallback(self):
        for d in (date(2026, 10, 13), date(2026, 10, 15)):          # Tue, Thu
            plan = b.schedule_for(d)
            self.assertEqual(list(plan.preferences), ["6:00 AM"])
            self.assertFalse(plan.any_open)

    def test_weekends_fall_back_no_later_than_930(self):
        for d in (date(2026, 10, 17), date(2026, 10, 18)):          # Sat, Sun
            plan = b.schedule_for(d)
            self.assertEqual(list(plan.preferences), ["8:00 AM", "8:45 AM"])
            self.assertTrue(plan.any_open)
            self.assertEqual(plan.latest, time(9, 30))

    def test_unscheduled_day_books_nothing_but_probe_can_look(self):
        wed = date(2026, 10, 14)
        self.assertIsNone(b.schedule_for(wed))
        self.assertEqual(b.schedule_for(wed, probe=True), b.DRY_RUN_PROBE)

    def test_cron_days_book_the_scheduled_days(self):
        # cron 58 7 * * 1,3,5,6 books the next day; that next day must be in SCHEDULE.
        for cron_dow in (1, 3, 5, 6):
            booked_weekday = cron_dow % 7          # cron dow - 1 + 1 day = Python weekday
            self.assertIn(booked_weekday, b.SCHEDULE, cron_dow)


class SlotStart(unittest.TestCase):
    def test_parses(self):
        self.assertEqual(b.slot_start("9:30am - 10:10am Indoor Pool"), time(9, 30))
        self.assertEqual(b.slot_start("12:30pm-1:25pm Indoor Lap"), time(12, 30))
        self.assertEqual(b.slot_start("12:00am-12:40am Indoor Pool"), time(0, 0))
        self.assertEqual(b.slot_start("8:30pm - 9:00pm Indoor Pool"), time(20, 30))
        self.assertEqual(b.slot_start("8:00 AM"), time(8, 0))

    def test_unparseable_is_none_never_a_guess(self):
        self.assertIsNone(b.slot_start("Water Fitness"))
        self.assertIsNone(b.slot_start(""))


class ChooseSlot(unittest.TestCase):
    def test_weekday_books_preference(self):
        _, text, matched = b.choose_slot(WEEKDAY, ["6:00 AM"], set(), any_open=False)
        self.assertTrue(text.startswith("6:00am"))
        self.assertEqual(matched, "6:00 AM")

    def test_weekday_books_nothing_when_6am_is_gone(self):
        self.assertEqual(b.choose_slot(closed(WEEKDAY, "6:00"), ["6:00 AM"], set(), any_open=False),
                         (None, None, None))

    def test_weekday_regrid_books_nothing(self):
        # 2026-08-03: 6:15 stopped existing. With any_open=False that is a clean failure.
        self.assertEqual(b.choose_slot(WEEKDAY, ["6:15 AM"], set(), any_open=False),
                         (None, None, None))

    def test_preference_at_right_time_but_not_a_lane_is_rejected(self):
        opts = [opt("8:00am - 8:40am Water Fitness - Free"), opt("8:45am - 9:25am Indoor Pool - Free")]
        _, text, matched = b.choose_slot(opts, ["8:00 AM", "8:45 AM"], set())
        self.assertTrue(text.startswith("8:45am"))
        self.assertEqual(matched, "8:45 AM")

    def test_weekend_pivots_to_845_when_8_is_gone(self):
        _, text, matched = b.choose_slot(closed(WEEKEND, "8:00"), ["8:00 AM", "8:45 AM"], set(),
                                         latest=time(9, 30))
        self.assertTrue(text.startswith("8:45am"))
        self.assertEqual(matched, "8:45 AM")

    def test_weekend_fallback_never_earlier_than_first_preference(self):
        opts = [opt("6:00am-6:40am Indoor Pool - Free"), opt("8:00am - 8:40am Indoor Pool", True),
                opt("8:45am - 9:25am Indoor Pool", True), opt("9:30am - 10:10am Indoor Pool - Free")]
        _, text, matched = b.choose_slot(opts, ["8:00 AM", "8:45 AM"], set(), latest=time(9, 30))
        self.assertTrue(text.startswith("9:30am"))
        self.assertIsNone(matched)

    def test_weekend_fallback_never_later_than_latest(self):
        # Without the cutoff a busy Saturday books an 8:30 PM lane and calls it success.
        opts = closed(WEEKEND, "8:00", "8:45", "9:30", "10:15")
        self.assertEqual(b.choose_slot(opts, ["8:00 AM", "8:45 AM"], set(), latest=time(9, 30)),
                         (None, None, None))

    def test_fallback_skips_classes_and_untyped_slots(self):
        opts = closed(WEEKEND, "8:00", "8:45", "9:30", "10:15")
        _, text, _ = b.choose_slot(opts, ["8:00 AM"], set())
        self.assertTrue(text.startswith("8:30pm"))           # not Water Fitness, not untyped 12:30

    def test_avoided_time_is_never_resubmitted(self):
        # 2026-07-24: 8:00 looked open but the site refused it as sold out.
        _, text, _ = b.choose_slot(WEEKEND, ["8:00 AM", "8:45 AM"], {"8:00 AM"}, latest=time(9, 30))
        self.assertTrue(text.startswith("8:45am"))

    def test_unparseable_start_is_skipped_in_fallback(self):
        opts = [opt("Morning Indoor Pool - Free"), opt("9:00am-9:40am Indoor Pool - Free")]
        _, text, _ = b.choose_slot(opts, ["8:00 AM"], set())
        self.assertTrue(text.startswith("9:00am"))


class ClassifyResult(unittest.TestCase):
    def test_success(self):
        self.assertEqual(b.classify_result("Success! You're going"), (None, True))
        self.assertEqual(b.classify_result("Success! You’re going"), (None, True))   # curly quote

    def test_error_beats_success(self):
        err, ok = b.classify_result("Ticket Purchased ... Unable to complete registration")
        self.assertEqual((err, ok), ("unable to complete registration", False))

    def test_breadcrumb_and_thank_you_are_not_success(self):
        # 2026-07-24 false SUCCESS: the breadcrumb is rendered on every step.
        self.assertEqual(b.classify_result("1.TICKETS 2.PAYMENTS 3.CONFIRMATION Thank you"), (None, False))

    def test_empty_is_not_success(self):
        self.assertEqual(b.classify_result(""), (None, False))


class PurchasedSlot(unittest.TestCase):
    def test_reads_ticket_purchased_heading(self):
        text = "Same-day Registration NOT available\n Ticket Purchased\n6:00am-6:40am Indoor Pool\n$0.00\n"
        self.assertEqual(b._PURCHASED_RE.search(text).group(1), "6:00am-6:40am Indoor Pool")

    def test_absent_when_not_held(self):
        self.assertIsNone(b._PURCHASED_RE.search(" Ticket Prices\n6:00am-6:40am Indoor Pool\n"))


class ParseTicketPrices(unittest.TestCase):
    PAGE = (" Ticket Purchased\n6:00am-6:40am Indoor Pool\n$0.00\n Ticket Prices\n"
            "6:00am-6:40am Indoor Pool\n$0.00 (2 Tickets Left)\n7:30am-8:10am Indoor Pool\n$0.00 (Sold Out)\n"
            "5:00pm - 8:10pm Adult Water VolleyBall\n$0.00 (Sold Out)\nDate and time\n"
            "Bocce Ball Club | Open Play Times\nOct 15, 8:00 AM - 9:30 AM\n")

    def test_parses_grid_after_the_heading_only(self):
        g = b.parse_ticket_prices(self.PAGE)
        self.assertEqual([o["text"] for o in g], ["6:00am-6:40am Indoor Pool", "7:30am-8:10am Indoor Pool",
                                                  "5:00pm - 8:10pm Adult Water VolleyBall"])
        self.assertEqual([o["disabled"] for o in g], [False, True, True])
        self.assertTrue(all(o["value"].startswith("grid-") for o in g))

    def test_no_list(self):
        self.assertEqual(b.parse_ticket_prices("nothing here"), [])


class OrderCandidates(unittest.TestCase):
    def test_split_day_puts_the_morning_listing_first(self):
        # 2026-08-03: Tuesday published as two events; only the first holds 6:00.
        late = {"href": "/events/1849598", "text": "Indoor Lap Pool Reservation | Tuesday August, 4 10:00AM-5:00PM"}
        early = {"href": "/events/1853228", "text": "Indoor Lap Pool Reservation | Tuesday August, 4 6:00AM-8:55AM"}
        self.assertEqual(b.order_candidates([late, early], ["6:00 AM"]), [early, late])

    def test_unrecognised_windows_keep_dom_order_and_drop_nothing(self):
        c = [{"href": "/events/1", "text": "A"}, {"href": "/events/2", "text": "B"}]
        self.assertEqual(b.order_candidates(c, ["6:00 AM"]), c)


class SecondsUntilOpen(unittest.TestCase):
    def test_holds_from_758(self):
        self.assertEqual(b.seconds_until_open(datetime(2026, 10, 14, 7, 58, 0)), 120.0)

    def test_after_bell_proceeds(self):
        self.assertEqual(b.seconds_until_open(datetime(2026, 10, 14, 8, 0, 1)), 0.0)

    def test_never_holds_past_the_cap(self):
        self.assertEqual(b.seconds_until_open(datetime(2026, 10, 14, 7, 0, 0)), 0.0)


class CookieImport(unittest.TestCase):
    def test_cookie_editor_json(self):
        raw = json.dumps([
            {"domain": "members.mytrilogylife.com", "name": "mtl_session", "value": "a",
             "expirationDate": 1800000000.5, "httpOnly": True, "secure": True, "sameSite": "lax"},
            {"domain": ".mytrilogylife.com", "name": "r", "value": "b", "sameSite": "no_restriction"},
            {"domain": ".google.com", "name": "NID", "value": "g"}])
        c = b.cookies_to_storage_state(raw)["cookies"]
        self.assertEqual([x["name"] for x in c], ["mtl_session", "r"])     # other sites dropped
        self.assertEqual(c[0]["sameSite"], "Lax")
        self.assertEqual((c[1]["sameSite"], c[1]["secure"]), ("None", True))

    def test_cookie_header(self):
        c = b.cookies_to_storage_state("Cookie: a=1; b=x=y")["cookies"]
        self.assertEqual([(x["name"], x["value"]) for x in c], [("a", "1"), ("b", "x=y")])

    def test_nothing_relevant_is_an_error(self):
        with self.assertRaises(ValueError):
            b.cookies_to_storage_state('[{"domain": ".google.com", "name": "a", "value": "b"}]')


class SessionAlert(unittest.TestCase):
    def test_cron_run_days_book_scheduled_days(self):
        self.assertEqual(sorted((d + 1) % 7 for d in b.CRON_RUN_DAYS), sorted(b.SCHEDULE))

    def test_next_run(self):
        wed_evening = datetime(2026, 10, 14, 19, 0)
        self.assertEqual(b.next_run(wed_evening), datetime(2026, 10, 16, 7, 58))     # Fri
        self.assertEqual(b.next_run(datetime(2026, 10, 16, 7, 0)), datetime(2026, 10, 16, 7, 58))
        self.assertEqual(b.next_run(datetime(2026, 10, 18, 19, 0)), datetime(2026, 10, 19, 7, 58))  # Sun→Mon

    def test_session_status(self):
        E = "https://members.mytrilogylife.com/events"
        self.assertEqual(b.session_status(True, E, False), "live")
        self.assertEqual(b.session_status(False, "https://members.mytrilogylife.com/login", False), "dead")
        # A login form without the redirect is still dead - never downgraded to "unknown".
        self.assertEqual(b.session_status(False, E, True), "dead")
        self.assertEqual(b.session_status(False, "about:blank", False), "unknown")

    def test_crashed_check_still_alerts(self):
        # Under cron output goes to /dev/null: a crash must still reach Slack.
        sent = []
        saved = (b.load_session, b.sync_playwright, b.send_slack, b.send_email)

        def boom():
            raise RuntimeError("chromium failed to launch")

        b.load_session = lambda: {"cookies": []}
        b.sync_playwright = boom
        b.send_slack = lambda msg: sent.append(("slack", msg))
        b.send_email = lambda *a, **k: sent.append(("email", a))
        try:
            self.assertFalse(b.check_session(notify=True))
        finally:
            b.load_session, b.sync_playwright, b.send_slack, b.send_email = saved
        self.assertEqual([kind for kind, _ in sent], ["slack"])
        self.assertIn("could not check", sent[0][1])

    def test_live_is_silent(self):
        self.assertIsNone(b.session_alert("live", datetime(2026, 10, 14, 19, 0)))

    def test_dead_alerts_slack_and_email_with_steps(self):
        slack, email = b.session_alert("dead", datetime(2026, 10, 14, 19, 0))
        for text in (slack, email):
            self.assertIn("Friday 7:58 AM", text)
            self.assertIn("--import-cookies", text)

    def test_unreachable_is_slack_only(self):
        slack, email = b.session_alert("unknown", datetime(2026, 10, 14, 19, 0))
        self.assertIn("could not check", slack)
        self.assertEqual(email, "")


class EmailRecipients(unittest.TestCase):
    def send(self, **kw):
        sent = {}

        class FakeSMTP:
            def __init__(self, *a): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, frm, to, msg): sent.update(to=to, msg=msg)

        real, b.smtplib.SMTP = b.smtplib.SMTP, FakeSMTP
        try:
            b.send_email("s", "body", **kw)
        finally:
            b.smtplib.SMTP = real
        return sent

    def test_default_ccs(self):
        sent = self.send()
        self.assertEqual(sent["to"], [b.NOTIFY_EMAIL, b.CC_EMAIL])
        self.assertIn("Cc: " + b.CC_EMAIL, sent["msg"])

    def test_no_cc(self):
        sent = self.send(cc=None)
        self.assertEqual(sent["to"], [b.NOTIFY_EMAIL])
        self.assertNotIn("Cc:", sent["msg"])


class EventMatcher(unittest.TestCase):
    """The real _FIND_EVENT_JS, run against hand-built listing pages."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls._pw = sync_playwright().start()
            cls._browser = cls._pw.chromium.launch(headless=True)
        except Exception as exc:                       # browser not installed, etc.
            raise unittest.SkipTest(f"Playwright unavailable: {exc}")
        cls.page = cls._browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def match(self, html, day):
        self.page.set_content(html)
        return self.page.evaluate(b._FIND_EVENT_JS, [b.EVENT_NAME, day.strftime("%-m/%-d/%Y"),
                                                      day.strftime("%B %-d")])

    E = "https://members.mytrilogylife.com/events/"

    def test_retyped_title_still_matches(self):
        # 2026-08-03: "Tuesday August, 4" was missed by a raw substring match.
        got = self.match(f'<a href="{self.E}1853228">Indoor Lap Pool Reservation | Tuesday August, 4</a>',
                         date(2026, 8, 4))
        self.assertEqual([g["href"] for g in got], [self.E + "1853228"])

    def test_august_4_does_not_match_august_14(self):
        got = self.match(f'<a href="{self.E}1">Indoor Lap Pool Reservation | Friday, August 14</a>',
                         date(2026, 8, 4))
        self.assertEqual(got, [])

    def test_plural_weekend_title_matches(self):
        got = self.match(f'<a href="{self.E}2">Indoor Lap Pool Reservations | Saturday, October 17</a>',
                         date(2026, 10, 17))
        self.assertEqual(len(got), 1)

    def test_never_guesses_from_a_container_or_neighbour(self):
        # 2026-07-28: a container-scan fallback returned the nav /events link, and then a
        # different day's event. A date mentioned outside the anchor must not match.
        html = (f'<nav><a href="https://members.mytrilogylife.com/events">EVENTS</a></nav>'
                f'<div>Indoor Lap Pool Reservation | Thursday, July 30'
                f'  <a href="{self.E}1849591">Indoor Lap Pool Reservation | Tuesday, July 28</a></div>')
        self.assertEqual(self.match(html, date(2026, 7, 30)), [])

    def test_non_detail_link_is_ignored(self):
        html = '<a href="https://members.mytrilogylife.com/events">Indoor Lap Pool Reservation | Thursday, July 30</a>'
        self.assertEqual(self.match(html, date(2026, 7, 30)), [])

    def test_split_day_returns_every_listing(self):
        html = (f'<a href="{self.E}1849598">Indoor Lap Pool Reservation | Tuesday August, 4 10:00AM-5:00PM</a>'
                f'<a href="{self.E}1853228">Indoor Lap Pool Reservation | Tuesday August, 4 6:00AM-8:55AM</a>')
        self.assertEqual(len(self.match(html, date(2026, 8, 4))), 2)


if __name__ == "__main__":
    unittest.main()
