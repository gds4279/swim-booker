#!/usr/bin/env python3
"""PreToolUse guard for swim-booker.

Blocks the two things Claude can do here that cannot be taken back:

1. A live run. `book_swim.py` without a safe flag books a real lane and emails
   Gary plus a CC address; `run.sh` is exactly that. Importing the module and
   calling main()/book_once()/send_email()/send_slack() does the same.
2. Reading a credential into the transcript: `.env` (password, SMTP/Slack
   secrets) and `session.json` / `session.tmp` (a logged-in session).

It is a guard against accidents, not an adversary - it pattern-matches the
command text. Not a substitute for the user's own judgement about a live run:
when one is wanted, the user runs it themselves (`! ./run.sh`).
"""
import json
import os
import re
import sys

SAFE_FLAGS = ("--dry-run", "--check-session", "--import-cookies")
SECRET_NAMES = {".env", "session.json", "session.tmp"}
# `.env` but not `.env.template` / `.envrc`; `.venv` never matches (no "." before "env").
SECRET_RE = re.compile(r"(?<![\w.])(?:\.env|session\.json|session\.tmp)(?![\w.])")
LIVE_CALL_RE = re.compile(r"\b(?:main|send_email|send_slack|report_success)\s*\(|\bbook_once\s*\((?![^)]*dry_run\s*=\s*True)")
SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n&]")
# book_swim.py being *executed* (python [opts] .../book_swim.py, or ./book_swim.py),
# as opposed to merely named by py_compile, git, grep, etc.
EXEC_RE = re.compile(r"(?:^|[\s/])python[\d.]*\s+(?:-\S+\s+)*\S*book_swim\.py"
                     r"|(?:^|\s)(?:\./|/\S*/)book_swim\.py")


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def check_bash(cmd: str) -> None:
    if SECRET_RE.search(cmd):
        deny("Blocked: this command touches a credential file (.env / session.json). "
             "Their contents must not enter the transcript. Use `book_swim.py --check-session` "
             "to test the session, or ask the user to inspect the file themselves.")
    # Whole command, not per segment: `python3 -c "import book_swim as b; b.main()"`
    # puts the import and the call on opposite sides of a `;`.
    if "book_swim" in cmd and LIVE_CALL_RE.search(cmd):
        deny("Blocked: calling main()/book_once()/send_email()/send_slack()/report_success() "
             "from book_swim books a lane or sends real notifications. Test pure functions only, "
             "or stub smtplib/urllib first in a test file.")
    for seg in SEGMENT_SPLIT.split(cmd):
        if re.search(r"(?:^|[\s/])run\.sh\b", seg):
            deny("Blocked: run.sh is a LIVE booking run (books a lane, sends email/Slack). "
                 "Use `python3 book_swim.py --dry-run`, or ask the user to run it with `! ./run.sh`.")
        if EXEC_RE.search(seg) and not any(f in seg for f in SAFE_FLAGS):
            deny("Blocked: book_swim.py without --dry-run / --check-session / --import-cookies "
                 "is a LIVE booking run (books a lane, sends email/Slack). Add --dry-run, or ask "
                 "the user to run it themselves.")


def check_path(path: str) -> None:
    if path and os.path.basename(path.rstrip("/")) in SECRET_NAMES:
        deny(f"Blocked: {os.path.basename(path)} holds credentials and must not be read or "
             f"written by Claude. Use `book_swim.py --check-session` / `--import-cookies` instead.")


def main() -> None:
    data = json.load(sys.stdin)
    tool, inp = data.get("tool_name", ""), data.get("tool_input") or {}
    if tool == "Bash":
        check_bash(inp.get("command", ""))
    else:
        check_path(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")


if __name__ == "__main__":
    main()
