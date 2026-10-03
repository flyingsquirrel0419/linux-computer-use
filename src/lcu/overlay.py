"""On-screen cursor for the agent: a small click-through window that follows
the agent's pointer.

Runs as its own process (GTK main loop) and is driven by JSON lines on stdin:
  {"pos": [x, y]}                 move the tip to real screen pixel (x, y)
  {"btn": "left", "down": true}   press feedback / ripple on release
  {"label": "Claude"}             name tag ("" hides it)
It quits when stdin closes, so it never outlives the MCP server.

Behaviour: glides with the pointer, wiggles while the agent is idle
(thinking), pulses on clicks, takes its accent colour from the wallpaper, and
fades out after a long idle period.

Customise with env vars:
  LCU_CURSOR_COLOR=#rrggbb    fixed accent colour instead of the wallpaper's
  LCU_CURSOR_ICON=/path.png   your own icon (PNG/SVG)
  LCU_CURSOR_HOTSPOT=x,y      tip position inside that icon (icon pixels)
  LCU_CURSOR_SIZE=28          icon height in px (custom icons)
  LCU_CURSOR_SCALE=1.35       size of the built-in arrow
"""

import colorsys
import json
import math
import os
import sys
import time
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

SCALE = float(os.environ.get("LCU_CURSOR_SCALE", "1.35"))
PAD = 34                     # room around the tip for ripple and wiggle
WIN_W, WIN_H = 240, 100
THINK_AFTER = 0.9            # s without commands -> "thinking" wiggle
STILL_AFTER = 180            # s idle -> stop wiggling
HIDE_AFTER = 900             # s idle -> fade out
FALLBACK_COLOR = (0.30, 0.43, 1.0)

# Built-in pointer, tip at (0, 0): a rounded "multiplayer" arrow.
ARROW = [(0, 0), (5.6, 17.2), (8.6, 9.4), (16.6, 7.0)]


# ------------------------------------------------------------------ colour

def _wallpaper_path() -> str | None:
    src = Gio.SettingsSchemaSource.get_default()
    candidates = [
        ("org.cinnamon.desktop.background", "picture-uri"),
        ("org.gnome.desktop.background", "picture-uri-dark"),
        ("org.gnome.desktop.background", "picture-uri"),
        ("org.mate.background", "picture-filename"),
    ]
    for schema, key in candidates:
        if src is None or src.lookup(schema, True) is None:
            continue
        s = Gio.Settings.new(schema)
        if key not in s.list_keys():
            continue
        val = s.get_string(key)
        if not val:
            continue
        path = unquote(urlparse(val).path) if "://" in val else val
        if os.path.exists(path):
            return path
    return None


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def accent_color():
    if os.environ.get("LCU_CURSOR_COLOR"):
        try:
            return _hex(os.environ["LCU_CURSOR_COLOR"])
        except Exception:
            pass
    path = _wallpaper_path()
    if not path:
        return FALLBACK_COLOR
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 64, 64, True)
    except Exception:
        return FALLBACK_COLOR
    data, n, stride = pb.get_pixels(), pb.get_n_channels(), pb.get_rowstride()
    bins = [[0.0, 0.0, 0.0, 0.0] for _ in range(12)]  # weight, r, g, b
    for y in range(pb.get_height()):
        for x in range(pb.get_width()):
            i = y * stride + x * n
            r, g, b = data[i] / 255, data[i + 1] / 255, data[i + 2] / 255
            h, s, v = colorsys.rgb_to_hsv(r, g, b)
            if s < 0.22 or v < 0.18:
                continue
            w = s * v
            k = int(h * 12) % 12
            bins[k][0] += w
            bins[k][1] += r * w
            bins[k][2] += g * w
            bins[k][3] += b * w
    best = max(bins, key=lambda b: b[0])
    if best[0] < 1.0:
        return FALLBACK_COLOR
    r, g, b = (best[1] / best[0], best[2] / best[0], best[3] / best[0])
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    # vivid and mid-bright so it reads on both light and dark backgrounds
    return colorsys.hsv_to_rgb(h, max(s, 0.68), 0.88)


