#!/usr/bin/env python3
"""Print one scheduled run out of swim_booker.log, plus a recent-history line.

    run_log.py              # the most recent scheduled run
    run_log.py 2026-10-14   # the scheduled run on that date

A scheduled run starts at an "Attempt 1/N" entry and ends at its Slack line (or
the next run / end of file). Dry runs, --check-session and --import-cookies are
not scheduled runs and are skipped. Read-only.
"""
import re
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parents[4] / "swim_booker.log"
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2},\d{3}\s+\w+\s+(.*)$")
START = re.compile(r"^Attempt 1/\d+$")


def entries(lines):
    """Group lines into (date, message, text) - tracebacks stay with their entry."""
    out = []
    for line in lines:
        m = STAMP.match(line)
        if m:
            out.append([m.group(1), m.group(2), line])
        elif out:
            out[-1][2] += "\n" + line
    return out


def runs(es):
    found, cur = [], None
    for date, msg, text in es:
        if START.match(msg):
            cur = {"date": date, "lines": []}
            found.append(cur)
        if cur is None:
            continue
        if date != cur["date"]:          # a later day's manual commands are not this run
            cur = None
            continue
        cur["lines"].append((msg, text))
        if msg.startswith("Slack notification") or msg.startswith("Nothing scheduled"):
            cur = None
    return found


def outcome(run):
    msgs = [m for m, _ in run["lines"]]
    if any(m.startswith("Giving up") for m in msgs):
        return "FAILED"
    if any(m.startswith("SUCCESS") or m.startswith("Already registered") for m in msgs):
        return "booked"
    return "unfinished?"


def main():
    if not LOG.exists():
        sys.exit(f"no log at {LOG}")
    all_runs = runs(entries(LOG.read_text(errors="replace").splitlines()))
    if not all_runs:
        sys.exit("no scheduled runs found in the log")
    want = sys.argv[1] if len(sys.argv) > 1 else None
    picked = [r for r in all_runs if r["date"] == want] if want else all_runs[-1:]
    if not picked:
        sys.exit(f"no scheduled run on {want}; runs exist on: "
                 + ", ".join(r["date"] for r in all_runs[-10:]))
    run = picked[-1]
    attempts = sum(1 for m, _ in run["lines"] if re.match(r"Attempt \d+/\d+$", m))
    print(f"== Run of {run['date']}: {outcome(run)} after {attempts} attempt(s) ==")
    for _, text in run["lines"]:
        print(text if len(text) < 600 else text[:600] + " …[truncated]")
    print("\n== Recent runs (oldest → newest) ==")
    print("  ".join(f"{r['date']}:{outcome(r)}" for r in all_runs[-10:]))


if __name__ == "__main__":
    main()
