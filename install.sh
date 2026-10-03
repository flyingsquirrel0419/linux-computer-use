#!/usr/bin/env bash
# Set up linux-computer-use and register it with Claude Code and Codex.
# Idempotent: safe to re-run after `git pull`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="$(command -v uv || true)"
[ -n "$UV" ] || { echo "uv not found: https://docs.astral.sh/uv/" >&2; exit 1; }

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
  if claude mcp get linux-cu >/dev/null 2>&1; then
    echo "    MCP server linux-cu already registered"
  else
    claude mcp add -s user linux-cu -- "$UV" --directory "$ROOT" run lcu
  fi
  link_skill "$HOME/.claude/skills"
fi

if command -v codex >/dev/null; then
  echo "==> Codex"
  CFG="$HOME/.codex/config.toml"
  mkdir -p "$(dirname "$CFG")"; touch "$CFG"
  if grep -q '^\[mcp_servers\.linux-cu\]' "$CFG"; then
    echo "    MCP server linux-cu already in $CFG"
  else
    cp "$CFG" "$CFG.bak-lcu"
    cat >> "$CFG" <<EOF

[mcp_servers.linux-cu]
command = "$UV"
args = ["--directory", "$ROOT", "run", "lcu"]
startup_timeout_sec = 60
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
EOF
    echo "    added to $CFG (backup: $CFG.bak-lcu)"
  fi
  link_skill "$HOME/.codex/skills"
fi

echo "==> done. Start a new Claude Code / Codex session to pick it up."
