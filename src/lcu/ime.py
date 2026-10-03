"""Keep the input method from rewriting synthetic keystrokes.

With ibus-hangul (or any CJK engine) in Hangul mode, typed ASCII is composed
into Hangul ("llo" -> "ㅣㅣㅐ"). While typing we switch ibus to a plain US
engine and switch back afterwards. Characters the layout lacks are sent as
Unicode keysyms (see input._Keymap), which engines pass through untouched, so
Korean text still comes out right.

Side effect: ibus-hangul re-enters its `initial-input-mode` (usually latin)
when restored. Disable with LCU_IME_BYPASS=0.
"""

import os
import shutil
import subprocess
import time
from contextlib import contextmanager

PLAIN_ENGINE = os.environ.get("LCU_PLAIN_ENGINE", "xkb:us::eng")
ENABLED = os.environ.get("LCU_IME_BYPASS", "1") != "0"


def _ibus(*args: str) -> str | None:
    if not shutil.which("ibus"):
        return None
    try:
        r = subprocess.run(["ibus", *args], capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


@contextmanager
def plain_input():
    current = _ibus("engine") if ENABLED else None
    switched = bool(current) and not current.startswith("xkb:")
    if switched:
        _ibus("engine", PLAIN_ENGINE)
        time.sleep(0.15)
    try:
        yield
    finally:
        if switched:
            time.sleep(0.05)
            _ibus("engine", current)
