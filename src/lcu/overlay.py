"""On-screen cursor for the agent: a small click-through window that mirrors
the agent's pointer, drawn and animated like the Codex computer-use cursor.

Runs as its own process (GTK main loop), driven by JSON lines on stdin:
  {"pos": [x, y]}                          jump (hotspot at real pixel x, y)
  {"pos": [x, y], "tan": a, "pv": v, "dist": d, "dt": s}   glide frame
  {"pos": [x, y], "arrive": true}          end of a glide
  {"btn": "left", "down": true}            press
  {"label": "Claude"}                      optional name tag ("" hides it)
It quits when stdin closes, so it never outlives the MCP server.

Look and motion follow the Codex AgentCursor (glyph, centre hotspot, glassy
gradient, scoot stretch/rotation springs, press pulse; see motion.py). The
accent colour comes from the wallpaper and the cursor wiggles while the
agent is idle (thinking).

Env: LCU_CURSOR_COLOR=#rrggbb, LCU_CURSOR_SCALE=1.0 (14 px glyph),
     LCU_CURSOR_ICON=/path.png + LCU_CURSOR_HOTSPOT=x,y + LCU_CURSOR_SIZE=28
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

from lcu.motion import GLYPH, GLYPH_SIZE, MOTION, Spring, clamp, wrap_angle  # noqa: E402

SCALE = float(os.environ.get("LCU_CURSOR_SCALE", "1.0"))
PAD = 40                     # hotspot position inside the window
WIN_W, WIN_H = 200, 80
THINK_AFTER = 1.2            # s without commands -> "thinking" wiggle
STILL_AFTER = 180            # s idle -> stop wiggling
HIDE_AFTER = 900             # s idle -> fade out
FALLBACK_COLOR = (0.36, 0.55, 1.0)


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
        if not s.props.settings_schema.has_key(key):
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


def _palette(rgb):
    """start (light rim) / mid / end (deep) tones around the accent."""
    h, l, s = colorsys.rgb_to_hls(*rgb)
    start = colorsys.hls_to_rgb(h, min(0.72, l + 0.12), s)
    end = colorsys.hls_to_rgb(h, max(0.32, l - 0.12), s)
    return start, rgb, end


# ----------------------------------------------------------------- window

class Cursor(Gtk.Window):
    def __init__(self, label: str):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.label = label
        self.pal = _palette(accent_color())
        self.icon, self.hotspot = self._load_icon()
        self.pos = None
        self.last_cmd = time.monotonic()
        self.opacity = 0.0
        self.hidden = False
        self.click_t = None          # 0..1 while the press pulse runs
        self.move_dist = 0.0
        self.gliding = False
        self._last_raise = 0.0
        self._last_frame = time.monotonic()
        self._buf = b""
        m = MOTION
        self.axis = Spring(m["click_angle"], *m["scoot_axis"])
        self.base_rot = Spring(0.0, *m["scoot_base_rotation"])
        self.stretch_x = Spring(1.0, *m["scoot_stretch"])
        self.stretch_y = Spring(1.0, *m["scoot_stretch"])
        self.rot_off = Spring(0.0, *m["scoot_rotation"])

        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_default_size(WIN_W, WIN_H)
        self.set_size_request(WIN_W, WIN_H)
        self.set_accept_focus(False)
        self.connect("draw", self.on_draw)
        self.connect("realize", lambda _w: self.input_shape_combine_region(cairo.Region()))

        GLib.io_add_watch(GLib.IOChannel.unix_new(sys.stdin.fileno()),
                          GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP,
                          self.on_stdin)
        GLib.timeout_add(16, self.tick)

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
        return True

    def handle(self, msg: dict) -> bool:
        self.last_cmd = time.monotonic()
        self.hidden = False
        m = MOTION
        if "pos" in msg:
            x, y = msg["pos"]
            self.pos = (int(x), int(y))
            self.move(self.pos[0] - PAD, self.pos[1] - PAD)
            if not self.get_visible():
                self.show_all()
            self._raise()
            if "tan" in msg:  # a glide frame: same targets as the Codex engine
                self.gliding = True
                self.move_dist = msg.get("dist", 0.0)
                tan = msg["tan"]
                prev = getattr(self, "_prev", None)
                speed = 0.0
                if prev is not None and msg.get("dt"):
                    speed = math.hypot(x - prev[0], y - prev[1]) / msg["dt"]
                self._prev = (x, y)
                intensity = clamp(speed / 900, 0, 1) if self.move_dist >= m["scoot_distance"] else 0.0
                self.axis.set_angle_target(tan)
                self.base_rot.set_angle_target(clamp(wrap_angle(tan - m["click_angle"]),
                                                     -m["scoot_rotation_max"], m["scoot_rotation_max"]) * intensity)
                self.stretch_x.target = 1 + m["scoot_stretch_x"] * intensity
                self.stretch_y.target = 1 - m["scoot_squash_y"] * intensity
                self.rot_off.target = clamp(msg.get("pv", 0.0) * 0.035,
                                            -m["scoot_rotation_max"], m["scoot_rotation_max"]) * intensity
            else:
                self._settle_targets()
        if "btn" in msg and msg.get("down"):
            self.click_t = 0.0
        if "label" in msg:
            self.label = msg["label"] or ""
        self.queue_draw()
        return not msg.get("quit")

    def _settle_targets(self):
        self.gliding = False
        self._prev = None
        self.axis.set_angle_target(MOTION["click_angle"])
        self.base_rot.target = 0.0
        self.stretch_x.target = self.stretch_y.target = 1.0
        self.rot_off.target = 0.0

    def _raise(self):
        now = time.monotonic()
        if now - self._last_raise > 0.5 and self.get_window() is not None:
            self.get_window().raise_()
            self._last_raise = now

    # ------------------------------------------------------------ animation
    def tick(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last_frame)
        self._last_frame = now
        if self.pos is None:
            return True
        idle = now - self.last_cmd
        if self.gliding and idle > 0.15:  # glide stream ended without "arrive"
            self._settle_targets()
        if idle > HIDE_AFTER:
            self.hidden = True
        dirty = False
        target = 0.0 if self.hidden else 1.0
        if self.opacity != target:
            step = dt / 0.16
            self.opacity = min(target, self.opacity + step) if target > self.opacity else max(target, self.opacity - step)
            dirty = True
        elif self.hidden and self.get_visible():
            self.hide()
        for s in (self.axis, self.base_rot, self.stretch_x, self.stretch_y, self.rot_off):
            if not s.settled():
                s.step(dt)
                dirty = True
        if self.click_t is not None:
            self.click_t += dt * 4
            if self.click_t >= 1:
                self.click_t = None
            dirty = True
        if THINK_AFTER < idle < STILL_AFTER:
            dirty = True
            self._raise()
        if dirty:
            self.queue_draw()
        return True

    # -------------------------------------------------------------- drawing
    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.pos is None or self.opacity <= 0.01:
            return False
        now = time.monotonic()
        idle = now - self.last_cmd
        press = math.sin(math.pi * self.click_t) if self.click_t is not None else 0.0

        cr.save()
        cr.translate(PAD, PAD)
        cr.rotate(self.axis.value)
        cr.scale(self.stretch_x.value, self.stretch_y.value)
        cr.rotate(-self.axis.value)
        rot = self.base_rot.value + self.rot_off.value
        if THINK_AFTER < idle < STILL_AFTER:  # thinking: wiggle about the hotspot
            ph = (idle - THINK_AFTER) * (2 * math.pi * 1.4)
            ramp = min(1.0, (idle - THINK_AFTER) / 0.5)
            rot += math.radians(12) * math.sin(ph) * ramp
        cr.rotate(rot)
        s = SCALE * (1 - 0.1 * press)
        cr.scale(s, s)
        if self.icon is not None:
            Gdk.cairo_set_source_pixbuf(cr, self.icon, -self.hotspot[0], -self.hotspot[1])
            cr.paint_with_alpha(self.opacity)
        else:
            self._draw_glyph(cr, press)
        cr.restore()

        if self.label:
            self._draw_label(cr, PAD + GLYPH_SIZE * SCALE * 0.6, PAD + GLYPH_SIZE * SCALE * 0.55)
        return False

    def _glyph_path(self, cr):
        size = GLYPH_SIZE
        off = -size / 2  # hotspot = centre of the glyph box, as in Codex
        P = lambda p: (off + p[0] * size, off + p[1] * size)  # noqa: E731
        cr.new_path()
        for seg in GLYPH:
            if seg[0] == "move":
                cr.move_to(*P(seg[1]))
            elif seg[0] == "line":
                cr.line_to(*P(seg[1]))
            elif seg[0] == "curve":
                cr.curve_to(*P(seg[1]), *P(seg[2]), *P(seg[3]))
            else:
                cr.close_path()

    def _draw_glyph(self, cr, press: float):
        start, mid, end = self.pal
        a = self.opacity
        off = -GLYPH_SIZE / 2
        # glow under the glyph (stands in for a 9px canvas shadow blur)
        blur = 9 + press * 3
        glow_a = (0.38 + press * 0.12) * a
        cr.save()
        cr.translate(0, 1)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for i in range(4, 0, -1):  # nested strokes approximate a soft falloff
            self._glyph_path(cr)
            cr.set_line_width(blur * i / 4)
            cr.set_source_rgba(*end, glow_a * 0.11)
            cr.stroke()
        cr.restore()
        # glassy gradient fill
        self._glyph_path(cr)
        grad = cairo.LinearGradient(off, off, -off, -off)
        grad.add_color_stop_rgba(0, *start, (0.34 + press * 0.08) * a)
        grad.add_color_stop_rgba(0.55, *mid, (0.30 + press * 0.08) * a)
        grad.add_color_stop_rgba(1, *end, (0.26 + press * 0.08) * a)
        cr.set_source(grad)
        cr.fill_preserve()
        # crisp rim
        cr.set_source_rgba(*start, (0.94 + press * 0.06) * a)
        cr.set_line_width(1.55)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.stroke()

    def _draw_label(self, cr, x, y):
        r, g, b = self.pal[1]
        a = self.opacity
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
