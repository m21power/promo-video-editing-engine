"""
Phone frames. Procedural frames are drawn at 2x and downsampled for clean edges.
Custom frames: drop a PNG with a *transparent screen area* into assets/frames/.
The screen shape is detected automatically (flood fill from the centre).
"""
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter

import config

LANCZOS = Image.Resampling.LANCZOS
CUSTOM_PREFIX = "Custom · "
_custom_dir = None
_cache = {}


@dataclass
class FrameAsset:
    body: Image.Image      # RGBA, drawn under the screenshot (None for PNG frames)
    overlay: Image.Image   # RGBA, drawn over the screenshot (camera, glass, PNG frame)
    mask: Image.Image      # L, exact screen shape
    screen: tuple          # (l, t, r, b) bounding box of the screen
    size: tuple


def set_custom_dir(path):
    global _custom_dir
    _custom_dir = Path(path)
    _cache.clear()


def list_frames():
    names = list(config.FRAME_STYLES)
    if _custom_dir and _custom_dir.exists():
        names += [CUSTOM_PREFIX + p.stem for p in sorted(_custom_dir.glob("*.png"))]
    return names


def get_frame(name, body_w, color, sheen):
    key = (name, int(body_w), color, int(sheen))
    if key not in _cache:
        if len(_cache) > 16:
            _cache.clear()
        png = _custom_dir / f"{name[len(CUSTOM_PREFIX):]}.png" if (_custom_dir and name.startswith(CUSTOM_PREFIX)) else None
        if png and png.exists():
            _cache[key] = _build_png(png, int(body_w))
        else:
            style = config.FRAME_STYLES.get(name) or next(iter(config.FRAME_STYLES.values()))
            _cache[key] = _build_style(style, int(body_w), color, sheen)
    return _cache[key]


def _mix(c, target, a):
    return tuple(int(c[i] * (1 - a) + target[i] * a) for i in range(3))


def _build_style(st, body_w, color, sheen):
    S = 2
    w = max(60, body_w) * S
    h = int(w * st["aspect"])
    has_body = st.get("body", True)
    pad = int(w * 0.028) if st.get("buttons") else 0
    W, H = w + 2 * pad, h
    try:
        base = tuple(ImageColor.getrgb(color))[:3]
    except ValueError:
        base = (21, 23, 28)

    bezel = st["bezel"] * w
    r_out = st["radius"] * w
    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    mask = Image.new("L", (W, H), 0)

    sx0, sy0, sx1, sy1 = pad + bezel, bezel, pad + w - 1 - bezel, h - 1 - bezel
    r_scr = max(2.0, r_out - bezel * 0.9) if has_body else r_out

    if has_body:
        d = ImageDraw.Draw(body)
        if pad:  # side buttons (power + volume), tucked under the body edge
            btn = _mix(base, (0, 0, 0), 0.25)
            x0, x1 = pad + w - S * 3, pad + w + pad * 0.85
            for y0, y1 in ((0.20, 0.30), (0.33, 0.40)):
                d.rounded_rectangle((x0, h * y0, x1, h * y1), radius=S * 3, fill=btn)
        box = (pad, 0, pad + w - 1, h - 1)
        d.rounded_rectangle(box, radius=r_out, fill=base)
        d.rounded_rectangle(box, radius=r_out, outline=_mix(base, (255, 255, 255), 0.35), width=max(2, int(S * 1.4)))
        inset = bezel * 0.38  # black glass edge around the display
        d.rounded_rectangle((pad + inset, inset, pad + w - 1 - inset, h - 1 - inset),
                            radius=max(2.0, r_out - inset), fill=(7, 8, 10))
    ImageDraw.Draw(mask).rounded_rectangle((sx0, sy0, sx1, sy1), radius=r_scr, fill=255)

    # glass reflection, clipped to the screen
    if sheen > 0:
        sm = Image.new("L", (W, H), 0)
        sd = ImageDraw.Draw(sm)
        sd.polygon([(W * 0.30, 0), (W * 0.78, 0), (0, H * 0.42), (0, H * 0.20)], fill=255)
        sd.polygon([(W * 0.82, 0), (W * 0.90, 0), (0, H * 0.48), (0, H * 0.445)], fill=170)
        sm = ImageChops.multiply(sm.filter(ImageFilter.GaussianBlur(w * 0.008)), mask)
        sm = sm.point([int(v * sheen / 100 * 0.15) for v in range(256)])
        white = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        white.putalpha(sm)
        over.alpha_composite(white)

    od = ImageDraw.Draw(over)
    cx = pad + w / 2
    cam = st.get("camera")
    if cam == "punch":
        r = st["cam"] * w / 2
        cy = sy0 + r * 2.1
        od.ellipse((cx - r * 1.4, cy - r * 1.4, cx + r * 1.4, cy + r * 1.4), fill=(3, 3, 5))
        od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(10, 11, 16), outline=(36, 40, 56), width=max(1, S))
        g = r * 0.34
        od.ellipse((cx - g - r * 0.18, cy - g - r * 0.2, cx + g - r * 0.18, cy + g - r * 0.2), fill=(28, 42, 84))
    elif cam == "pill":
        spw, sph = w * 0.20, w * 0.014
        cy = bezel * 0.5
        od.rounded_rectangle((cx - spw / 2, cy - sph / 2, cx + spw / 2, cy + sph / 2), radius=sph / 2, fill=(26, 28, 34))
        r = w * 0.0085
        od.ellipse((cx + spw / 2 + w * 0.04 - r, cy - r, cx + spw / 2 + w * 0.04 + r, cy + r), fill=(14, 16, 24), outline=(40, 46, 64))
    if not has_body:  # hairline edge so frameless screens don't melt into the background
        od.rounded_rectangle((sx0, sy0, sx1, sy1), radius=r_scr, outline=(255, 255, 255, 46), width=S * 2)

    size = (W // S, H // S)
    body, over, mask = (im.resize(size, LANCZOS) for im in (body, over, mask))
    screen = mask.point(lambda v: 255 if v > 127 else 0).getbbox()
    return FrameAsset(body if has_body else None, over, mask, screen, size)


def _build_png(path, body_w):
    img = Image.open(path).convert("RGBA")
    img = img.resize((body_w, max(1, round(img.height * body_w / img.width))), LANCZOS)
    hole = img.getchannel("A").point(lambda v: 255 if v < 20 else 0)
    c = (img.width // 2, img.height // 2)
    if hole.getpixel(c) == 255:
        ImageDraw.floodfill(hole, c, 128)
        mask = hole.point(lambda v: 255 if v == 128 else 0).filter(ImageFilter.MaxFilter(3))
    else:  # no transparent screen found: fall back to an inset rounded rectangle
        mask = Image.new("L", img.size, 0)
        i = int(img.width * 0.05)
        ImageDraw.Draw(mask).rounded_rectangle((i, i, img.width - i, img.height - i), radius=i * 2, fill=255)
    return FrameAsset(None, img, mask, mask.getbbox(), img.size)
