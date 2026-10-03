"""Record docs/demo.gif on a throwaway X display (run via scripts/record_demo.sh).

A multi-app task, done by the agent through the real MCP tools only:
  1. open the file manager (nemo) from the terminal
  2. create a `notes` folder there
  3. open README.md in the text editor (gedit), add a section, save
  4. back in the terminal: add a file to notes/, then git status/commit/log

New windows are tiled as they appear so the terminal output stays visible
(agent clicks don't raise windows). Step captions are burned into the frames.

Modes:
  --background   show a desktop-type gradient window (the wallpaper)
  (default)      run the scenario and write the GIF
"""

import asyncio
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
W, H = 1280, 720
FPS = 12

# where each app's window goes: (x, y, width, height)
LAYOUT = {
    "nemo": (12, 12, 616, 330),
    "gedit": (12, 372, 616, 336),
    "gnome-terminal": (652, 12, 616, 696),
}


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


# ------------------------------------------------------------- tiling + rec

class Tiler(threading.Thread):
    """Moves each app window to its LAYOUT slot as soon as it is mapped."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()

    def run(self):
        from Xlib import X, display, protocol
        d = display.Display()
        root = d.screen().root
        placed = set()
        while not self.stop.is_set():
            prop = root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), 0)
            for wid in (prop.value if prop else []):
                if wid in placed:
                    continue
                win = d.create_resource_object("window", wid)
                try:
                    cls = " ".join(win.get_wm_class() or ()).lower()
                except Exception:
                    continue
                slot = next((v for k, v in LAYOUT.items() if k in cls), None)
                if slot is None:
                    continue
                x, y, w, h = slot
                ev = protocol.event.ClientMessage(
                    window=win, client_type=d.intern_atom("_NET_MOVERESIZE_WINDOW"),
                    data=(32, [(1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | 10, x, y, w, h]))
                root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
                d.sync()
                placed.add(wid)
            time.sleep(0.03)


class Recorder(threading.Thread):
    def __init__(self, out_dir: pathlib.Path):
        super().__init__(daemon=True)
        self.out_dir, self.stop = out_dir, threading.Event()
        self.n = 0
        self.caption = ""

    def run(self):
        import mss
        from PIL import Image, ImageDraw, ImageFont
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
        with mss.MSS() as sct:
            nxt = time.monotonic()
            while not self.stop.is_set():
                raw = sct.grab({"left": 0, "top": 0, "width": W, "height": H})
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                if self.caption:
                    d = ImageDraw.Draw(img, "RGBA")
                    l, t, r, b = d.textbbox((0, 0), self.caption, font=font)
                    tw, th = r - l, b - t
                    x0, y0 = (W - tw) // 2 - 22, H - th - 46
                    d.rounded_rectangle([x0, y0, x0 + tw + 44, y0 + th + 26], radius=(th + 26) // 2,
                                        fill=(16, 22, 40, 225), outline=(90, 130, 255, 255), width=2)
                    d.text((x0 + 22 - l, y0 + 13 - t), self.caption, font=font, fill=(255, 255, 255))
                img.save(self.out_dir / f"{self.n:05d}.png", compress_level=1)
                self.n += 1
                nxt += 1 / FPS
                time.sleep(max(0, nxt - time.monotonic()))


# ------------------------------------------------------------------- agent

def parse_tree(text: str) -> list[dict]:
    """ui_tree lines -> [{id, role, name, x, y, window}]"""
    out, window = [], ""
    for line in text.splitlines():
        if line.startswith("== "):
            window = line
            continue
        m = re.search(r'\[(\d+)\] (.+?)(?: "(.*?)")? @\((\d+),(\d+)\)', line)
        if m:
            out.append({"id": int(m[1]), "role": m[2], "name": m[3] or "", "x": int(m[4]),
                        "y": int(m[5]), "window": window})
    return out


async def agent_actions(rec: Recorder) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = {**os.environ, "LCU_CURSOR_LABEL": "Agent", "LCU_CURSOR_COLOR": "#4c7dff",
           "LCU_IME_BYPASS": "0"}
    p = StdioServerParameters(command="uv", args=["--directory", str(ROOT), "run", "lcu-supervised"], env=env)
    async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
        await s.initialize()

        async def call(name, **args):
            res = await s.call_tool(name, args)
            text = res.content[0].text if res.content and res.content[0].type == "text" else ""
            if res.isError or text.startswith("NOT SENT"):
                raise RuntimeError(f"{name}{args}: {text}")
            return text

        async def find(app, role, name=None, window=None, tries=20):
            for _ in range(tries):
                els = parse_tree(await call("ui_tree", app=app, only_interactive=False))
                hits = [e for e in els if e["role"] == role and (name is None or e["name"] == name)
                        and (window is None or window in e["window"])]
                if hits:
                    return hits[0]
                await asyncio.sleep(0.4)
            raise RuntimeError(f"{app}: no {role} {name!r}")

        async def shell(cmd, pause=1.2):
            await call("type_text", text=cmd + "\n", expect_window="terminal")
            await asyncio.sleep(pause)

        await call("screen_info")                       # the agent's pointer appears
        await asyncio.sleep(1.0)

        rec.caption = "1/4  Open the file manager from the terminal"
        term = await find("gnome-terminal", "terminal")
        await call("click_element", id=term["id"])
        await shell("nemo . 2>/dev/null &", pause=3.0)

        rec.caption = "2/4  Create a folder in the file manager"
        view = await find("nemo", "layered pane", "Icon View")
        await call("click_element", id=view["id"], method="mouse")
        await call("key", combo="ctrl+shift+n", expect_window="nemo")
        await asyncio.sleep(1.0)
        await call("type_text", text="notes\n", expect_window="nemo")
        await asyncio.sleep(1.2)
        await call("key", combo="ctrl+2", expect_window="nemo")   # list view: files become readable
        await asyncio.sleep(1.2)

        rec.caption = "3/4  Open README.md in the text editor, edit and save"
        readme = await find("nemo", "table cell", "README.md")
        await call("click", x=readme["x"], y=readme["y"], count=2)
        editor = await find("gedit", "text", window="README.md")
        await asyncio.sleep(0.8)
        await call("click_element", id=editor["id"])
        await call("key", combo="ctrl+End", expect_window="gedit")
        await call("type_text", text="\n\n## Notes\n\nOrganised in the file manager, edited here,\n"
                                     "and committed from the terminal by an AI agent.",
                   expect_window="gedit")
        await asyncio.sleep(0.8)
        await call("key", combo="ctrl+s", expect_window="gedit")
        await asyncio.sleep(1.5)

        rec.caption = "4/4  Commit the changes with git in the terminal"
        term = await find("gnome-terminal", "terminal")   # ids change on every ui_tree call
        await call("click_element", id=term["id"])
        await shell("echo '# Ideas' > notes/ideas.md")
        await shell("git status --short", pause=1.8)
        await shell("git add -A && git commit -qm 'Add notes and a README section'")
        await shell("git log --oneline --stat", pause=3.5)

        rec.caption = ""
        await call("mouse_move", x=900, y=640)
        await asyncio.sleep(2.0)                         # idle: the cursor "thinks"
        rec.stop.set()                                   # stop before the server exits
        rec.join()


def main() -> None:
    if "--background" in sys.argv:
        background()
        return
    out_gif = ROOT / "docs" / "demo.gif"
    out_gif.parent.mkdir(exist_ok=True)

    tiler = Tiler()
    tiler.start()
    time.sleep(1.5)                                      # terminal gets its slot
    frames = pathlib.Path(tempfile.mkdtemp(prefix="lcu-demo-frames-"))
    rec = Recorder(frames)
    rec.start()
    time.sleep(0.6)
    asyncio.run(agent_actions(rec))
    tiler.stop.set()

    vf = (f"fps={FPS},scale=960:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                    "-i", str(frames / "%05d.png"), "-vf", vf, "-loop", "0", str(out_gif)], check=True)
    shutil.rmtree(frames)
    print(f"{rec.n} frames -> {out_gif} ({out_gif.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
