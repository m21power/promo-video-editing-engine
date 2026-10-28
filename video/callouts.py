"""Highlights ("callouts"): a shape drawn on the phone screen for a limited time, with an appear and a
disappear animation. They are painted onto the screenshot / video frame itself, exactly like the tap
indicators, so they move, scale and tilt together with the phone.

A scene stores them in `scene.callouts`, a list of dicts (any missing key uses DEFAULTS):
    shape      rectangle | rounded | ellipse | triangle | diamond | hexagon | star | heart | arrow | cross
    style      Outline | Filled | Outline + fill | Spotlight (dims everything except the shape)
    color      "#RRGGBB"
    x, y       centre of the shape, as fractions of the phone screen (0..1)
    w, h       size, as fractions of the screen width / height
    rot        rotation in degrees
    thickness  outline width, % of the screen width
    opacity    fill opacity (Filled / Outline + fill) or how dark the dimming is (Spotlight), 0..100
    start,end  seconds from the start of the scene
    anim_in    pop | fade | grow | draw | spin | drop | none
    anim_out   fade | shrink | burst | draw | spin | none
    anim_time  length of each animation in seconds
    pulse      a ripple ring that repeats while the shape is on screen
"""
import math

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter

from video.animations import clamp01, ease_in, ease_in_out, ease_out, ease_out_back, ease_out_bounce, smooth

LANCZOS = Image.Resampling.LANCZOS

SHAPES = ["rectangle", "rounded", "ellipse", "triangle", "diamond", "hexagon", "star", "heart", "arrow", "cross"]
STYLES = ["Outline", "Filled", "Outline + fill", "Spotlight"]
ANIM_IN = ["pop", "fade", "grow", "draw", "spin", "drop", "none"]
ANIM_OUT = ["fade", "shrink", "burst", "draw", "spin", "none"]

DEFAULTS = dict(shape="ellipse", style="Outline", color="#FFD43B", x=0.5, y=0.5, w=0.34, h=0.16, rot=0.0,
                thickness=1.0, opacity=35, start=0.8, end=2.6, anim_in="pop", anim_out="fade", anim_time=0.45,
                pulse=True)


def new_callout(start=0.8, end=2.6, **kw):
    return {**DEFAULTS, "start": round(start, 2), "end": round(end, 2), **kw}


# ------------------------------------------------------------------ geometry
def _points(shape, hw, hh):
    """Outline of a shape centred on (0, 0) with half-width hw and half-height hh (pixels, y points down)."""
    if shape == "rectangle":
        return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    if shape == "rounded":
        r, pts = min(hw, hh) * 0.45, []
        for cx, cy, a0 in ((hw - r, -hh + r, -90), (hw - r, hh - r, 0), (-hw + r, hh - r, 90), (-hw + r, -hh + r, 180)):
            for k in range(11):
                a = math.radians(a0 + 9 * k)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts
    if shape == "triangle":
        return [(0, -hh), (hw, hh), (-hw, hh)]
    if shape == "diamond":
        return [(0, -hh), (hw, 0), (0, hh), (-hw, 0)]
    if shape == "hexagon":
        return [(hw * math.cos(math.radians(-90 + 60 * k)), hh * math.sin(math.radians(-90 + 60 * k))) for k in range(6)]
    if shape == "star":
        return [((1 if k % 2 == 0 else 0.42) * hw * math.cos(math.radians(-90 + 36 * k)),
                 (1 if k % 2 == 0 else 0.42) * hh * math.sin(math.radians(-90 + 36 * k))) for k in range(10)]
    if shape == "heart":
        pts = []
        for k in range(80):
            t = 2 * math.pi * k / 80
            x = 16 * math.sin(t) ** 3
            y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
            pts.append((x / 16 * hw, (y - 2.5) / 14.5 * hh))
        return pts
    if shape == "arrow":  # points to the right; use the rotation to aim it
        return [(x * hw, y * hh) for x, y in ((-1, -.35), (.1, -.35), (.1, -1), (1, 0), (.1, 1), (.1, .35), (-1, .35))]
    if shape == "cross":
        t = 0.34
        return [(x * hw, y * hh) for x, y in ((-t, -1), (t, -1), (t, -t), (1, -t), (1, t), (t, t),
                                              (t, 1), (-t, 1), (-t, t), (-1, t), (-1, -t), (-t, -t))]
    return [(hw * math.cos(2 * math.pi * k / 96), hh * math.sin(2 * math.pi * k / 96)) for k in range(96)]  # ellipse


def _place(pts, cx, cy, deg):
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]


def _partial(pts, frac):
    """The first `frac` (0..1) of the closed outline, as a polyline: used to 'draw' the shape."""
    closed = pts + [pts[0]]
    lens = [math.dist(closed[i], closed[i + 1]) for i in range(len(closed) - 1)]
    target, acc, out = sum(lens) * clamp01(frac), 0.0, [closed[0]]
    for i, ln in enumerate(lens):
        if acc + ln <= target:
            out.append(closed[i + 1])
            acc += ln
        else:
            t = (target - acc) / ln if ln > 0 else 0.0
            a, b = closed[i], closed[i + 1]
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            break
    return out


