"""Fill in desktop-session env vars that MCP hosts often strip.

The MCP stdio client spawns servers with a minimal environment (Codex and the
reference SDK pass only HOME/PATH/USER...), so DISPLAY, XAUTHORITY and the
session D-Bus address (needed by AT-SPI and ibus) may be missing.
"""

import glob
import os


def ensure() -> None:
    uid = os.getuid()
    if not os.environ.get("DISPLAY"):
        socks = sorted(glob.glob("/tmp/.X11-unix/X*"))
        os.environ["DISPLAY"] = ":" + socks[0].rsplit("X", 1)[1] if socks else ":0"
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
