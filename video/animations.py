"""Easing curves, phone motions and the tap indicator. A motion maps scene progress to a Pose."""
import math
from dataclasses import dataclass, fields

from PIL import Image, ImageDraw

LANCZOS = Image.Resampling.LANCZOS


def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def smooth(x):  # smootherstep: zero velocity and acceleration at both ends
    x = clamp01(x)
    return x * x * x * (x * (x * 6 - 15) + 10)


def ease_out(x):
    return 1 - (1 - clamp01(x)) ** 3


def ease_in(x):
    return clamp01(x) ** 3


def ease_in_out(x):
    x = clamp01(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_back(x, c1=1.15):  # tiny overshoot, then settles: the "spring" feel
    x = clamp01(x)
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_out_bounce(x):  # falls, hits the floor, bounces twice, settles
    x = clamp01(x)
    n, d = 7.5625, 2.75
    if x < 1 / d:
        return n * x * x
    if x < 2 / d:
        x -= 1.5 / d
        return n * x * x + 0.75
    if x < 2.5 / d:
        x -= 2.25 / d
        return n * x * x + 0.9375
    x -= 2.625 / d
    return n * x * x + 0.984375


@dataclass
class Pose:
    """Phone transform. x/y are % of canvas width/height, rot in degrees (counter-clockwise)."""
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    rot: float = 0.0
    alpha: float = 1.0

    def combine(self, o):
        return Pose(self.x + o.x, self.y + o.y, self.scale * o.scale, self.rot + o.rot, self.alpha * o.alpha)

    def lerp(self, o, t):
        return Pose(*(getattr(self, f.name) + (getattr(o, f.name) - getattr(self, f.name)) * t for f in fields(self)))


def _sin(t, period, phase=0.0):
    return math.sin(2 * math.pi * t / period + phase)


# each motion: (u = progress 0..1, t = seconds into the scene, d = scene duration) -> Pose
MOTIONS = {
    "none":        lambda u, t, d: Pose(),
    "push_in":     lambda u, t, d: Pose(scale=1 + 0.06 * ease_in_out(u)),
    "pull_out":    lambda u, t, d: Pose(scale=1.07 - 0.07 * ease_in_out(u)),
    "camera_push": lambda u, t, d: Pose(scale=1 + 0.09 * ease_in_out(u), y=-1.6 * ease_in_out(u)),
    "float":       lambda u, t, d: Pose(y=1.0 * _sin(t, 3.4), x=0.35 * _sin(t, 5.1, 1.0), rot=0.6 * _sin(t, 4.3, 0.8)),
    "rise":        lambda u, t, d: Pose(y=6 * (1 - ease_out(t / 1.3)), scale=0.95 + 0.05 * ease_out(t / 1.3)),
    "tilt":        lambda u, t, d: Pose(rot=-5 * (1 - ease_out(t / 1.5)), x=-2 * (1 - ease_out(t / 1.5))),
    "parallax":    lambda u, t, d: Pose(x=-1.8 + 3.6 * ease_in_out(u), rot=-1.2 + 2.4 * ease_in_out(u)),
    "outro":       lambda u, t, d: Pose(scale=1 - 0.07 * ease_in_out(t / 1.6), y=2.5 * ease_in_out(t / 1.6)
                                        + 0.8 * _sin(t, 3.6)),
    # --- entrance motions: play during the first ~1 s of the scene, then hold still (combine with float/orbit) ---
    "drop":        lambda u, t, d: Pose(y=-14 * (1 - ease_out_bounce(t / 1.1)), rot=2.5 * (1 - ease_out(t / 1.1))),
    "swing":       lambda u, t, d: Pose(rot=14 * (1 - ease_out_back(t / 1.2, 1.7)), x=-6 * (1 - ease_out(t / 1.2))),
    "spring_pop":  lambda u, t, d: Pose(scale=0.78 + 0.22 * ease_out_back(t / 0.9, 1.9)),
    "punch":       lambda u, t, d: Pose(scale=1 + 0.12 * (1 - ease_out_back(t / 0.8, 1.5))),
    "slide_up":    lambda u, t, d: Pose(y=10 * (1 - ease_out_back(t / 1.1, 1.3)), scale=0.94 + 0.06 * ease_out(t / 1.1)),
    # --- continuous motions: keep moving for the whole scene ---
    "orbit":       lambda u, t, d: Pose(x=1.4 * _sin(t, 4.0), y=0.9 * _sin(t, 2.0), rot=1.2 * _sin(t, 4.0, 1.2)),
    "showcase":    lambda u, t, d: Pose(rot=-4 + 8 * ease_in_out(u), x=-2 + 4 * ease_in_out(u),
                                        scale=1 + 0.05 * ease_in_out(u)),
}

# taps are no longer part of a motion: add them per scene with the "Add tap" button
MOTION_PRESETS = ["none", "push_in", "pull_out", "camera_push", "float", "rise", "tilt", "parallax",
                  "push_in+float", "pull_out+float", "camera_push+float", "rise+float", "tilt+float",
                  "drop", "drop+float", "swing", "swing+float", "spring_pop", "spring_pop+float",
                  "punch", "punch+orbit", "slide_up", "slide_up+float", "orbit", "showcase"]


def motion_pose(spec, u, t, d):
    """`spec` may combine motions with '+', e.g. 'push_in+float'."""
    pose = Pose()
    for name in str(spec).split("+"):
        fn = MOTIONS.get(name.strip())
        if fn:
            pose = pose.combine(fn(u, t, d))
    return pose


def intro_pose(t):
    e = ease_out(t / 1.2)
    return Pose(alpha=smooth(t / 0.7), scale=0.92 + 0.08 * e, y=5 * (1 - e))


def touch_patch(radius, p):
    """White tap indicator (dot + expanding ring) as an RGBA patch of 4r x 4r, p in 0..1."""
    S = 3
    R = max(2, int(radius * S))
    size = 4 * R
    appear, press = smooth(p / 0.2), smooth((p - 0.2) / 0.15)
    ring = ease_out(clamp01((p - 0.3) / 0.5))
    a = appear * (1 - smooth((p - 0.75) / 0.25))
    if a <= 0.01:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d, c = ImageDraw.Draw(img), size / 2
    r = R * 0.55 * (1.3 - 0.3 * appear - 0.18 * press)
    d.ellipse((c - r * 1.15, c - r * 1.15, c + r * 1.15, c + r * 1.15), fill=(0, 0, 0, int(40 * a)))
    d.ellipse((c - r, c - r, c + r, c + r), fill=(255, 255, 255, int(150 * a)), outline=(255, 255, 255, int(235 * a)), width=S * 2)
    if p > 0.3:
        rr = R * (0.7 + 1.25 * ring)
        d.ellipse((c - rr, c - rr, c + rr, c + rr), outline=(255, 255, 255, int(210 * (1 - ring) * a)), width=S * 3)
    return img.resize((size // S, size // S), LANCZOS)