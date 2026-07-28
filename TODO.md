# TODO — weekday 6:15 AM expansion

**Status: done and live as of 2026-07-28.** Tue/Thu 6:15 AM booking is enabled in
`SCHEDULE` and in cron. Nothing is outstanding; what follows is what to watch and why
things are the way they are.

```cron
58 7 * * 1,3,5,6 /home/gary/projects/swim-booker/run.sh
```

| Run day | Books | Plan |
|---|---|---|
| Monday | Tuesday | 6:15 AM, no fallback |
| Wednesday | Thursday | 6:15 AM, no fallback |
| Friday | Saturday | 8:00 → 8:45, else a later lane between 8:00 and 9:30 |
| Saturday | Sunday | same as Friday |

## Watch the first live weekday run

**Wednesday, 07:58 → books Thursday.** (Monday is the other weekday trigger.) In
`swim_booker.log`, expect the hold at the bell, the grid, then either a 6:15 booking
or:

```
6:15 AM unavailable for 2026-07-30 – not booking a fallback
```

**That second message is the design working, not a bug.** It is a `terminal` failure,
so it stops retrying immediately rather than burning ~30s per pointless attempt, and
it sends a failure email and Slack message.

Grid evidence from 2026-07-28 (Wednesday's event, read at 08:05, five minutes after
publish, on a day already flagged `ALMOST FULL`):

```
OPEN   6:15am-6:55am Indoor Pool - Free
OPEN   7:00am-7:40am Indoor Pool - Free
taken  7:45am-8:25am Water Fitness - Free
taken  8:30am-9:10am Indoor Pool - Free
```

Early-morning weekday lanes look genuinely attainable — better than Monday's
sold-out-in-eight-minutes reading suggested.

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

## Cron numbering

Cron day numbers (1, 3, 5, 6) and `SCHEDULE` keys (1, 3, 5, 6) are the same digits by
coincidence: cron numbering is Python's `weekday()` plus one, and the day booked is
always the day after the run, so the two offsets cancel. Change one and re-derive the
other rather than copying it.
