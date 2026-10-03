"""Synthetic mouse/keyboard input through the XTEST extension.

All public functions take *real* screen pixels; server.py converts from
screenshot space before calling in.
"""

import time
from contextlib import contextmanager

from Xlib import X, XK
from Xlib.ext import xtest

from . import display, ime, keys

BUTTONS = {"left": 1, "middle": 2, "right": 3, "back": 8, "forward": 9}
SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}

KEY_DELAY = 0.008  # between synthetic key events while typing
_SHIFT = XK.string_to_keysym("Shift_L")
_SHIFTS = {_SHIFT, XK.string_to_keysym("Shift_R")}


def _d():
    return display.get_display()


def _flush():
    _d().sync()


# --------------------------------------------------------------------- mouse

def move(x: int, y: int) -> None:
    with display.lock():
        xtest.fake_input(_d(), X.MotionNotify, x=x, y=y)
        _flush()


def position() -> tuple[int, int]:
    with display.lock():
        p = _d().screen().root.query_pointer()
        return p.root_x, p.root_y


def button(btn: int, down: bool) -> None:
    with display.lock():
        xtest.fake_input(_d(), X.ButtonPress if down else X.ButtonRelease, btn)
        _flush()


def click(x: int, y: int, btn: str = "left", count: int = 1, modifiers: list[str] | None = None) -> None:
    code = BUTTONS[btn]
    move(x, y)
    time.sleep(0.03)
    with hold(modifiers or []):
        for i in range(count):
            button(code, True)
            time.sleep(0.02)
            button(code, False)
            if i + 1 < count:
                time.sleep(0.06)


def drag(x1: int, y1: int, x2: int, y2: int, btn: str = "left", steps: int = 20) -> None:
    code = BUTTONS[btn]
    move(x1, y1)
    time.sleep(0.05)
    button(code, True)
    time.sleep(0.05)
    for i in range(1, steps + 1):
        move(round(x1 + (x2 - x1) * i / steps), round(y1 + (y2 - y1) * i / steps))
        time.sleep(0.01)
    time.sleep(0.05)
    button(code, False)


def scroll(x: int, y: int, direction: str, amount: int = 3, modifiers: list[str] | None = None) -> None:
    code = SCROLL_BUTTONS[direction]
    move(x, y)
    time.sleep(0.02)
    with hold(modifiers or []):
        for _ in range(amount):
            button(code, True)
            button(code, False)
            time.sleep(0.02)


# ------------------------------------------------------------------ keyboard

class _Keymap:
    """Resolves keysyms to keycodes, borrowing a spare keycode when needed.

    Characters missing from the active layout (Hangul, emoji, CJK...) are typed
    by temporarily binding their Unicode keysym to an unused keycode, the same
    trick xdotool uses. The binding is removed afterwards.
    """

    def __init__(self):
        self._spare = None
        self._dirty = False

    def lookup(self, ks: int) -> tuple[int, bool] | None:
        best = None
        for code, index in _d().keysym_to_keycodes(ks):
            if index == 0:
                return code, False
            if index == 1 and best is None:
                best = (code, True)
        return best

    def _find_spare(self) -> int:
        d = _d()
        first = d.display.info.min_keycode
        count = d.display.info.max_keycode - first + 1
        mapping = d.get_keyboard_mapping(first, count)
        # search from the top: high keycodes are rarely bound to real keys
        for i in range(count - 1, -1, -1):
            if not any(mapping[i]):
                return first + i
        raise RuntimeError("no spare keycode available for remapping")

    def borrow(self, ks: int) -> int:
        d = _d()
        if self._spare is None:
            self._spare = self._find_spare()
        per = len(d.get_keyboard_mapping(self._spare, 1)[0])
        d.change_keyboard_mapping(self._spare, [[ks] * per])
        d.sync()
        self._dirty = True
        time.sleep(0.03)  # let clients process MappingNotify before the key arrives
        return self._spare

    def restore(self):
        if self._dirty and self._spare is not None:
            d = _d()
            per = len(d.get_keyboard_mapping(self._spare, 1)[0])
            d.change_keyboard_mapping(self._spare, [[0] * per])
            d.sync()
            self._dirty = False


def _key_event(code: int, down: bool) -> None:
    xtest.fake_input(_d(), X.KeyPress if down else X.KeyRelease, code)
    _flush()


def _resolve(km: _Keymap, ks: int) -> tuple[int, bool]:
    hit = km.lookup(ks)
    if hit is not None:
        return hit
    return km.borrow(ks), False


@contextmanager
def hold(names: list[str]):
    """Hold modifier/other keys (by name) for the duration of the block."""
    with display.lock():
        km = _Keymap()
        codes = []
        try:
            for n in names:
                code, _ = _resolve(km, keys.name_keysym(n))
                _key_event(code, True)
                codes.append(code)
            yield
        finally:
            for code in reversed(codes):
                _key_event(code, False)
            km.restore()


def press_combo(combo: str, repeat: int = 1) -> None:
    """Press a chord such as 'ctrl+shift+t' (all down in order, up in reverse)."""
    syms = keys.parse_combo(combo)
    with display.lock():
        km = _Keymap()
        shift_code = km.lookup(_SHIFT)[0]
        try:
            for _ in range(repeat):
                pressed = []
                has_shift = any(s in _SHIFTS for s in syms)
                for i, ks in enumerate(syms):
                    code, need_shift = _resolve(km, ks)
                    # "?" or "ctrl+plus" need Shift on most layouts; add it for
                    # the final key unless the caller already holds Shift.
                    if need_shift and i == len(syms) - 1 and not has_shift:
                        _key_event(shift_code, True)
                        pressed.append(shift_code)
                    _key_event(code, True)
                    pressed.append(code)
                    time.sleep(KEY_DELAY)
                for code in reversed(pressed):
                    _key_event(code, False)
                    time.sleep(KEY_DELAY)
        finally:
            km.restore()


def type_text(text: str, delay: float = KEY_DELAY) -> None:
    with display.lock(), ime.plain_input():
        km = _Keymap()
        shift_code = km.lookup(_SHIFT)[0]
        try:
            for ch in text:
                code, need_shift = _resolve(km, keys.char_keysym(ch))
                if need_shift:
                    _key_event(shift_code, True)
                _key_event(code, True)
                _key_event(code, False)
                if need_shift:
                    _key_event(shift_code, False)
                time.sleep(delay)
        finally:
            km.restore()
