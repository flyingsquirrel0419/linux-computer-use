#!/usr/bin/env bash
# Run the test suite against a throwaway X server: Xvfb + a window manager
# (creating an MPX master pointer needs one). Run it under dbus-run-session.
# Usage: scripts/ci_x11.sh [pytest args...]
set -euo pipefail
cd "$(dirname "$0")/.."

DISP="${LCU_TEST_DISPLAY:-:99}"
WM=""
for wm in openbox muffin; do command -v "$wm" >/dev/null && { WM="$wm"; break; }; done
[ -n "$WM" ] || { echo "need a window manager (openbox or muffin)" >&2; exit 1; }

Xvfb "$DISP" -screen 0 1280x720x24 -nolisten tcp >/dev/null 2>&1 & XVFB=$!
trap 'kill $WMPID $XVFB 2>/dev/null || true' EXIT
for _ in $(seq 50); do DISPLAY="$DISP" xinput list >/dev/null 2>&1 && break; sleep 0.1; done
DISPLAY="$DISP" "$WM" >/dev/null 2>&1 & WMPID=$!
sleep 2

LCU_TEST_DISPLAY="$DISP" uv run --no-sync python -m pytest -q "$@"
