"""Pure-logic tests: no X server needed."""

import math

from Xlib import XK

from lcu import keys, motion


def test_parse_combo_aliases_and_case():
    assert keys.parse_combo("ctrl+shift+t") == [
        XK.string_to_keysym("Control_L"), XK.string_to_keysym("Shift_L"), ord("t")]
    # "ctrl+A" means ctrl+a, not ctrl+shift+a
    assert keys.parse_combo("ctrl+A")[-1] == ord("a")
    assert keys.parse_combo("Return") == [XK.string_to_keysym("Return")]
    assert keys.parse_combo("ctrl++")[-1] == XK.string_to_keysym("plus")


def test_char_keysym_unicode():
    assert keys.char_keysym("a") == ord("a")
    assert keys.char_keysym("\n") == XK.string_to_keysym("Return")
    assert keys.char_keysym("한") == 0x01000000 + ord("한")


def test_segments_respect_spare_keycodes():
    from lcu import input as inp

    class FakeKeymap(inp._Keymap):
        def spares(self):
            return [250, 251]

        def in_layout(self, ks):
            return (10, False) if ks < 0x100 else None

    pieces = list(inp._segments(FakeKeymap(), "ab테스트트 가x"))
    assert "".join(p for p, _ in pieces) == "ab테스트트 가x"
    for _, need in pieces:
        assert len(need) <= 2 and len(set(need)) == len(need)
    assert [[chr(k - 0x01000000) for k in n] for _, n in pieces] == [["테", "스"], ["트", "가"]]


def test_trajectory_reaches_target_on_screen():
    start, end, size = (100, 600), (1100, 150), (1280, 720)
    frames = list(motion.trajectory(start, end, size=size))
    x, y, *_ = frames[-1]
    assert math.hypot(x - end[0], y - end[1]) < 1.0
    assert all(-1 <= fx <= size[0] + 1 and -1 <= fy <= size[1] + 1 for fx, fy, *_ in frames)
    # spring timing as in Codex: response = dist/1000*0.9, clamped
    assert motion.move_response(10) == 0.12
    assert motion.move_response(10_000) == 2.2


def test_short_move_is_straight():
    path = motion.plan_path((0, 0), (5, 5))
    for t in (0.25, 0.5, 0.75):
        x, y = path.sample(t)
        assert abs(x - y) < 1e-9
