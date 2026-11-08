#!/usr/bin/env bash
# Print labels on a Nelko P21 over Bluetooth LE. Creates .venv on first run.
#
#   ./print.sh --status
#   ./print.sh --labels-dir examples/breaker-panel/labels L1
#   ./print.sh --labels-dir examples/breaker-panel/labels --all
#   ./print.sh my_label.png
#
# macOS: run from Terminal.app (or another terminal you've allowed Bluetooth access).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: creating .venv and installing requirements..."
  python3 -m venv .venv
  .venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
fi
exec .venv/bin/python p21_ble.py "$@"
