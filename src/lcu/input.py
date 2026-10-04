"""Synthetic mouse/keyboard input through the XTEST extension.

All public functions take *real* screen pixels; server.py converts from
screenshot space before calling in.
"""

import math
import os
import time
from contextlib import contextmanager

from Xlib import X, XK
from Xlib.ext import xtest

from . import display, ime, keymap_state, keys
from . import motion as lmotion

BUTTONS = {"left": 1, "middle": 2, "right": 3, "back": 8, "forward": 9}
SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}

KEY_DELAY = 0.008  # between synthetic key events while typing
_SHIFT = XK.string_to_keysym("Shift_L")
_SHIFTS = {_SHIFT, XK.string_to_keysym("Shift_R")}


def _d():
    return display.get_display()


def _flush():
    _d().sync()


def _kmd():
    # keymap edits go to the user's core keyboard map, which is what toolkits
    # consult even for keys coming from the agent's own keyboard
    return display.get_core_display()


# Hooks set by server.py to drive the cursor overlay (real screen pixels).
on_move = None    # (x, y, motion | None) -> None
on_button = None  # (button_name, down: bool) -> None

GLIDE = os.environ.get("LCU_GLIDE", "1") != "0"


# --------------------------------------------------------------------- mouse

def move(x: int, y: int, motion: dict | None = None) -> None:
    """Put the pointer at (x, y). `motion` carries glide state for the overlay."""
    with display.lock():
        xtest.fake_input(_d(), X.MotionNotify, x=x, y=y)
        _flush()
    if on_move:
        on_move(x, y, motion)


def glide(x: int, y: int) -> None:
    """Travel like the Codex agent cursor: a planned cubic arc driven by a
    damped spring (see motion.py). Hover effects fire along the way and the
    overlay mirrors every frame. LCU_GLIDE=0 makes this a plain jump.
    """
    x0, y0 = position()
    dist = math.hypot(x - x0, y - y0)
    if not GLIDE or dist < 2:
        move(x, y)
        return
    last = None
    t_next = time.monotonic()
    for fx, fy, tan, pv, dt in lmotion.trajectory((x0, y0), (x, y), size=display.real_size()):
        px, py = round(fx), round(fy)
        if (px, py) != last:
            move(px, py, {"tan": tan, "pv": pv, "dist": dist, "dt": dt})
            last = (px, py)
        t_next += dt
        delay = t_next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    move(x, y, {"arrive": True})


def position() -> tuple[int, int]:
    with display.lock():
        p = _d().screen().root.query_pointer()
        return p.root_x, p.root_y


_BUTTON_NAMES = {v: k for k, v in BUTTONS.items()}


def button(btn: int, down: bool) -> None:
    with display.lock():
        xtest.fake_input(_d(), X.ButtonPress if down else X.ButtonRelease, btn)
        _flush()
    if on_button and btn in _BUTTON_NAMES:
        on_button(_BUTTON_NAMES[btn], down)


def click(x: int, y: int, btn: str = "left", count: int = 1, modifiers: list[str] | None = None) -> None:
    code = BUTTONS[btn]
    glide(x, y)
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
    glide(x1, y1)
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
    glide(x, y)
    time.sleep(0.02)
    with hold(modifiers or []):
        for _ in range(amount):
            button(code, True)
            button(code, False)
            time.sleep(0.02)


# ------------------------------------------------------------------ keyboard

# How long clients get to pick up a keymap change before the first key that
# depends on it. Some (ibus, Electron) refresh their keymap lazily; with too
# short a wait the first remapped character is lost or typed with the stale
# binding (e.g. "테스트" -> "홈스트").
REMAP_SETTLE = float(os.environ.get("LCU_REMAP_SETTLE", "0.12"))


