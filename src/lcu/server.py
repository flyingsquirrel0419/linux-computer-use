"""MCP server exposing Linux (X11) desktop control to Claude Code, Codex, etc."""

import json
import time
from typing import Literal

from mcp.server.fastmcp import FastMCP, Image

from . import env

env.ensure()  # before anything touches X11 or D-Bus

from . import capture, display, input as inp  # noqa: E402

INSTRUCTIONS = """\
Controls the user's Linux X11 desktop.

Coordinates: every x/y you send or receive is in the pixel space of the image
returned by `screenshot` (full screen, possibly downscaled). Never rescale them
yourself. The red cross in screenshots is the mouse pointer.

Workflow: screenshot -> act -> verify. Action tools accept `screenshot_after`
to return a fresh screenshot in the same call; prefer that over a separate
screenshot call. For native apps, `ui_tree` lists widgets with their center
coordinates and ids; `click_element`/`set_text` act on them directly and are
more reliable than pixel guessing. Before typing, confirm focus with
`active_window` or pass `expect_window` to `type_text`/`key`. Use `type_text` for text (any Unicode,
including Korean) and `key` for shortcuts like "ctrl+l" or "Return".
"""

mcp = FastMCP("linux-computer-use", instructions=INSTRUCTIONS)

Button = Literal["left", "right", "middle", "back", "forward"]


def _a11y():
    from . import a11y  # lazy: the server still works without PyGObject/AT-SPI
    return a11y


def _shot() -> Image:
    return Image(data=capture.grab(), format="png")


def _result(msg: str, screenshot_after: bool, settle: float = 0.4):
    if not screenshot_after:
        return msg
    time.sleep(settle)  # let the UI repaint before capturing
    return [msg, _shot()]


# ---------------------------------------------------------------- perception

@mcp.tool()
def screenshot(x: int | None = None, y: int | None = None,
               width: int | None = None, height: int | None = None) -> Image:
    """Capture the screen. Optionally pass x/y/width/height (screenshot coords)
    to zoom into a region for reading small text; coordinates you act on must
    still be full-screen screenshot coordinates."""
    region = None
    if None not in (x, y, width, height):
        region = (x, y, width, height)
    return Image(data=capture.grab(region), format="png")


@mcp.tool()
def screen_info() -> str:
    """Screen size (real and screenshot space), scale, and pointer position."""
    rw, rh = display.real_size()
    sw, sh = display.shot_size()
    px, py = display.to_shot(*inp.position())
    return json.dumps({
        "screenshot_size": [sw, sh], "real_size": [rw, rh],
        "scale": round(display.scale(), 4), "cursor": [px, py],
    })


@mcp.tool()
def active_window() -> str:
    """The focused window: title, WM class, pid. Check this before typing so
    keystrokes don't land in the wrong app."""
    return json.dumps(display.active_window(), ensure_ascii=False)


def _check_focus(expect_window: str | None) -> str | None:
    """Return an error message if the focused window doesn't match."""
    if not expect_window:
        return None
    w = display.active_window()
    hay = f"{w['title']} {w['class']}".lower()
    if expect_window.lower() in hay:
        return None
    return (f"NOT SENT: focused window is {w['title']!r} ({w['class']}), "
            f"expected {expect_window!r}. Focus the right window first.")


@mcp.tool()
def cursor_position() -> str:
    """Current mouse pointer position in screenshot coordinates."""
    x, y = display.to_shot(*inp.position())
    return json.dumps({"x": x, "y": y})


# --------------------------------------------------------------------- mouse

@mcp.tool()
def click(x: int, y: int, button: Button = "left", count: int = 1,
          modifiers: list[str] | None = None, screenshot_after: bool = False):
    """Click at (x, y). count=2 for double-click, 3 for triple-click.
    modifiers e.g. ["ctrl"] or ["shift"] are held during the click."""
    rx, ry = display.to_real(x, y)
    inp.click(rx, ry, button, count, modifiers)
    return _result(f"clicked {button} x{count} at ({x},{y})", screenshot_after)


@mcp.tool()
def mouse_move(x: int, y: int, screenshot_after: bool = False):
    """Move the pointer to (x, y) without clicking (e.g. to reveal hover menus)."""
    inp.move(*display.to_real(x, y))
    return _result(f"moved to ({x},{y})", screenshot_after)


@mcp.tool()
def drag(start_x: int, start_y: int, end_x: int, end_y: int,
         button: Button = "left", screenshot_after: bool = False):
    """Press at start, move smoothly to end, release."""
    inp.drag(*display.to_real(start_x, start_y), *display.to_real(end_x, end_y), btn=button)
    return _result(f"dragged ({start_x},{start_y}) -> ({end_x},{end_y})", screenshot_after)


