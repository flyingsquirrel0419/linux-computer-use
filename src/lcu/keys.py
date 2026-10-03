"""Key-name parsing: "ctrl+shift+t", "Return", "alt+F4", "super" ...

Accepts xdotool-style X keysym names as well as common aliases, so prompts
written for Anthropic's computer-use tool work unchanged.
"""

from Xlib import XK

ALIASES = {
    "ctrl": "Control_L", "control": "Control_L", "lctrl": "Control_L", "rctrl": "Control_R",
    "alt": "Alt_L", "lalt": "Alt_L", "ralt": "Alt_R", "altgr": "ISO_Level3_Shift",
    "shift": "Shift_L", "lshift": "Shift_L", "rshift": "Shift_R",
    "super": "Super_L", "win": "Super_L", "windows": "Super_L", "meta": "Super_L",
    "cmd": "Super_L", "command": "Super_L",
    "enter": "Return", "return": "Return", "ret": "Return",
    "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "bksp": "BackSpace",
    "del": "Delete", "delete": "Delete", "ins": "Insert", "insert": "Insert",
    "tab": "Tab", "space": "space", "spacebar": "space",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "pageup": "Prior", "page_up": "Prior", "pgup": "Prior",
    "pagedown": "Next", "page_down": "Next", "pgdn": "Next",
    "home": "Home", "end": "End",
    "capslock": "Caps_Lock", "numlock": "Num_Lock", "scrolllock": "Scroll_Lock",
    "printscreen": "Print", "print": "Print", "prtsc": "Print",
    "menu": "Menu", "pause": "Pause",
    "plus": "plus", "minus": "minus", "comma": "comma", "period": "period",
    "slash": "slash", "backslash": "backslash", "semicolon": "semicolon",
    "hangul": "Hangul", "hanja": "Hangul_Hanja", "han/eng": "Hangul",
}


def char_keysym(ch: str) -> int:
    """Keysym for a single character (Latin-1 identity, else Unicode keysym)."""
    if ch == "\n":
        return XK.string_to_keysym("Return")
    if ch == "\t":
        return XK.string_to_keysym("Tab")
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 + cp


def name_keysym(name: str) -> int:
    """Keysym for a single key token ("a", "F5", "ctrl", "Return", "ㄱ"...)."""
    if len(name) == 1:
        return char_keysym(name)
    canonical = ALIASES.get(name.lower(), name)
    ks = XK.string_to_keysym(canonical)
    if not ks:
        # try case variants: "f5" -> "F5", "pagedown" handled above
        ks = XK.string_to_keysym(canonical.capitalize()) or XK.string_to_keysym(canonical.upper())
    if not ks:
        raise ValueError(f"unknown key name: {name!r}")
    return ks


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+t' -> [keysym, ...] in press order. A lone '+' is the plus key."""
    combo = combo.strip()
    if combo == "+":
        return [name_keysym("plus")]
    parts = [p for p in combo.replace(" ", "").split("+")]
    # "ctrl++" -> trailing empty token means the plus key
    out = []
    for i, p in enumerate(parts):
        if p == "":
            if i == len(parts) - 1:
                out.append(name_keysym("plus"))
            continue
        # "ctrl+A" almost always means ctrl+a, not ctrl+shift+a
        if len(parts) > 1 and len(p) == 1 and "A" <= p <= "Z":
            p = p.lower()
        out.append(name_keysym(p))
    return out