# ----------------------------------------------------------------- window

class Cursor(Gtk.Window):
    def __init__(self, label: str):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.label = label
        self.color = accent_color()
        self.icon, self.hotspot = self._load_icon()
        self.pos = None
        self.last_cmd = time.monotonic()
        self.pressed = False
        self.ripples: list[float] = []  # start times
        self.alpha = 0.0
        self.visible_target = 1.0
        self._last_raise = 0.0
        self._buf = b""

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_default_size(WIN_W, WIN_H)
        self.set_size_request(WIN_W, WIN_H)
        self.set_accept_focus(False)
        self.connect("draw", self.on_draw)
        self.connect("realize", self.on_realize)

        GLib.io_add_watch(GLib.IOChannel.unix_new(sys.stdin.fileno()),
                          GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP,
                          self.on_stdin)
        GLib.timeout_add(33, self.tick)

    def _load_icon(self):
        path = os.environ.get("LCU_CURSOR_ICON")
        if not path:
            return None, (0, 0)
        try:
            size = int(os.environ.get("LCU_CURSOR_SIZE", "28"))
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
            f = size / pb.get_height()
            pb = pb.scale_simple(max(1, round(pb.get_width() * f)), size, GdkPixbuf.InterpType.BILINEAR)
            hx, hy = (float(v) for v in os.environ.get("LCU_CURSOR_HOTSPOT", "0,0").split(","))
            return pb, (hx * f, hy * f)
        except Exception as e:
            print(f"lcu-overlay: cannot load icon {path}: {e}", file=sys.stderr)
            return None, (0, 0)

    def on_realize(self, _w):
        # click-through: an empty input shape lets every event fall through
        self.input_shape_combine_region(cairo.Region())

    # -------------------------------------------------------------- input
    def on_stdin(self, channel, cond):
        # raw fd reads: a buffered readline() could strand lines in Python's
        # buffer while the fd itself looks idle
        try:
            data = os.read(sys.stdin.fileno(), 65536)
        except OSError:
            data = b""
        if not data:
            Gtk.main_quit()
            return False
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not self.handle(msg):
                Gtk.main_quit()
                return False
        self.queue_draw()
        return True

    def handle(self, msg: dict) -> bool:
        self.last_cmd = time.monotonic()
        self.visible_target = 1.0
        if "pos" in msg:
            x, y = msg["pos"]
            self.pos = (int(x), int(y))
            self.move(self.pos[0] - PAD, self.pos[1] - PAD)
            if not self.get_visible():
                self.show_all()
            self._raise()
        if "btn" in msg:
            self.pressed = bool(msg.get("down"))
            if not self.pressed:
                self.ripples.append(time.monotonic())
        if "label" in msg:
            self.label = msg["label"] or ""
        return not msg.get("quit")

    def _raise(self):
        now = time.monotonic()
        if now - self._last_raise > 0.5 and self.get_window() is not None:
            self.get_window().raise_()
            self._last_raise = now

    # ------------------------------------------------------------ animation
    def tick(self):
        if self.pos is None:
            return True
        idle = time.monotonic() - self.last_cmd
        if idle > HIDE_AFTER:
            self.visible_target = 0.0
        target = self.visible_target
        if abs(self.alpha - target) > 0.01:
            self.alpha += (target - self.alpha) * 0.25
            self.queue_draw()
        elif self.alpha == 0.0 and self.get_visible() and target == 0.0:
            self.hide()
        animating = bool(self.ripples) or THINK_AFTER < idle < STILL_AFTER
        if animating:
            self._raise()
            self.queue_draw()
        return True

    # -------------------------------------------------------------- drawing
    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.pos is None or self.alpha <= 0.01:
            return False
        now = time.monotonic()
        idle = now - self.last_cmd
        r, g, b = self.color

        # click ripples around the tip
        alive = []
        for t0 in self.ripples:
            p = (now - t0) / 0.45
            if p >= 1:
                continue
            alive.append(t0)
            cr.save()
            cr.arc(PAD, PAD, 5 + 24 * p, 0, 2 * math.pi)
            cr.set_source_rgba(r, g, b, 0.6 * (1 - p) * self.alpha)
            cr.set_line_width(2.5)
            cr.stroke()
            cr.restore()
        self.ripples = alive

        cr.save()
        cr.translate(PAD, PAD)
        if THINK_AFTER < idle < STILL_AFTER:  # thinking: wiggle around the tip
            ph = (idle - THINK_AFTER) * 2 * math.pi * 1.4
            ramp = min(1.0, (idle - THINK_AFTER) / 0.4)
            cr.rotate(math.radians(11) * math.sin(ph) * ramp)
            cr.translate(0, 1.5 * math.sin(ph * 2) * ramp)
        s = SCALE * (0.86 if self.pressed else 1.0)
        cr.scale(s, s)
        if self.icon is not None:
            Gdk.cairo_set_source_pixbuf(cr, self.icon, -self.hotspot[0], -self.hotspot[1])
            cr.paint_with_alpha(self.alpha)
            tip_w, tip_h = self.icon.get_width() - self.hotspot[0], self.icon.get_height() - self.hotspot[1]
        else:
            self._draw_arrow(cr)
            tip_w, tip_h = 16.6, 17.2
        cr.restore()

        if self.label:
            self._draw_label(cr, PAD + tip_w * SCALE * 0.72, PAD + tip_h * SCALE * 0.92)
        return False

    def _arrow_path(self, cr):
        pts = ARROW
        # rounded corners: quadratic-ish via short line + curve at each vertex
        n = len(pts)
        rad = 1.6
        for i in range(n):
            p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
            d1 = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            d2 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            a = (p1[0] - (p1[0] - p0[0]) * rad / d1, p1[1] - (p1[1] - p0[1]) * rad / d1)
            c = (p1[0] + (p2[0] - p1[0]) * rad / d2, p1[1] + (p2[1] - p1[1]) * rad / d2)
            if i == 0:
                cr.move_to(*a)
            else:
                cr.line_to(*a)
            cr.curve_to(p1[0], p1[1], p1[0], p1[1], *c)
        cr.close_path()

    def _draw_arrow(self, cr):
        r, g, b = self.color
        a = self.alpha
        # soft shadow
        cr.save()
        cr.translate(0.8, 1.6)
        self._arrow_path(cr)
        cr.set_source_rgba(0, 0, 0, 0.28 * a)
        cr.set_line_width(4)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke_preserve()
        cr.fill()
        cr.restore()
        # white outline + accent fill
        self._arrow_path(cr)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(1, 1, 1, a)
        cr.set_line_width(2.6)
        cr.stroke_preserve()
        grad = cairo.LinearGradient(0, 0, 14, 16)
        grad.add_color_stop_rgba(0, min(1, r * 1.15 + 0.08), min(1, g * 1.15 + 0.08), min(1, b * 1.15 + 0.08), a)
        grad.add_color_stop_rgba(1, r * 0.85, g * 0.85, b * 0.85, a)
        cr.set_source(grad)
        cr.fill()

    def _draw_label(self, cr, x, y):
        r, g, b = self.color
        a = self.alpha
        cr.save()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11.5)
        ext = cr.text_extents(self.label)
        w, h = ext.x_advance + 14, 20
        rad = h / 2
        cr.new_sub_path()
        cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, math.pi / 2)
        cr.arc(x + rad, y + rad, rad, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(r * 0.9, g * 0.9, b * 0.9, 0.95 * a)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.9 * a)
        cr.set_line_width(1.2)
        cr.stroke()
        cr.move_to(x + 7, y + h / 2 - (ext.y_bearing + ext.height / 2))
        cr.set_source_rgba(1, 1, 1, a)
        cr.show_text(self.label)
        cr.restore()


def main():
    label = ""
    if "--label" in sys.argv:
        label = sys.argv[sys.argv.index("--label") + 1]
    GLib.set_prgname("lcu-overlay")
    win = Cursor(label)
    win.realize()
    Gtk.main()
    del win


if __name__ == "__main__":
    main()
