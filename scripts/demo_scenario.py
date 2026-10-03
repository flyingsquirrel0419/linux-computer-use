"""Record docs/demo.gif on a throwaway X display (run via scripts/record_demo.sh).

Two gedit windows: the agent works in the left one through the real MCP
tools while a simulated user keeps typing in the right one with the core
pointer and keyboard. The agent cursor is the real overlay; the user's
cursor isn't part of Xvfb captures, so it is drawn onto the frames.

Modes:
  --background   show a desktop-type gradient window (the wallpaper)
  (default)      run the scenario and write the GIF
"""

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
W, H = 1280, 720
FPS = 12


def background() -> None:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    import cairo
    from gi.repository import Gdk, Gtk

    win = Gtk.Window()
    win.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
    win.set_default_size(W, H)
    win.move(0, 0)
    win.set_app_paintable(True)

    def draw(_w, cr):
        g = cairo.LinearGradient(0, 0, W, H)
        g.add_color_stop_rgb(0, 0.07, 0.10, 0.20)
        g.add_color_stop_rgb(1, 0.10, 0.22, 0.36)
        cr.set_source(g)
        cr.paint()

    win.connect("draw", draw)
    win.show_all()
    Gtk.main()


# ------------------------------------------------------------------ helpers

def x_windows():
    from Xlib import display
    d = display.Display()
    root = d.screen().root
    out = {}
    for c in root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), 0).value:
        w = d.create_resource_object("window", c)
        p = w.get_full_property(d.intern_atom("_NET_WM_NAME"), d.intern_atom("UTF8_STRING"))
        if p:
            out[p.value.decode()] = w
    return d, root, out


def place(name: str, x: int, y: int, w: int, h: int) -> None:
    from Xlib import X, protocol
    d, root, wins = x_windows()
    win = next(v for k, v in wins.items() if name in k)
    ev = protocol.event.ClientMessage(
        window=win, client_type=d.intern_atom("_NET_MOVERESIZE_WINDOW"),
        data=(32, [(1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | 10, x, y, w, h]))
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.sync()


def activate(name: str) -> None:
    from Xlib import X, protocol
    d, root, wins = x_windows()
    win = next(v for k, v in wins.items() if name in k)
    ev = protocol.event.ClientMessage(window=win, client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
                                      data=(32, [2, X.CurrentTime, 0, 0, 0]))
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.sync()


def draw_user_cursor(img, x: int, y: int) -> None:
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    arrow = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12)]
    pts = [(x + px, y + py) for px, py in arrow]
    d.polygon(pts, fill=(20, 20, 20), outline=(255, 255, 255))
    d.line(pts + [pts[0]], fill=(255, 255, 255), width=1)
    tx, ty = x + 14, y + 18
    d.rounded_rectangle([tx, ty, tx + 34, ty + 18], radius=9, fill=(40, 40, 46), outline=(255, 255, 255))
    d.text((tx + 7, ty + 3), "You", fill=(255, 255, 255))


class Recorder(threading.Thread):
    def __init__(self, out_dir: pathlib.Path):
        super().__init__(daemon=True)
        self.out_dir, self.stop = out_dir, threading.Event()
        self.n = 0

    def run(self):
        import mss
        from PIL import Image
        from Xlib import display
        core = display.Display()  # its pointer is the user's core pointer
        with mss.MSS() as sct:
            nxt = time.monotonic()
            while not self.stop.is_set():
                raw = sct.grab({"left": 0, "top": 0, "width": W, "height": H})
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                p = core.screen().root.query_pointer()
                draw_user_cursor(img, p.root_x, p.root_y)
                img.save(self.out_dir / f"{self.n:05d}.png", compress_level=1)
                self.n += 1
                nxt += 1 / FPS
                time.sleep(max(0, nxt - time.monotonic()))