class _Keymap:
    """Resolves keysyms to keycodes, borrowing spare keycodes when needed.

    Characters missing from the active layout (Hangul, emoji, CJK...) are typed
    by temporarily binding their Unicode keysym to an unused keycode, the same
    trick xdotool uses. Bindings are made in batches, each keysym on its own
    keycode, followed by one sync + settle wait, so clients see a single
    MappingNotify per batch instead of one per character. They are removed
    afterwards.
    """

    def __init__(self, journal=None):
        self._spares: list[int] | None = None
        self._bound: dict[int, int] = {}  # keysym -> borrowed keycode (current batch)
        self._touched: set[int] = set()   # every keycode we changed, for restore()
        self._entries: dict[int, dict] = {}
        self._journal = journal

    def lookup(self, ks: int) -> tuple[int, bool] | None:
        if ks in self._bound:
            return self._bound[ks], False
        return self.in_layout(ks)

    def in_layout(self, ks: int) -> tuple[int, bool] | None:
        best = None
        for code, index in _d().keysym_to_keycodes(ks):
            if index == 0:
                return code, False
            if index == 1 and best is None:
                best = (code, True)
        return best

    def spares(self) -> list[int]:
        if self._spares is None:
            d = _kmd()
            first = d.display.info.min_keycode
            count = d.display.info.max_keycode - first + 1
            mapping = d.get_keyboard_mapping(first, count)
            # from the top: high keycodes are rarely bound to real keys
            self._spares = [first + i for i in range(count - 1, -1, -1) if not any(mapping[i])]
            if not self._spares:
                raise RuntimeError("no spare keycode available for remapping")
        return self._spares

    def bind(self, keysyms: list[int]) -> None:
        """Bind up to len(spares()) keysyms at once, then wait for clients."""
        if not keysyms:
            return
        d = _kmd()
        spares = self.spares()
        if len(keysyms) > len(spares):
            raise ValueError("more keysyms than spare keycodes")
        if self._journal is None:
            raise RuntimeError("keymap changes require a recovery journal")
        updates = []
        for ks, code in zip(keysyms, spares):
            current = list(d.get_keyboard_mapping(code, 1)[0])
            previous = self._entries.get(code)
            if previous is None and any(current):
                raise RuntimeError(f"spare keycode {code} is no longer empty")
            if previous is not None and not keymap_state.owned_mapping(current, previous["bound"]):
                raise RuntimeError(f"borrowed keycode {code} changed externally")
            updates.append((ks, code, current, previous))
        for ks, code, current, previous in updates:
            self._entries[code] = {"code": code, "before": previous["before"] if previous else current,
                                   "bound": [ks] * len(current),
                                   "prior": previous["bound"] if previous else current}
            self._touched.add(code)
        keymap_state.write(self._journal, list(self._entries.values()))
        self._bound = {}
        for ks, code, current, _ in updates:
            d.change_keyboard_mapping(code, [[ks] * len(current)])
            self._bound[ks] = code
        # round-trip on both connections: the server has applied the change
        # and the agent connection is ordered after it
        d.sync()
        _d().sync()
        time.sleep(REMAP_SETTLE)

    def borrow(self, ks: int) -> int:
        if ks not in self._bound:
            self.bind([*self._bound, ks][-len(self.spares()):])
        return self._bound[ks]

    def restore(self):
        if not self._touched:
            return
        d = _kmd()
        for code in self._touched:
            entry = self._entries[code]
            if keymap_state.matches_entry(list(d.get_keyboard_mapping(code, 1)[0]), entry):
                d.change_keyboard_mapping(code, [entry["before"]])
        d.sync()
        keymap_state.clear(self._journal)
        self._bound, self._touched = {}, set()


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
    with display.lock(), keymap_state.session() as journal:
        km = _Keymap(journal)
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
    with display.lock(), keymap_state.session() as journal:
        km = _Keymap(journal)
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


def _segments(km: _Keymap, text: str):
    """Split text so each piece needs no more borrowed keysyms than there are
    spare keycodes. Yields (piece, keysyms_to_bind)."""
    piece, need = [], []
    limit = None
    for ch in text:
        ks = keys.char_keysym(ch)
        if km.in_layout(ks) is None and ks not in need:
            if limit is None:
                limit = len(km.spares())
            if len(need) == limit:
                yield "".join(piece), need
                piece, need = [], []
            need.append(ks)
        piece.append(ch)
    if piece:
        yield "".join(piece), need


def type_text(text: str, delay: float = KEY_DELAY) -> None:
    with display.lock(), keymap_state.session() as journal, ime.plain_input():
        km = _Keymap(journal)
        shift_code = km.lookup(_SHIFT)[0]
        try:
            for piece, need in _segments(km, text):
                km.bind(need)
                for ch in piece:
                    code, need_shift = km.lookup(keys.char_keysym(ch))
                    if need_shift:
                        _key_event(shift_code, True)
                    _key_event(code, True)
                    _key_event(code, False)
                    if need_shift:
                        _key_event(shift_code, False)
                    time.sleep(delay)
                # let the last remapped keys be processed before rebinding
                if need:
                    time.sleep(REMAP_SETTLE / 2)
        finally:
            km.restore()
