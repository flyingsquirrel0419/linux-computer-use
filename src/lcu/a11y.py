"""Accessibility tree via AT-SPI.

Gives the agent a list of on-screen widgets with names, roles and boxes, so it
can target "the Save button" instead of guessing pixels. Element ids are only
valid until the next ui_tree() call.
"""

import subprocess
import sys
import time

try:
    import gi
except ImportError:  # venv without system site-packages: borrow the distro's PyGObject
    sys.path.append("/usr/lib/python3/dist-packages")
    import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

from . import display  # noqa: E402

Atspi.init()
try:
    Atspi.set_timeout(1500, 15000)  # ms per call; a hung app must not hang us
except Exception:
    pass

S = Atspi.StateType

INTERACTIVE_ROLES = {
    "push button", "toggle button", "check box", "radio button", "menu item",
    "check menu item", "radio menu item", "menu", "entry", "password text",
    "text", "combo box", "link", "list item", "page tab", "spin button",
    "slider", "tree item", "table cell", "icon", "button", "scroll bar",
    "tool bar item", "split button", "terminal", "document web", "editbar",
}

MAX_NODES = 4000

_cache: dict[int, "Atspi.Accessible"] = {}


def accessibility_enabled() -> bool | None:
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "toolkit-accessibility"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        return out == "true"
    except Exception:
        return None


def _apps():
    desk = Atspi.get_desktop(0)
    for i in range(desk.get_child_count()):
        try:
            app = desk.get_child_at_index(i)
        except Exception:
            continue
        if app is not None:
            yield app


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _extents(acc):
    comp = _safe(acc.get_component_iface)
    if comp is None:
        return None
    r = _safe(lambda: comp.get_extents(Atspi.CoordType.SCREEN))
    if r is None or r.width <= 0 or r.height <= 0:
        return None
    return r.x, r.y, r.width, r.height


def _on_screen(box) -> bool:
    if box is None:
        return False
    w, h = display.real_size()
    x, y, bw, bh = box
    return x + bw > 0 and y + bh > 0 and x < w and y < h


def list_windows() -> list[dict]:
    out = []
    for app in _apps():
        app_name = _safe(app.get_name, "") or "?"
        for j in range(_safe(app.get_child_count, 0)):
            win = _safe(lambda: app.get_child_at_index(j))
            if win is None:
                continue
            states = _safe(win.get_state_set)
            box = _extents(win)
            out.append({
                "app": app_name,
                "title": _safe(win.get_name, "") or "",
                "role": _safe(win.get_role_name, "") or "",
                "active": bool(states and states.contains(S.ACTIVE)),
                "visible": bool(states and states.contains(S.SHOWING)),
                "box": [*display.to_shot(box[0], box[1]), *display.to_shot(box[2], box[3])] if box else None,
            })
    return out


