#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

source "$SCRIPT_DIR/.venv/bin/activate"

# Started at 7:58 so login and event lookup (~23s) finish before slots open.
# book_swim.py then holds until 08:00:00 itself, so no sleep is needed here -
# it needs to be running early, not started late.
python3 book_swim.py
