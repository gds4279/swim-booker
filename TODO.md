# TODO — weekday 6:15 AM expansion

**Status: done and live as of 2026-07-28**, and confirmed booking on 2026-07-29.
Tue/Thu 6:15 AM booking is enabled in `SCHEDULE` and in cron. Nothing is outstanding;
what follows is what to watch and why things are the way they are.

```cron
58 7 * * 1,3,5,6 /home/gary/projects/swim-booker/run.sh
```

| Run day | Books | Plan |
|---|---|---|
| Monday | Tuesday | 6:15 AM, no fallback |
| Wednesday | Thursday | 6:15 AM, no fallback |
| Friday | Saturday | 8:00 → 8:45, else a later lane between 8:00 and 9:30 |
| Saturday | Sunday | same as Friday |

## First live weekday run: 2026-07-29 — booked, but reported as failed

Wednesday 07:58 → Thursday 7/30. **The booking worked**: `6:15am-6:55am Indoor Pool`,
reached at 08:00:14 and submitted at 08:00:20. Trilogy sent its confirmation email.
The 6:15 lane was open at the bell, which settles the question the weekday expansion
was waiting on — early weekday lanes are attainable.

The script then emailed `FAILED after 5 attempt(s)`. Fixed in `5c74c25`; the reasoning
is in CLAUDE.md under "The wizard is not the last word" and "Never wait on a page-wide
selector". In short:

- Both waits for confirmation text used a **page-wide** selector, and the event page
  around the wizard already contains every phrase they waited for — `(Sold Out)` on
  each gone slot, and a `Ticket Purchased` heading once you hold a ticket. 15 visible
  matches, measured. So the wait returned instantly and the verdict was read 822ms
  after submitting, before the wizard had painted.
- Attempts 2–5 then died on `no member row for Gary`, which is what the wizard shows
  once you *already hold a ticket*. The proof of success was reported as the cause of
  failure.

**Still worth watching: the first weekend run under these changes** (Friday 07:58 →
Saturday). The weekend path has a fallback enabled, so a misread confirmation there
was the case that could have produced a genuine double-booking rather than four
harmless refusals.

If a weekday 6:15 is ever really gone, expect:

```
6:15 AM unavailable for <date> – not booking a fallback
```

**That message is the design working, not a bug.** It is a `terminal` failure, so it
stops retrying immediately rather than burning ~30s per pointless attempt, and it
sends a failure email and Slack message.

## Decisions — do not re-litigate

- **Weekday 6:15 has no fallback.** If it is gone, book nothing and notify. 6:15 is
  the only slot that still gets Gary to work on time; a "booked" email for a midday
  lane is worse than a failure notice.
- **Weekend fallback is bounded at both ends**: never earlier than 8:00 AM, never
  later than 9:30 AM (inclusive — `9:30am - 10:10am` books, `10:15am` does not). The
  floor is derived from `preferences[0]`, so it tracks the target automatically. An
  explicit 8:00/8:45 preference is never subject to either bound.
- **`OPEN_TIME` is 08:00 for every day** and means *when registration opens*, not a
  swim time. Confirmed from the site's own text: *"Reservations may be made at 8:00am
  the day prior."* Never "fix" it to a swim time.
- **`EVENT_NAME` is singular on purpose** — a substring of both the weekday
  (`…Reservation`) and weekend (`…Reservations`) titles. Do not "correct" it.
- **The event matcher does not guess.** It requires a single anchor naming both the
  event and the date, with an `/events/<id>` href. Do not reinstate a nearest-anchor
  fallback: it returned the top-nav EVENTS link, and once restricted to event links,
  returned *a different day's event* — verified 2026-07-28, Thursday 7/30 resolving
  to Tuesday's `1849591`. A plausible URL that books the wrong day beats any URL
  check. If it stops matching, fail loudly and find out why.
- **`Water Fitness` is a class, not a lane**, and shares the dropdown with lanes. The
  `LANE_TYPE` filter applies to preferred times too, not just fallbacks.
- **The event page outranks the wizard.** `Ticket Purchased` on the event page is the
  site's own answer to "what do I hold?", and it names the slot. It decides before the
  wizard opens, on a missing member row, and before failing on an unreadable wizard.
  Do not let a retry re-enter the wizard without that check: on a weekend the fallback
  would happily book a second lane at another time, and the first is never released.
- **Never wait on a page-wide selector for confirmation text.** The ticket grid behind
  the wizard answers it instantly. Wait on the wizard's own text, and treat an
  unpainted wizard as *no verdict yet* rather than as failure.

## Timing — settled, 2026-07-31

The dropdown is reached ~08:00:18–20. That is **not** fixable by starting earlier:

- Clocks agree. The server's HTTP `Date` was sampled against ours by catching its
  second boundary: median offset **−0.017s** over 26 boundaries (range −0.18 to
  +0.08, inside the ±0.15s measurement bracket). No CDN in front, so that is the
  application server's clock. Local box is NTP-synced. **Do not re-investigate this.**
- The site publishes tomorrow's event around **08:00:08–14**, not at 08:00:00.
- Register-click to wizard render costs another ~6s.

The one place seconds could still be won: each listing re-check is a full
`page.goto(..., wait_until="networkidle")` at ~4s, so publication can go unnoticed for
up to ~4s. A lighter `wait_until` for re-checks would tighten that. Not done — it
touches the most time-critical path in the script and every slot was still open at
08:00:20 on 07-31, so there is no evidence it is needed yet.

## Cron numbering

Cron day numbers (1, 3, 5, 6) and `SCHEDULE` keys (1, 3, 5, 6) are the same digits by
coincidence: cron numbering is Python's `weekday()` plus one, and the day booked is
always the day after the run, so the two offsets cancel. Change one and re-derive the
other rather than copying it.
