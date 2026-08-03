# TODO — weekday early-lane booking

**Status: done and live as of 2026-07-28**, and confirmed booking on 2026-07-29.
Tue/Thu early-lane booking is enabled in `SCHEDULE` and in cron. Nothing is outstanding;
what follows is what to watch and why things are the way they are.

```cron
58 7 * * 1,3,5,6 /home/gary/projects/swim-booker/run.sh
```

| Run day | Books | Plan |
|---|---|---|
| Monday | Tuesday | 6:00 AM, no fallback |
| Wednesday | Thursday | 6:00 AM, no fallback |
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

If the weekday early lane is ever really gone, expect:

```
6:00 AM unavailable for <date> – not booking a fallback
```

**That message is the design working, not a bug.** It is a `terminal` failure, so it
stops retrying immediately rather than burning ~30s per pointless attempt, and it
sends a failure email and Slack message.

## 2026-08-03 — the site moved under us, three ways at once

Monday 07:58 → Tuesday 8/4. All five attempts failed with `'Indoor Lap Pool
Reservation' for 8/4/2026 never appeared on the events page`, while the event was
listed the whole time. Three independent site-side changes, none of them ours:

1. **The title's punctuation moved.** Tuesday was published as `Tuesday August, 4`;
   Monday, the same morning, read `Monday, August 3`. The matcher compared raw
   substrings, so `August 4` was not found. **This is the one that caused the
   failure.** Titles are typed by hand at the club — treat their formatting as
   unreliable input, not as a contract.
2. **The weekday grid was re-timed.** `6:15/7:00/7:45/8:30` became
   `6:00/6:45/7:30/8:15`, confirmed against both Monday's and Tuesday's events.
   `6:15 AM` no longer exists, so even with the date fixed the run would have failed
   terminally — correctly, but with nothing booked.
3. **One day is now several events.** Tuesday was split into `6:00AM-8:55AM`
   (`1853228`) and `10:00AM-5:00PM` (`1849598`). Only the first holds the 6:00 lane.
   Monday was still a single all-day event, so this is per-day, not a global switch.

Fixed in the same commit: normalised date comparison with token boundaries, every
matching anchor returned rather than the first, `order_candidates()` to pick which,
and `SCHEDULE`/`DRY_RUN_PROBE` moved to `6:00 AM`. Verified by dry run and then by an
ad-hoc live run that booked `6:00am-6:40am` for 8/4 at 08:18 — the lane was still
open, so nothing was lost.

**Open question for Gary, not decided here:** the new grid's second slot is
`6:45am-7:25am`, which ends later than the old `6:15am-6:55am` did. It was left out
of `SCHEDULE` deliberately — adding it would widen the "gets Gary to work on time"
rule, which is his call, not a translation of the old config.

## Decisions — do not re-litigate

- **The weekday early lane has no fallback.** If it is gone, book nothing and notify.
  The first lane of the day is the only one that still gets Gary to work on time; a "booked" email for a midday
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
- **The event matcher does not guess.** Every match must come from one anchor that
  names both the event and the date itself, with an `/events/<id>` href — never from a
  container, never inferred from a neighbour. Do not reinstate a nearest-anchor
  fallback: it returned the top-nav EVENTS link, and once restricted to event links,
  returned *a different day's event* — verified 2026-07-28, Thursday 7/30 resolving
  to Tuesday's `1849591`. A plausible URL that books the wrong day beats any URL
  check. If it stops matching, fail loudly and find out why.
- **Strict about which anchor, loose about punctuation.** Those are different things.
  The anchor must name the event and the date itself and link to `/events/<id>` — but
  the date comparison normalises both sides to lowercase words, because the club
  hand-types titles and shipped `Tuesday August, 4`. Token boundaries do the real
  work: `august 4` must not match `august 14`.
- **A day can be published as more than one event.** All matching anchors are
  returned; `order_candidates()` ranks them by whether the listing's own text
  advertises a preferred time. It decides which page to *open* only — `choose_slot()`
  still decides what is *booked*, so a bad ranking cannot book the wrong time.
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
