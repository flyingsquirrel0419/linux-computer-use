#!/usr/bin/env bash
# Record docs/demo.gif on a throwaway display (never your desktop).
# Needs Xvfb, muffin (compositing, for the translucent agent cursor), nemo,
# gedit, gnome-terminal, git and ffmpeg. Usage: scripts/record_demo.sh
#
# Apps run with a fake HOME (/tmp/lcu-demo/home), an English locale, a demo
# prompt and a demo git identity, so nothing personal ends up in the GIF.
# Those variables are set before the D-Bus session starts because
# D-Bus-activated apps (gnome-terminal-server) inherit the daemon's env.
set -euo pipefail
cd "$(dirname "$0")/.."

DEMO=/tmp/lcu-demo
if [ "${1:-}" != "--inside" ]; then
  export DISPLAY="${LCU_DEMO_DISPLAY:-:98}"
  export LANG=en_US.UTF-8 LANGUAGE=en LC_ALL=en_US.UTF-8
  export UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}" HOME="$DEMO/home"
  export GIT_PAGER=cat PAGER=cat
  rm -rf "$DEMO" && mkdir -p "$HOME/project" "$HOME/.config"
  printf '%s\n' "PROMPT='%F{green}%Bdemo%b%f:%F{blue}%B%~%b%f\$ '" > "$HOME/.zshrc"
  printf '%s\n' "PS1='\[\e[1;32m\]demo\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]\\\$ '" > "$HOME/.bashrc"
  printf '[user]\n\tname = Demo User\n\temail = demo@example.com\n[init]\n\tdefaultBranch = main\n[core]\n\tpager = cat\n' > "$HOME/.gitconfig"
  printf '[Default Applications]\ntext/markdown=org.gnome.gedit.desktop\ntext/plain=org.gnome.gedit.desktop\n' > "$HOME/.config/mimeapps.list"
  (cd "$HOME/project" && printf '# project\n\nA small demo repository.\n' > README.md \
     && git init -q && git add -A && git commit -qm "Initial commit")
  status=0
  dbus-run-session -- "$0" --inside || status=$?
  rm -rf "$DEMO"
  exit $status
fi

Xvfb "$DISPLAY" -screen 0 1280x720x24 -nolisten tcp >/dev/null 2>&1 & PIDS="$!"
# apps started via D-Bus (gnome-terminal-server, nemo, gedit) exit with the X server
trap 'kill $PIDS 2>/dev/null || true' EXIT
for _ in $(seq 50); do xinput list >/dev/null 2>&1 && break; sleep 0.1; done
muffin --replace >/dev/null 2>&1 & PIDS="$! $PIDS"
sleep 2
uv run --no-sync python scripts/demo_scenario.py --background >/dev/null 2>&1 & PIDS="$! $PIDS"
sleep 1
(cd "$HOME/project" && gnome-terminal >/dev/null 2>&1)
sleep 3

uv run --no-sync python scripts/demo_scenario.py