@mcp.tool()
def mouse_down(button: Button = "left") -> str:
    """Press and hold a mouse button at the current pointer position."""
    inp.button(inp.BUTTONS[button], True)
    return f"{button} down"


@mcp.tool()
def mouse_up(button: Button = "left", screenshot_after: bool = False):
    """Release a mouse button."""
    inp.button(inp.BUTTONS[button], False)
    return _result(f"{button} up", screenshot_after)


@mcp.tool()
def scroll(x: int, y: int, direction: Literal["up", "down", "left", "right"] = "down",
           amount: int = 3, modifiers: list[str] | None = None,
           screenshot_after: bool = False):
    """Scroll the wheel at (x, y). amount = number of wheel clicks."""
    inp.scroll(*display.to_real(x, y), direction, amount, modifiers)
    return _result(f"scrolled {direction} x{amount} at ({x},{y})", screenshot_after)


# ------------------------------------------------------------------ keyboard

@mcp.tool()
def type_text(text: str, expect_window: str | None = None, screenshot_after: bool = False):
    """Type text into the focused widget. Any Unicode works (Korean, emoji...).
    Newlines press Return. expect_window: substring of the focused window's
    title or class; if it doesn't match, nothing is typed."""
    if err := _check_focus(expect_window):
        return err
    inp.type_text(text)
    return _result(f"typed {len(text)} chars", screenshot_after)


@mcp.tool()
def key(combo: str, repeat: int = 1, expect_window: str | None = None,
        screenshot_after: bool = False):
    """Press a key or chord: "Return", "ctrl+c", "ctrl+shift+t", "alt+F4",
    "super", "Escape", "Page_Down". X keysym names are accepted.
    expect_window works as in type_text."""
    if err := _check_focus(expect_window):
        return err
    inp.press_combo(combo, repeat)
    return _result(f"pressed {combo}" + (f" x{repeat}" if repeat > 1 else ""), screenshot_after)


@mcp.tool()
def hold_key(combo: str, seconds: float, screenshot_after: bool = False):
    """Hold key(s) down for `seconds` (max 10), e.g. for games or key repeat."""
    with inp.hold([k for k in combo.split("+") if k]):
        time.sleep(min(max(seconds, 0), 10))
    return _result(f"held {combo} for {seconds}s", screenshot_after)


@mcp.tool()
def wait(seconds: float = 1.0, screenshot_after: bool = True):
    """Wait for the UI (max 30s), then by default return a screenshot."""
    time.sleep(min(max(seconds, 0), 30))
    return _result(f"waited {seconds}s", screenshot_after, settle=0)


# ------------------------------------------------------------- accessibility

@mcp.tool()
def list_windows() -> str:
    """Top-level windows known to AT-SPI: app, title, active/visible, box."""
    return json.dumps(_a11y().list_windows(), ensure_ascii=False, indent=1)


@mcp.tool()
def ui_tree(app: str | None = None, window: str | None = None,
            active_window_only: bool = False, only_interactive: bool = True,
            max_depth: int = 40) -> str:
    """List visible widgets as `[id] role "name" @(x,y)` with center coordinates
    in screenshot space. Filter by app/window name substring, or
    active_window_only=True. Ids are valid until the next ui_tree call.
    Apps only appear if accessibility is enabled (Chrome/Electron need
    --force-renderer-accessibility)."""
    return _a11y().tree(app, window, max_depth, only_interactive, active_window_only)


@mcp.tool()
def click_element(id: int, method: Literal["auto", "action", "mouse"] = "auto",
                  screenshot_after: bool = False):
    """Activate a ui_tree element. auto = AT-SPI action if available, else a
    real mouse click at its center."""
    a = _a11y()
    if method in ("auto", "action"):
        done = a.do_action(id)
        if done:
            return _result(f"element {id}: action '{done}'", screenshot_after)
        if method == "action":
            return f"element {id} has no usable action"
    rx, ry = a.center(id)
    inp.click(rx, ry)
    return _result(f"element {id}: clicked at {display.to_shot(rx, ry)}", screenshot_after)


@mcp.tool()
def set_text(id: int, text: str, screenshot_after: bool = False):
    """Replace the contents of an editable ui_tree element. Falls back to
    focusing it, select-all and typing when the widget isn't directly editable."""
    a = _a11y()
    if a.set_text(id, text):
        return _result(f"element {id}: text set", screenshot_after)
    if not a.focus(id):
        inp.click(*a.center(id))
    time.sleep(0.1)
    inp.press_combo("ctrl+a")
    inp.type_text(text)
    return _result(f"element {id}: typed (fallback)", screenshot_after)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
