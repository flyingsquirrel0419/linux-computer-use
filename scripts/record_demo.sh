#!/usr/bin/env bash
# Record docs/demo.gif on a throwaway display (never your desktop).
# Needs Xvfb, muffin (compositing, for the translucent agent cursor), gedit
# and ffmpeg. Usage: dbus-run-session -- scripts/record_demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export DISPLAY="${LCU_DEMO_DISPLAY:-:98}"
export LANG=en_US.UTF-8 LANGUAGE=en LC_ALL=en_US.UTF-8   # English UI in the recording
DEMO=/tmp/lcu-demo
rm -rf "$DEMO" && mkdir -p "$DEMO" && : > "$DEMO/agent.txt" && : > "$DEMO/you.txt"

Xvfb "$DISPLAY" -screen 0 1280x720x24 -nolisten tcp >/dev/null 2>&1 & XV=$!
PIDS="$XV"
trap 'kill $PIDS 2>/dev/null || true; rm -rf "$DEMO"' EXIT
for _ in $(seq 50); do xinput list >/dev/null 2>&1 && break; sleep 0.1; done
muffin --replace >/dev/null 2>&1 & PIDS="$! $PIDS"
sleep 2
uv run --no-sync python scripts/demo_scenario.py --background >/dev/null 2>&1 & PIDS="$! $PIDS"
sleep 1
gedit --new-window "$DEMO/agent.txt" >/dev/null 2>&1 & PIDS="$! $PIDS"
sleep 2
gedit --new-window "$DEMO/you.txt" >/dev/null 2>&1 & PIDS="$! $PIDS"
sleep 3

uv run --no-sync python scripts/demo_scenario.py
