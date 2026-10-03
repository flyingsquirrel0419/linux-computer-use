"""Codex-style agent cursor: glyph geometry and motion model.

Ported to Python from maka-agent's cursor engine
(apps/desktop/src/renderer/computer-use-overlay/engine/cursor-engine.ts,
commit 5551d6c, https://github.com/maka-agent/maka-agent, Apache-2.0).
That project recovered the constants from the AgentCursor in the Codex
desktop app's SkyComputerUseService. See NOTICE.
"""

import math

TAU = 2 * math.pi

MOTION = {
    "click_angle": math.radians(-44),
    "candidate_count": 20,
    "bounds_margin": 20,
    "start_handle": 0.41960295031576633,
    "endpoint_handle": 0.15,
    "arc_size": 0.27655231880642772,
    "arc_flow": 0.5783555327868779,
    "straight_path_distance": 10,
    "spring_response_scaler": 0.9,
    "spring_response_min": 0.12,
    "spring_response_max": 2.2,
    "spring_damping": 0.9,
    "scoot_distance": 196,
    "scoot_axis": (0.07, 0.82),            # (response, damping fraction)
    "scoot_base_rotation": (0.09, 0.86),
    "scoot_stretch": (0.095, 0.72),
    "scoot_rotation": (0.055, 0.76),
    "scoot_stretch_x": 0.38,
    "scoot_squash_y": 0.18,
    "scoot_rotation_max": math.radians(76),
    "terminal_tangent_blend": 0.99,
}

# Normalized AgentCursor outline (unit square, drawn centred on the hotspot).
GLYPH_SIZE = 14
GLYPH = [
    ("move", (0.00599, 0.15864)),
    ("curve", (-0.02364, 0.06456), (0.06169, -0.02474), (0.15158, 0.00627)),
    ("line", (0.87634, 0.25652)),
    ("curve", (0.97594, 0.29096), (0.9834, 0.43547), (0.88794, 0.48095)),
    ("line", (0.59343, 0.62108)),
    ("line", (0.45955, 0.92925)),
    ("curve", (0.41611, 1.02925), (0.27801, 1.02146), (0.2451, 0.91717)),
    ("close",),
]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def wrap_angle(a: float) -> float:
    while a > math.pi:
        a -= TAU
    while a < -math.pi:
        a += TAU
    return a


class Spring:
    """Damped spring parameterised like SwiftUI: response (s) + damping fraction."""

    def __init__(self, value: float, response: float, damping: float):
        self.value = self.target = value
        self.velocity = 0.0
        self.response, self.damping = response, damping

    def step(self, dt: float) -> None:
        omega = TAU / max(0.001, self.response)
        k, c = omega * omega, 2 * self.damping * omega
        steps = max(1, math.ceil(dt * 240))
        h = dt / steps
        for _ in range(steps):
            self.velocity += (k * (self.target - self.value) - c * self.velocity) * h
            self.value += self.velocity * h

    def settled(self, eps: float = 0.001, veps: float = 0.01) -> bool:
        return abs(self.target - self.value) <= eps and abs(self.velocity) <= veps

    def set_angle_target(self, angle: float) -> None:
        self.target = self.value + wrap_angle(angle - self.value)


class CubicPath:
    def __init__(self, p0, p1, p2, p3):
        self.p = (p0, p1, p2, p3)

    def sample(self, t: float):
        t = clamp(t, 0, 1)
        u = 1 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        p0, p1, p2, p3 = self.p
        return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])

    def tangent(self, t: float):
        t = clamp(t, 0, 1)
        u = 1 - t
        p0, p1, p2, p3 = self.p
        return (3 * u * u * (p1[0] - p0[0]) + 6 * u * t * (p2[0] - p1[0]) + 3 * t * t * (p3[0] - p2[0]),
                3 * u * u * (p1[1] - p0[1]) + 6 * u * t * (p2[1] - p1[1]) + 3 * t * t * (p3[1] - p2[1]))


