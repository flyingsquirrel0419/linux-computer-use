"""Server-side handle on the overlay process (see overlay.py)."""

import json
import os
import subprocess
import sys
import threading

ENABLED = os.environ.get("LCU_OVERLAY", "1") != "0"

_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def label_for(client_name: str | None) -> str:
    """Name tag next to the cursor. Off by default, like Codex; set
    LCU_CURSOR_LABEL to a text, or to "auto" for the client's name."""
    want = os.environ.get("LCU_CURSOR_LABEL", "")
    if want != "auto":
        return want
    n = (client_name or "").lower()
    if "claude" in n:
        return "Claude"
    if "codex" in n:
        return "Codex"
    return (client_name or "Agent").split("-")[0].title()


def start(label: str) -> bool:
    global _proc
    if not ENABLED:
        return False
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return True
        try:
            _proc = subprocess.Popen(
                [sys.executable, "-m", "lcu.overlay", "--label", label],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, "NO_AT_BRIDGE": "1"},  # keep it out of ui_tree
            )
        except Exception:
            _proc = None
            return False
    return True


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def _send(msg: dict) -> None:
    if not running():
        return
    try:
        _proc.stdin.write((json.dumps(msg) + "\n").encode())
        _proc.stdin.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass


def pos(x: int, y: int, motion: dict | None = None) -> None:
    _send({"pos": [x, y], **(motion or {})})


def button(name: str, down: bool) -> None:
    _send({"btn": name, "down": down})


def stop() -> None:
    if running():
        _send({"quit": True})
        try:
            _proc.wait(timeout=1)
        except Exception:
            _proc.kill()
