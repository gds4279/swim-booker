#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

sleep 58
source "$SCRIPT_DIR/.venv/bin/activate"
python3 book_swim.py
