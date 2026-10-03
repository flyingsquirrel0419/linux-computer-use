"""The agent's own pointer and keyboard (X Input 2 multi-pointer, "MPX").

We add a second master pointer/keyboard pair named "lcu-<pid>" and make it
this process's ClientPointer. From then on, every XTEST event we send goes
through that pair instead of the user's:

- the user's mouse never moves, and their keyboard focus never changes;
- agent clicks land on whatever is under the *agent* pointer, without
  raising or activating the window (verified with muffin/Cinnamon + GTK3);
- agent keystrokes go to the window under the agent pointer (the new master
  keyboard's focus is PointerRoot).

Several agents (say Claude Code and Codex at once) each get their own pair.
The pair is removed on exit; pairs left behind by dead processes are cleaned
up on the next start. Set LCU_VIRTUAL_POINTER=0 to drive the user's real
pointer instead.
"""

import atexit
import os
import re
import shutil
import signal
import subprocess
import sys

from Xlib import X

from . import display

ENABLED = os.environ.get("LCU_VIRTUAL_POINTER", "1") != "0"
NAME = f"lcu-{os.getpid()}"

_state = {"active": False, "error": None}


def _xinput(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["xinput", *args], capture_output=True, text=True, timeout=5,
                          env={**os.environ})


def _masters() -> dict[str, int]:
    """name -> id of master pointers."""
    out = _xinput("list", "--short").stdout
    found = {}
    for line in out.splitlines():
        if "[master pointer" not in line or "id=" not in line:
            continue
        name, rest = line.split("id=", 1)
        found[name.strip(" \t⎡⎜⎣↳")] = int(rest.split()[0])
    return found


def _remove(name: str) -> None:
    _xinput("remove-master", f"{name} pointer")


def _reap_stale() -> None:
    for name in _masters():
        m = re.fullmatch(r"lcu-(\d+) pointer", name)
        if m and not os.path.exists(f"/proc/{m.group(1)}"):
            _remove(f"lcu-{m.group(1)}")


def _cleanup() -> None:
    if _state["active"]:
        _state["active"] = False
        _remove(NAME)


def _on_signal(signum, _frame):
    _cleanup()
    sys.exit(128 + signum)


def enable() -> bool:
    """Create the agent pointer and bind this process to it. Idempotent."""
    if _state["active"]:
        return True
    if not ENABLED:
        return False
    if not shutil.which("xinput"):
        _state["error"] = "xinput not installed (apt install xinput)"
        return False
    try:
        _reap_stale()
        if f"{NAME} pointer" not in _masters():
            r = _xinput("create-master", NAME)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "create-master failed")
        # ClientPointer is set per X client; `xinput set-cp` finds the client
        # through a window it owns, so give our connection a tiny window.
        with display.lock():
            d = display.get_display()
            win = d.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
            d.sync()
        r = _xinput("set-cp", str(win.id), f"{NAME} pointer")
        if r.returncode != 0:
            _remove(NAME)
            raise RuntimeError(r.stderr.strip() or "set-cp failed")
    except Exception as e:  # fall back to the shared pointer
        _state["error"] = str(e)
        return False
    _state["active"] = True
    atexit.register(_cleanup)
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, _on_signal)
    return True


def active() -> bool:
    return _state["active"]


def status() -> dict:
    return {"virtual_pointer": _state["active"], "name": NAME if _state["active"] else None,
            "error": _state["error"]}