def _direct(start, end) -> CubicPath:
    dx, dy = end[0] - start[0], end[1] - start[1]
    return CubicPath(start, (start[0] + dx / 3, start[1] + dy / 3),
                     (start[0] + dx * 2 / 3, start[1] + dy * 2 / 3), end)


def _overflow(path: CubicPath, start, end, size) -> float:
    if not size:
        return 0.0
    w, h = size
    m = MOTION["bounds_margin"]
    min_x = max(0, min(m, start[0], end[0]))
    min_y = max(0, min(m, start[1], end[1]))
    max_x = min(w, max(w - m, start[0], end[0]))
    max_y = min(h, max(h - m, start[1], end[1]))
    total = 0.0
    for i in range(33):
        x, y = path.sample(i / 32)
        total += max(0, min_x - x) ** 2 + max(0, x - max_x) ** 2
        total += max(0, min_y - y) ** 2 + max(0, y - max_y) ** 2
    return total


def plan_path(start, end, departure: float = MOTION["click_angle"], size=None) -> CubicPath:
    """Pick the arc among 20 candidates that best matches the desired bend
    while staying on screen."""
    c = MOTION
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = math.hypot(dx, dy)
    if dist <= c["straight_path_distance"]:
        return _direct(start, end)
    ang = math.atan2(dy, dx)
    direction = (math.cos(ang), math.sin(ang))
    dep = (math.cos(departure), math.sin(departure))
    perp = (-direction[1], direction[0])
    sign = 1 if math.sin(wrap_angle(ang - departure)) >= 0 else -1
    desired = min(dist * c["arc_size"], 120) * sign
    best, best_score, best_over = None, math.inf, math.inf
    n = c["candidate_count"]
    for i in range(n):
        arc = (i / (n - 1) * 2 - 1) * abs(desired)
        p1 = (start[0] + dep[0] * dist * c["start_handle"] + perp[0] * arc * c["arc_flow"],
              start[1] + dep[1] * dist * c["start_handle"] + perp[1] * arc * c["arc_flow"])
        p2 = (end[0] - direction[0] * dist * c["endpoint_handle"] + perp[0] * arc * (1 - c["arc_flow"]),
              end[1] - direction[1] * dist * c["endpoint_handle"] + perp[1] * arc * (1 - c["arc_flow"]))
        cand = CubicPath(start, p1, p2, end)
        control_len = (math.hypot(p1[0] - start[0], p1[1] - start[1])
                       + math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                       + math.hypot(end[0] - p2[0], end[1] - p2[1]))
        over = _overflow(cand, start, end, size)
        score = over * 1_000_000 + abs(arc - desired) * 0.8 + control_len * 0.2
        if score < best_score:
            best, best_score, best_over = cand, score, over
    return _direct(start, end) if best_over > 0.0001 else best


def move_response(dist: float) -> float:
    c = MOTION
    return clamp(dist / 1000 * c["spring_response_scaler"], c["spring_response_min"], c["spring_response_max"])


def trajectory(start, end, size=None, fps: int = 120):
    """Yield (x, y, tangent_angle, progress_velocity, dt) frames for a move."""
    path = plan_path(start, end, size=size)
    progress = Spring(0.0, move_response(math.hypot(end[0] - start[0], end[1] - start[1])),
                      MOTION["spring_damping"])
    progress.target = 1.0
    dt = 1 / fps
    blend0 = MOTION["terminal_tangent_blend"]
    for _ in range(int(fps * 4)):  # hard cap: 4 s
        progress.step(dt)
        p = clamp(progress.value, 0, 1)
        x, y = path.sample(p)
        tx, ty = path.tangent(p)
        tan = math.atan2(ty, tx)
        if p > blend0:
            tan += wrap_angle(MOTION["click_angle"] - tan) * clamp((p - blend0) / (1 - blend0), 0, 1)
        yield x, y, tan, progress.velocity, dt
        if progress.settled(0.0005, 0.005):
            break
