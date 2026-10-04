#!/usr/bin/env bash
# Set up linux-computer-use and register it with Claude Code and Codex.
# Idempotent: safe to re-run after `git pull`.
#
# Options:
#   --auto-approve      let Codex call the linux-cu tools without asking each time
#                       (needed for non-interactive `codex exec`)
#   --no-auto-approve   remove that setting again
# Without either option a new Codex registration asks per call, and an existing
# one keeps whatever approval setting it has.
set -euo pipefail

APPROVE=""
for arg in "$@"; do
  case "$arg" in
    --auto-approve) APPROVE=on ;;
    --no-auto-approve) APPROVE=off ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

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

# set (on), remove (off) or report (empty) default_tools_approval_mode in the
# [mcp_servers.linux-cu] table of the Codex config; prints the resulting state
codex_approval() {  # $1 = config path, $2 = on|off|""
  /usr/bin/python3 - "$1" "$2" <<'PY'
import re, sys
path, want = sys.argv[1], sys.argv[2]
lines = open(path).read().split("\n")
start = next(i for i, l in enumerate(lines) if l.strip() == "[mcp_servers.linux-cu]")
end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
key = re.compile(r"\s*default_tools_approval_mode\s*=")
section = lines[start + 1:end]
if want in ("on", "off"):
    section = [l for l in section if not key.match(l)]
    if want == "on":
        last = max((i for i, l in enumerate(section) if l.strip()), default=-1)
        section.insert(last + 1, 'default_tools_approval_mode = "approve"')
    open(path, "w").write("\n".join(lines[:start + 1] + section + lines[end:]))
print("on" if any(key.match(l) and '"approve"' in l for l in section) else "off")
PY
}

codex_upgrade_server() {  # $1 = config path, $2 = checkout; print changed|unchanged
  /usr/bin/python3 - "$1" "$2" <<'PY'
import ast, json, os, re, shutil, sys
path, root = sys.argv[1:]
lines = open(path).read().splitlines(keepends=True)
start = next(i for i, line in enumerate(lines) if line.strip() == "[mcp_servers.linux-cu]")
end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
old = re.compile(r'^(\s*args\s*=\s*)(\[.*\])(\s*(?:#.*)?\n?)$')
changed = False
owned = False
for i in range(start + 1, end):
    match = old.match(lines[i])
    if not match:
        continue
    try:
        args = ast.literal_eval(match.group(2))
    except (SyntaxError, ValueError):
        continue
    if (isinstance(args, list) and len(args) >= 4 and args[:1] == ["--directory"]
            and isinstance(args[1], str) and os.path.realpath(args[1]) == root):
        owned = True
    if owned and args[-2:] == ["run", "lcu"]:
        args[-1] = "lcu-supervised"
        lines[i] = match.group(1) + json.dumps(args) + match.group(3)
        changed = True
if changed:
    shutil.copy2(path, path + ".bak-lcu")
    open(path, "w").writelines(lines)
print("changed" if changed else "unchanged" if owned else "foreign")
PY
}

if command -v codex >/dev/null; then
  echo "==> Codex"
  CFG="$HOME/.codex/config.toml"
  mkdir -p "$(dirname "$CFG")"; touch "$CFG"
  if grep -q '^\[mcp_servers\.linux-cu\]' "$CFG"; then
    upgrade=$(codex_upgrade_server "$CFG" "$ROOT")
    if [ "$upgrade" = foreign ]; then
      echo "    linux-cu points to another checkout; leaving it unchanged"
    elif [ "$upgrade" = changed ]; then
      echo "    switched linux-cu to the supervisor (backup: $CFG.bak-lcu)"
    else
      echo "    MCP server linux-cu already in $CFG"
    fi
    if [ "$upgrade" = foreign ]; then
      :
    elif [ -n "$APPROVE" ]; then
      cp "$CFG" "$CFG.bak-lcu"
      state=$(codex_approval "$CFG" "$APPROVE")
      echo "    auto-approve: $state (backup: $CFG.bak-lcu)"
    else
      state=$(codex_approval "$CFG" "")
      echo "    auto-approve: $state (change with --auto-approve / --no-auto-approve)"
    fi
  else
    cp "$CFG" "$CFG.bak-lcu"
    cat >> "$CFG" <<EOF

[mcp_servers.linux-cu]
command = "$UV"
args = ["--directory", "$ROOT", "run", "lcu-supervised"]
startup_timeout_sec = 60
tool_timeout_sec = 120
EOF
    [ "$APPROVE" = on ] && codex_approval "$CFG" on >/dev/null
    echo "    added to $CFG (backup: $CFG.bak-lcu); auto-approve: ${APPROVE:-off}"
  fi
  link_skill "$HOME/.codex/skills"
fi

echo "==> done. Start a new Claude Code / Codex session to pick it up."
