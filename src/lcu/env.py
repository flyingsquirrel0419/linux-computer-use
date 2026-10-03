"""Fill in desktop-session env vars that MCP hosts often strip.

The MCP stdio client spawns servers with a minimal environment (Codex and the
reference SDK pass only HOME/PATH/USER...), so DISPLAY, XAUTHORITY and the
session D-Bus address (needed by AT-SPI and ibus) may be missing.
"""

import glob
import os
from collections import Counter


def _session_displays(uid: int) -> Counter:
    """DISPLAY values used by this user's running processes."""
    seen: Counter = Counter()
    for env_path in glob.glob("/proc/[0-9]*/environ"):
        try:
            if os.stat(env_path).st_uid != uid:
                continue
            with open(env_path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        for item in data.split(b"\0"):
            if item.startswith(b"DISPLAY="):
                seen[item[8:].decode(errors="replace")] += 1
                break
    return seen


def _guess_display(uid: int) -> str:
    """The display of the user's desktop session, not just the first socket:
    with several X servers (a test Xvfb, say) socket order is arbitrary."""
    socks = {":" + p.rsplit("X", 1)[1] for p in glob.glob("/tmp/.X11-unix/X*")}
    for disp, _ in _session_displays(uid).most_common():
        base = disp.split(".")[0]
        if base in socks:
            return disp
    if socks:
        return min(socks, key=lambda d: int(d[1:]) if d[1:].isdigit() else 1 << 30)
    return ":0"


def ensure() -> None:
    uid = os.getuid()
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = _guess_display(uid)
    if not os.environ.get("XAUTHORITY"):
        for cand in (os.path.expanduser("~/.Xauthority"), f"/run/user/{uid}/gdm/Xauthority"):
            if os.path.exists(cand):
                os.environ["XAUTHORITY"] = cand
                break
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = f"/run/user/{uid}/bus"
        if os.path.exists(bus):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
