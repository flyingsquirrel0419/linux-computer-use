"""Screen capture: grab the whole X screen, draw the pointer, downscale."""

import io

import mss
from PIL import Image, ImageDraw

from . import cursor, display


def _pointer_real() -> tuple[int, int]:
    with display.lock():
        p = display.get_display().screen().root.query_pointer()
        return p.root_x, p.root_y


def grab(region: tuple[int, int, int, int] | None = None, draw_cursor: bool = True) -> bytes:
    """Return PNG bytes in screenshot space.

    region: optional (x, y, w, h) in screenshot space to crop/zoom. A cropped
    region is returned at up to MAX_LONG_EDGE so small UI can be read, but
    coordinates the agent uses must still be full-screen screenshot coordinates.
    """
    with mss.mss() as sct:
        mon = sct.monitors[0]  # the full virtual screen == X root window
        raw = sct.grab(mon)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    if draw_cursor and not cursor.running():  # the overlay is already on screen
        # mss does not include the pointer; draw a small marker so the model
        # can see where the mouse is.
        cx, cy = _pointer_real()
        d = ImageDraw.Draw(img)
        r = max(6, img.width // 250)
        d.line([(cx - r, cy), (cx + r, cy)], fill=(255, 0, 0), width=2)
        d.line([(cx, cy - r), (cx, cy + r)], fill=(255, 0, 0), width=2)

    if region is not None:
        x, y, w, h = region
        x0, y0 = display.to_real(x, y)
        x1, y1 = display.to_real(x + w, y + h)
        img = img.crop((x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)))
        long_edge = max(img.size)
        if long_edge > display.MAX_LONG_EDGE or long_edge < display.MAX_LONG_EDGE // 2:
            f = display.MAX_LONG_EDGE / long_edge
            img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)
    else:
        size = display.shot_size()
        if size != img.size:
            img = img.resize(size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
