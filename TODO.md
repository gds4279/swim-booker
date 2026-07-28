# TODO — weekday 6:15 AM expansion

Goal: add **Tuesday and Thursday 6:15 AM** swims. Weekday booking is still switched
off in cron; weekend booking runs Fri/Sat as normal throughout.

---

## Status: one step left

The 2026-07-28 dry run answered the blocking question and exposed a bug that would
have broken the **weekend** runs. Both are dealt with.

### ✅ RESOLVED — a 6:15 AM lane exists

Read live from Wednesday's event (`/events/1849592`) at 08:05 on 2026-07-28:

```
OPEN   6:15am-6:55am Indoor Pool - Free
OPEN   7:00am-7:40am Indoor Pool - Free
taken  7:45am-8:25am Water Fitness - Free
taken  8:30am-9:10am Indoor Pool - Free
...
OPEN   4:15pm - 5:10pm Indoor Lap Pool - Free
```

The 6:15 lane was still open five minutes after the event published, on a day flagged
`ALMOST FULL`. Early-morning weekday slots look genuinely attainable — better than the
sold-out-in-eight-minutes picture from Monday suggested.

Two things this grid taught us, both now handled in code:

- **Slot text format differs.** Weekday rows are `6:15am-6:55am` (no spaces around the
  dash); weekend rows are `8:00am - 8:40am`. Both parse.
- **Not every ticket is a lane.** `Water Fitness` is a class sharing the same dropdown,
  and one row has no type at all. `LANE_TYPE` now filters these out — including at a
  preferred time, since a class at 6:15 is the wrong booking, not a lesser one.

### ✅ FIXED — the staging bug that would have broken Friday

The 07:58 dry run reached the ticket wizard of **an unrelated event**:

```
08:00:04  On event detail page: https://members.mytrilogylife.com/events   ← the LIST page
08:00:04  Using fallback register button (could not isolate 8 AM slot)
08:00:06  FAILED: Ticket selection step never appeared – no member row for Gary
```

`find_event_href` was returning the bare listing URL. Its container fallback scanned
`td, li, div` for anything mentioning the event and the date — but `querySelectorAll`
returns outermost-first, so the first "match" was a whole-page wrapper and the anchor
it yielded was the top-nav **EVENTS** link.

Restricting that fallback to `/events/<id>` links made it *worse*: Thursday 7/30 then
resolved to `/events/1849591`, **Tuesday's event**. A plausible URL that books the
wrong day is not something a URL check can catch. The fallback is now **deleted** — a
match must come from a single anchor naming both the event and the date.

`open_event_page` also validates it landed on `/events/<id>`, and the "any Register
button" fallback only fires on a real event page. Both layers are deliberate: one
stops a wrong URL being produced, the other stops a wrong URL being used.

**This was not a weekday-only bug.** Weekend runs on 07-24 and 07-25 started at
07:59:59, so `seconds_until_open()` was ≤ 0 and staging was skipped entirely. With
cron at 07:58, Friday 07-31 would have been the first weekend run to execute the
staging path — and it would have failed the same way.

### ✅ Also fixed

- Fallback slot selection can no longer go **earlier** than the first preference.
  Weekday grids start at 6:15, so unbounded "earliest open" would answer a gone 8:00
  with a 6:15 lane. (Weekend behaviour change: if the only open lane is before 8:00,
  the script now books nothing rather than an early Saturday lane.)
- The "could not isolate" warning names the actual slot instead of hardcoding `8 AM`.

---

## The one step left: enable weekday booking

**a. `book_swim.py`** — move the weekday plan into `SCHEDULE`:

```python
SCHEDULE: dict[int, DaySchedule] = {
    1: DaySchedule(["6:15 AM"], any_open=False),             # Tuesday
    3: DaySchedule(["6:15 AM"], any_open=False),             # Thursday
    5: DaySchedule(["8:00 AM", "8:45 AM"], any_open=True, latest=time_of_day(9, 30)),
    6: DaySchedule(["8:00 AM", "8:45 AM"], any_open=True, latest=time_of_day(9, 30)),
}
```

`WEEKDAY_SCHEDULE` and the `weekday_recon` argument to `schedule_for()` can then go —
they exist only so `--dry-run` can inspect a day that is not yet booked. Keep them if
you would rather retain that ability.

**b. crontab**

```bash
crontab -e
# from:  58 7 * * 5,6     /home/gary/projects/swim-booker/run.sh
# to:    58 7 * * 1,3,5,6 /home/gary/projects/swim-booker/run.sh
```

Cron day numbers and `SCHEDULE` keys are the same digits (1, 3, 5, 6) — systematic,
since cron numbering is Python's plus one and the day booked is always the next day.

### Then verify

```bash
crontab -l                              # 58 7 * * 1,3,5,6
python3 -m py_compile book_swim.py
```

### First real weekday booking: **Wednesday, 07:58 → books Thursday**

(Tuesday has no run — Wednesday is not a swim day.) Watch `swim_booker.log` for the
hold, the grid, then either a 6:15 booking or `6:15 AM unavailable … not booking a
fallback`. **That second message is the design working**, not a bug.

---

## Decisions already made — do not re-litigate

- **Weekday 6:15 has no fallback.** If it is gone, book nothing and notify. 6:15 is
  the only slot that still gets Gary to work on time; a "booked" email for a midday
  lane is worse than a failure notice.
- **Weekend fallback capped at 9:30 AM**, inclusive — `9:30am - 10:10am` books,
  `10:15am` does not. Bounds only the fallback; an explicit 8:00/8:45 preference is
  never subject to it.
- **`OPEN_TIME` is 08:00 for every day** and means *when registration opens*, not a
  swim time. Confirmed from the site's own text: *"Reservations may be made at 8:00am
  the day prior."* Never "fix" it to a swim time.
- **`EVENT_NAME` is singular on purpose.** Do not "correct" it to the plural.
- **The event matcher does not guess.** If it stops finding events, fail loudly and
  find out why. Do not reinstate a nearest-anchor fallback — it books the wrong day.