def user_actions(stop_after: float) -> None:
    """The 'human': glides the core pointer into the right window and types."""
    from Xlib import X, display
    from Xlib.ext import xtest
    d = display.Display()

    def move(x, y, dur=0.6):
        p = d.screen().root.query_pointer()
        x0, y0 = p.root_x, p.root_y
        steps = int(dur * 60)
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)
            xtest.fake_input(d, X.MotionNotify, x=round(x0 + (x - x0) * t), y=round(y0 + (y - y0) * t))
            d.sync()
            time.sleep(dur / steps)

    def type_(s, cps=11):
        for ch in s:
            ks = 0xFF0D if ch == "\n" else ord(ch)
            codes = list(d.keysym_to_keycodes(ks))
            code, idx = codes[0]
            if idx == 1:
                xtest.fake_input(d, X.KeyPress, d.keysym_to_keycode(0xFFE1))
            xtest.fake_input(d, X.KeyPress, code)
            xtest.fake_input(d, X.KeyRelease, code)
            if idx == 1:
                xtest.fake_input(d, X.KeyRelease, d.keysym_to_keycode(0xFFE1))
            d.sync()
            time.sleep(1 / cps)

    t0 = time.monotonic()
    move(980, 330, 0.8)
    xtest.fake_input(d, X.ButtonPress, 1); xtest.fake_input(d, X.ButtonRelease, 1); d.sync()
    time.sleep(0.3)
    type_("Meanwhile, I keep working here.\n")
    move(1040, 420, 0.7)
    type_("My mouse and focus stay mine.\n")
    while time.monotonic() - t0 < stop_after:
        time.sleep(0.1)


def parse_tree(text: str) -> list[dict]:
    """ui_tree lines -> [{id, role, name, x, y}]"""
    import re
    out = []
    for m in re.finditer(r'\[(\d+)\] (.+?)(?: "(.*?)")? @\((\d+),(\d+)\)', text):
        out.append({"id": int(m[1]), "role": m[2], "name": m[3] or "", "x": int(m[4]), "y": int(m[5])})
    return out


async def agent_actions(rec: "Recorder") -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = {**os.environ, "LCU_CURSOR_LABEL": "Agent", "LCU_CURSOR_COLOR": "#4c7dff",
           "LCU_IME_BYPASS": "0"}
    p = StdioServerParameters(command="uv", args=["--directory", str(ROOT), "run", "lcu-supervised"], env=env)
    async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        call = s.call_tool
        await call("screen_info", {})                      # agent pointer + cursor appear
        await asyncio.sleep(1.2)
        els = parse_tree((await call("ui_tree", {"window": "agent.txt"})).content[0].text)
        editor = next(e for e in els if e["role"] == "text")
        await call("click_element", {"id": editor["id"]})  # glides there, clicks the editor
        await call("type_text", {"text": "Hello from the agent! 안녕하세요 ✓\n", "expect_window": "agent.txt"})
        await asyncio.sleep(0.6)
        await call("type_text", {"text": "Typing in its own window, with its own pointer.\n",
                                 "expect_window": "agent.txt"})
        await asyncio.sleep(0.8)
        els = parse_tree((await call("ui_tree", {"window": "agent.txt"})).content[0].text)
        menu = max((e for e in els if e["role"] == "toggle button"), key=lambda e: e["x"])
        await call("click_element", {"id": menu["id"]})    # opens the main menu
        await asyncio.sleep(1.4)
        await call("key", {"combo": "Escape", "expect_window": "agent.txt"})
        await call("mouse_move", {"x": 420, "y": 520})
        await asyncio.sleep(2.6)                           # idle: the cursor "thinks"
        rec.stop.set()                                     # stop before the server exits
        rec.join()
        print(json.dumps({"tools": len((await s.list_tools()).tools)}))


def main() -> None:
    if "--background" in sys.argv:
        background()
        return
    out_gif = ROOT / "docs" / "demo.gif"
    out_gif.parent.mkdir(exist_ok=True)
    place("agent.txt", 24, 60, 600, 560)
    place("you.txt", 656, 60, 600, 560)
    time.sleep(1.0)
    activate("you.txt")                                    # the user's focus is on the right
    time.sleep(0.8)

    frames = pathlib.Path(tempfile.mkdtemp(prefix="lcu-demo-frames-"))
    rec = Recorder(frames)
    rec.start()
    time.sleep(0.8)
    user = threading.Thread(target=user_actions, args=(9.0,), daemon=True)
    user.start()
    asyncio.run(agent_actions(rec))
    user.join()

    vf = (f"fps={FPS},scale=960:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                    "-i", str(frames / "%05d.png"), "-vf", vf, "-loop", "0", str(out_gif)], check=True)
    shutil.rmtree(frames)
    print(f"{rec.n} frames -> {out_gif} ({out_gif.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
