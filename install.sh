#!/usr/bin/env bash
# Set up linux-computer-use and register it with Claude Code and Codex.
# Idempotent: safe to re-run after `git pull`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="$(command -v uv || true)"
[ -n "$UV" ] || { echo "uv not found: https://docs.astral.sh/uv/" >&2; exit 1; }

command -v xinput >/dev/null || echo "!! xinput not found: virtual mouse disabled (sudo apt install xinput)"

echo "==> venv (system site-packages for PyGObject/AT-SPI)"
if [ ! -d "$ROOT/.venv" ]; then
  "$UV" venv --directory "$ROOT" --python /usr/bin/python3 --system-site-packages -q
fi
"$UV" sync --directory "$ROOT" -q

link_skill() {  # $1 = skills dir
  mkdir -p "$1"
  ln -sfn "$ROOT/skills/linux-computer-use" "$1/linux-computer-use"
  echo "    skill -> $1/linux-computer-use"
}

if command -v claude >/dev/null; then
  echo "==> Claude Code"
  if claude mcp get linux-cu 2>/dev/null | grep -q "run lcu-supervised"; then
    echo "    MCP server linux-cu already registered"
  else
    # (re)register through the supervisor so a crashed server comes back
    claude mcp remove -s user linux-cu >/dev/null 2>&1 || true
    claude mcp add -s user linux-cu -- "$UV" --directory "$ROOT" run lcu-supervised
  fi
  link_skill "$HOME/.claude/skills"
fi

if command -v codex >/dev/null; then
  echo "==> Codex"
  CFG="$HOME/.codex/config.toml"
  mkdir -p "$(dirname "$CFG")"; touch "$CFG"
  if grep -q '^\[mcp_servers\.linux-cu\]' "$CFG"; then
    if grep -q '"run", "lcu"\]' "$CFG"; then
      cp "$CFG" "$CFG.bak-lcu"
      sed -i 's/"run", "lcu"\]/"run", "lcu-supervised"]/' "$CFG"
      echo "    switched linux-cu to the supervisor (backup: $CFG.bak-lcu)"
    else
      echo "    MCP server linux-cu already in $CFG"
    fi
  else
    cp "$CFG" "$CFG.bak-lcu"
    cat >> "$CFG" <<EOF

[mcp_servers.linux-cu]
command = "$UV"
args = ["--directory", "$ROOT", "run", "lcu-supervised"]
startup_timeout_sec = 60
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
EOF
    echo "    added to $CFG (backup: $CFG.bak-lcu)"
  fi
  link_skill "$HOME/.codex/skills"
fi

echo "==> done. Start a new Claude Code / Codex session to pick it up."