def tree(app: str | None = None, window: str | None = None, max_depth: int = 40,
         only_interactive: bool = True, active_window_only: bool = False) -> str:
    """Compact text dump of visible widgets, coordinates in screenshot space."""
    _cache.clear()
    lines: list[str] = []
    counter = [0]
    deadline = time.monotonic() + 20

    def walk(acc, depth, indent):
        if counter[0] >= MAX_NODES or time.monotonic() > deadline:
            return
        states = _safe(acc.get_state_set)
        if states is not None and not states.contains(S.SHOWING):
            return
        role = _safe(acc.get_role_name, "") or ""
        name = (_safe(acc.get_name, "") or "").strip().replace("\n", " ")
        box = _extents(acc)
        visible = _on_screen(box)
        interesting = role in INTERACTIVE_ROLES or _safe(acc.get_action_iface) is not None
        if not only_interactive:
            interesting = interesting or bool(name) or role in ("heading", "label", "static", "paragraph")

        child_indent = indent
        if visible and interesting and depth > 0:
            counter[0] += 1
            eid = counter[0]
            _cache[eid] = acc
            x, y = display.to_shot((box[0] + box[2] / 2), (box[1] + box[3] / 2))
            extra = []
            if states is not None:
                if states.contains(S.FOCUSED):
                    extra.append("focused")
                if states.contains(S.CHECKED) or states.contains(S.PRESSED):
                    extra.append("checked")
                if states.contains(S.SELECTED):
                    extra.append("selected")
                if not states.contains(S.ENABLED) and not states.contains(S.SENSITIVE):
                    extra.append("disabled")
            if role in ("entry", "text", "password text", "spin button", "combo box"):
                txt = _text_of(acc)
                if txt:
                    extra.append(f"value={txt[:60]!r}")
            flag = f" [{', '.join(extra)}]" if extra else ""
            label = f' "{name[:80]}"' if name else ""
            lines.append(f"{'  ' * indent}[{eid}] {role}{label} @({x},{y}){flag}")
            child_indent = indent + 1
        if depth >= max_depth:
            return
        n = _safe(acc.get_child_count, 0) or 0
        if n > 500:  # huge lists/tables: only the visible part matters, cap it
            n = 500
        for i in range(n):
            child = _safe(lambda: acc.get_child_at_index(i))
            if child is not None:
                walk(child, depth + 1, child_indent)

    for a in _apps():
        app_name = _safe(a.get_name, "") or ""
        if app and app.lower() not in app_name.lower():
            continue
        for j in range(_safe(a.get_child_count, 0) or 0):
            win = _safe(lambda: a.get_child_at_index(j))
            if win is None:
                continue
            st = _safe(win.get_state_set)
            if st is not None and not st.contains(S.SHOWING):
                continue
            if active_window_only and not (st and st.contains(S.ACTIVE)):
                continue
            title = _safe(win.get_name, "") or ""
            if window and window.lower() not in title.lower():
                continue
            lines.append(f'== {app_name}: "{title}"' + (" (active)" if st and st.contains(S.ACTIVE) else ""))
            walk(win, 0, 1)

    if counter[0] >= MAX_NODES:
        lines.append(f"... truncated at {MAX_NODES} elements; narrow with app=/window=")
    elif time.monotonic() > deadline:
        lines.append("... truncated (timeout); narrow with app=/window=")
    if not lines:
        hint = "No accessible windows matched."
        if accessibility_enabled() is False:
            hint += (" Accessibility is OFF: run `gsettings set org.gnome.desktop.interface"
                     " toolkit-accessibility true` and restart the apps.")
        return hint
    return "\n".join(lines)


def _text_of(acc) -> str:
    # call interface methods via their class: Accessible.get_text() etc. are ambiguous in gi
    if _safe(acc.get_text_iface) is None:
        return ""
    n = _safe(lambda: Atspi.Text.get_character_count(acc), 0) or 0
    return _safe(lambda: Atspi.Text.get_text(acc, 0, min(n, 200)), "") or ""


def get(eid: int):
    acc = _cache.get(eid)
    if acc is None:
        raise KeyError(f"unknown element id {eid}; call ui_tree again (ids reset on every call)")
    return acc


def center(eid: int) -> tuple[int, int]:
    """Real-pixel center of an element."""
    box = _extents(get(eid))
    if box is None:
        raise RuntimeError(f"element {eid} has no on-screen extents")
    return round(box[0] + box[2] / 2), round(box[1] + box[3] / 2)


PREFERRED_ACTIONS = ("click", "press", "activate", "jump", "toggle", "open", "select")


def do_action(eid: int) -> str | None:
    """Invoke the element's default AT-SPI action. Returns action name or None."""
    acc = get(eid)
    if _safe(acc.get_action_iface) is None:
        return None
    n = _safe(lambda: Atspi.Action.get_n_actions(acc), 0) or 0
    names = [(_safe(lambda: Atspi.Action.get_action_name(acc, i), "") or "").lower() for i in range(n)]
    for want in PREFERRED_ACTIONS:
        if want in names:
            i = names.index(want)
            if _safe(lambda: Atspi.Action.do_action(acc, i), False):
                return want
    return None


def set_text(eid: int, text: str) -> bool:
    acc = get(eid)
    if _safe(acc.get_editable_text_iface) is None:
        return False
    _safe(lambda: Atspi.Component.grab_focus(acc))
    return bool(_safe(lambda: Atspi.EditableText.set_text_contents(acc, text), False))


def focus(eid: int) -> bool:
    acc = get(eid)
    if _safe(acc.get_component_iface) is None:
        return False
    return bool(_safe(lambda: Atspi.Component.grab_focus(acc), False))