# ----------------------------------------------------------------- animation
def _state(c, lt):
    """How the shape looks at scene-time `lt`: scale, alpha, rotation, drop offset, outline progress, fill amount.
    Returns (state, hold_time) or None when the shape is not on screen. hold_time is None while animating."""
    a, b = float(c["start"]), float(c["end"])
    if b <= a or not a <= lt <= b:
        return None
    d = min(float(c["anim_time"]), (b - a) / 2)
    st = dict(scale=1.0, alpha=1.0, rot=0.0, dy=0.0, draw=1.0, fill=1.0)
    hold = lt - (a + d)
    if d > 1e-3 and lt < a + d:  # appearing
        p, kind, hold = clamp01((lt - a) / d), c["anim_in"], None
        if kind == "pop":
            st.update(scale=ease_out_back(p, 2.2), alpha=smooth(p / 0.35))
        elif kind == "fade":
            st.update(alpha=smooth(p))
        elif kind == "grow":
            st.update(scale=ease_out(p), alpha=smooth(p / 0.3))
        elif kind == "draw":
            st.update(draw=ease_in_out(p), fill=smooth((p - 0.6) / 0.4))
        elif kind == "spin":
            st.update(scale=ease_out(p), rot=-180 * (1 - ease_out(p)), alpha=smooth(p / 0.4))
        elif kind == "drop":
            st.update(dy=-0.15 * (1 - ease_out_bounce(p)), alpha=smooth(p / 0.25))
    elif d > 1e-3 and lt > b - d:  # disappearing
        q, kind, hold = clamp01(1 - (b - lt) / d), c["anim_out"], None
        if kind == "fade":
            st.update(alpha=1 - smooth(q))
        elif kind == "shrink":
            st.update(scale=1 - ease_in(q), alpha=1 - smooth((q - 0.4) / 0.6))
        elif kind == "burst":
            st.update(scale=1 + 0.5 * ease_out(q), alpha=1 - smooth(q))
        elif kind == "draw":
            st.update(draw=1 - ease_in_out(q), fill=1 - smooth(q / 0.4))
        elif kind == "spin":
            st.update(scale=1 - ease_in(q), rot=180 * ease_in(q), alpha=1 - smooth(q))
    if st["scale"] < 0.02 or st["alpha"] < 0.01:
        return None
    return st, hold


# ----------------------------------------------------------------- rendering
def _layer(size, c, lt):
    """[(rgb, 'L' mask, (x, y))] to paste onto the phone screen for one callout, or None."""
    res = _state(c, lt)
    if res is None:
        return None
    st, hold = res
    sw, sh = size
    style, shape = c["style"], c["shape"]
    spot = style == "Spotlight"
    outline = style in ("Outline", "Outline + fill")
    rgb = ImageColor.getrgb(str(c["color"]))[:3]
    w = max(0.01, float(c["w"])) * sw * st["scale"]
    h = max(0.01, float(c["h"])) * sh * st["scale"]
    cx, cy = float(c["x"]) * sw, (float(c["y"]) + st["dy"]) * sh
    rot = float(c["rot"]) + st["rot"]
    lw = max(1.0, float(c["thickness"]) / 100 * sw)
    feather = 0.012 * sw if spot else 0.0
    ph = None
    if c.get("pulse") and hold is not None and not spot:
        ph = (hold % 1.3) / 1.3
    reach = 0.5 * math.hypot(w, h) * (1.45 if ph is not None else 1.0)
    R = int(math.ceil(reach + lw + feather * 3 + 6))
    S = max(1, min(3, 1500 // R))
    n, mid = 2 * R * S, R * S
    fa = clamp01(float(c["opacity"]) / 100)
    fm = st["fill"] if outline else st["draw"]  # shapes without an outline fade in instead of being traced

    m = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(m)
    pts = _place(_points(shape, w / 2 * S, h / 2 * S), mid, mid, rot)
    if spot:
        d.polygon(pts, fill=255)
    else:
        if style in ("Filled", "Outline + fill"):
            d.polygon(pts, fill=int(255 * fa * fm))
        if outline and st["draw"] > 0.002:
            lwS = int(round(lw * S))
            if st["draw"] >= 0.999:
                d.line(pts + [pts[0], pts[1]], fill=255, width=lwS, joint="curve")
            else:
                part = _partial(pts, st["draw"])
                d.line(part, fill=255, width=lwS, joint="curve")
                for ex, ey in (part[0], part[-1]):  # round caps
                    d.ellipse((ex - lwS / 2, ey - lwS / 2, ex + lwS / 2, ey + lwS / 2), fill=255)
        if ph is not None:  # ripple ring that grows and fades, repeating
            es = 1 + 0.35 * ease_out(ph)
            e = Image.new("L", (n, n), 0)
            epts = _place(_points(shape, w / 2 * S * es, h / 2 * S * es), mid, mid, rot)
            ImageDraw.Draw(e).line(epts + [epts[0], epts[1]], fill=int(190 * (1 - ph)),
                                   width=max(1, int(round(lw * S * 0.8))), joint="curve")
            m = ImageChops.lighter(m, e)
    m = m.resize((2 * R, 2 * R), LANCZOS)
    pos = (round(cx - R), round(cy - R))

    if spot:  # a full-screen dimming layer with a soft hole where the shape is
        if feather > 0.5:
            m = m.filter(ImageFilter.GaussianBlur(feather))
        hole = Image.new("L", (sw, sh), 0)
        hole.paste(m, pos)
        dim = fa * st["alpha"] * fm
        return [(rgb, hole.point([int(dim * (255 - v)) for v in range(256)]), (0, 0))]
    a = st["alpha"]
    return [(rgb, m.point([int(v * a) for v in range(256)]), pos)]


def layers(size, callouts, lt):
    """All callouts that are visible `lt` seconds into the scene, in drawing order."""
    out = []
    for c in callouts or []:
        try:
            lay = _layer(size, {**DEFAULTS, **c}, lt)
        except Exception:  # a bad value in a hand-edited project must never break the render
            lay = None
        if lay:
            out.extend(lay)
    return out