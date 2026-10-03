"""X11 connection and the screenshot <-> real-screen coordinate mapping.

Every coordinate the agent sends or receives is in *screenshot space*: the
(possibly downscaled) image returned by the screenshot tool. Conversion to real
screen pixels happens only here, so the model never has to think about it.
"""

import os
import threading

from Xlib import display as xdisplay

# Long edge of the screenshot sent to the model. Claude's vision works best at
# or below ~1.15 MP / 1568 px; 1280 keeps text legible and tokens moderate.
MAX_LONG_EDGE = int(os.environ.get("LCU_MAX_LONG_EDGE", "1280"))

_lock = threading.RLock()
_display = None
_core = None


def get_display():
    global _display
    with _lock:
        if _display is None:
            _display = xdisplay.Display()  # honours $DISPLAY
        return _display


def lock():
    """Serialize all X11 traffic; python-xlib is not thread-safe."""
    return _lock


def real_size() -> tuple[int, int]:
    root = get_display().screen().root
    geom = root.get_geometry()
    return geom.width, geom.height


def scale() -> float:
    """Factor from real pixels to screenshot pixels (<= 1)."""
    w, h = real_size()
    return min(1.0, MAX_LONG_EDGE / max(w, h))


def shot_size() -> tuple[int, int]:
    w, h = real_size()
    s = scale()
    return round(w * s), round(h * s)


def to_real(x: float, y: float) -> tuple[int, int]:
    s = scale()
    w, h = real_size()
    rx = min(max(round(x / s), 0), w - 1)
    ry = min(max(round(y / s), 0), h - 1)
    return rx, ry


def to_shot(x: float, y: float) -> tuple[int, int]:
    s = scale()
    return round(x * s), round(y * s)



def get_core_display():
    """A second connection that stays bound to the user's core pointer/keyboard.

    Keymap changes must go through it: toolkits translate keycodes with the
    core keyboard's map, even for events from the agent's own keyboard.
    """
    global _core
    with _lock:
        if _core is None:
            _core = xdisplay.Display()
        return _core


def _describe(d, win) -> dict:
    title = ""
    try:
        name = win.get_full_property(d.intern_atom("_NET_WM_NAME"), d.intern_atom("UTF8_STRING"))
        title = name.value.decode("utf-8", "replace") if name else (win.get_wm_name() or "")
    except Exception:
        pass
    cls = ""
    try:
        wc = win.get_wm_class()
        cls = wc[1] if wc else ""
    except Exception:
        pass
    pid = None
    try:
        p = win.get_full_property(d.intern_atom("_NET_WM_PID"), 0)
        pid = int(p.value[0]) if p else None
    except Exception:
        pass
    return {"title": title, "class": cls, "pid": pid}


def _client_of(d, win, depth: int = 3):
    """Find the application window (has WM_STATE) inside a WM frame."""
    wm_state = d.intern_atom("WM_STATE")
    if win.get_full_property(wm_state, 0) is not None:
        return win
    if depth == 0:
        return None
    for child in reversed(win.query_tree().children):
        found = _client_of(d, child, depth - 1)
        if found is not None:
            return found
    return None


def user_focus_window() -> dict:
    """The window with the user's keyboard focus (EWMH _NET_ACTIVE_WINDOW)."""
    with _lock:
        d = get_display()
        root = d.screen().root
        prop = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), 0)
        if not prop or not prop.value or not prop.value[0]:
            return {"title": "", "class": "", "pid": None}
        return _describe(d, d.create_resource_object("window", prop.value[0]))


def window_under_pointer() -> dict:
    """The top-level window under *this connection's* pointer (the agent's)."""
    with _lock:
        d = get_display()
        child = d.screen().root.query_pointer().child
        if not child:
            return {"title": "", "class": "", "pid": None}
        try:
            client = _client_of(d, child)
        except Exception:
            client = None
        return _describe(d, client or child)
