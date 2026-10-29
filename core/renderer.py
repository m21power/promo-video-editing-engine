"""Composition engine: Settings + screenshot -> final RGB image at any size."""
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageOps

import config
from core import fonts, frames

BICUBIC = Image.Resampling.BICUBIC
LANCZOS = Image.Resampling.LANCZOS


def rgb(value, fallback=(255, 255, 255)):
    try:
        return tuple(ImageColor.getrgb(str(value)))[:3]
    except ValueError:
        return fallback


def canvas_size(s):
    return config.CANVAS_PRESETS.get(s.canvas) or next(iter(config.CANVAS_PRESETS.values()))


# ------------------------------------------------------------------ background
def _dither(mask):
    """Tiny noise on gradient masks removes visible banding."""
    return ImageChops.add(mask, Image.effect_noise(mask.size, 6), 1.0, -128)


def _linear_mask(W, H, angle):
    d = int(math.hypot(W, H)) + 4
    g = Image.linear_gradient("L").resize((d, d), BICUBIC).rotate(angle, resample=BICUBIC)
    l, t = (d - W) // 2, (d - H) // 2
    return g.crop((l, t, l + W, t + H))


_bg_dir = None
BG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def set_backgrounds_dir(path):
    global _bg_dir
    _bg_dir = Path(path)


def list_backgrounds():
    if not _bg_dir or not _bg_dir.exists():
        return []
    return sorted((p.name for p in _bg_dir.iterdir() if p.suffix.lower() in BG_EXT), key=str.lower)


@lru_cache(maxsize=2)
def _load_bg(path, mtime):
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


@lru_cache(maxsize=4)
def _bg_image(path, mtime, zoom, pan_x, pan_y, dim, W, H):
    out = _fit_screenshot(_load_bg(path, mtime), (W, H), "Cover", zoom, pan_x, pan_y)
    return Image.blend(out, Image.new("RGB", (W, H), (0, 0, 0)), dim / 100) if dim else out


def background(s, W, H):
    if s.bg_mode == "Image" and _bg_dir:
        path = _bg_dir / s.bg_image
        if s.bg_image != "None" and path.is_file():
            return _bg_image(str(path), path.stat().st_mtime, s.bg_img_zoom, s.bg_img_pan_x,
                             s.bg_img_pan_y, s.bg_img_dim, W, H).copy()
    key = (s.bg_mode, rgb(s.bg_color1, (11, 16, 32)), rgb(s.bg_color2, (43, 28, 115)), s.bg_angle, W, H)
    return _background(*key).copy()


@lru_cache(maxsize=4)
def _background(mode, c1, c2, angle, W, H):
    if mode == "Solid":
        return Image.new("RGB", (W, H), c1)
    if mode == "Radial gradient":
        rw, rh = int(W * 1.7), int(H * 1.5)
        mask = Image.new("L", (W, H), 255)
        mask.paste(Image.radial_gradient("L").resize((rw, rh), BICUBIC), (int(W / 2 - rw / 2), int(H * 0.42 - rh / 2)))
    else:
        mask = _linear_mask(W, H, angle)
    mask = _dither(mask)
    return Image.composite(Image.new("RGB", (W, H), c2), Image.new("RGB", (W, H), c1), mask)


# ------------------------------------------------------------------------ text
def _wrap(text, font, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if cur and font.getlength(trial) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return lines


def layout_text(s, W, H):
    """Measure the text block. Returns (blocks, bottom_y)."""
    margin, dx, y = s.text_margin / 100 * W, s.text_x / 100 * W, s.text_y / 100 * H
    x, anchor = {"left": (margin + dx, "la"), "center": (W / 2 + dx, "ma"), "right": (W - margin + dx, "ra")}[s.text_align]
    specs = [(s.title, s.title_font, s.title_size, s.title_color, 100, s.line_spacing, True),
             (s.subtitle, s.subtitle_font, s.subtitle_size, s.subtitle_color, s.subtitle_opacity, 1.3, False)]
    blocks = []
    for text, fname, size, color, opacity, spacing, bold in specs:
        if not text or not text.strip():
            continue
        font = fonts.load_font(fname, max(8, round(size / 100 * W)), bold)
        asc, desc = font.getmetrics()
        lh = (asc + desc) * spacing
        if blocks:
            y += s.text_gap / 100 * W
        items = []
        for line in _wrap(text.strip("\n"), font, W - 2 * margin):
            items.append((x, y, line, font))
            y += lh
        blocks.append(dict(items=items, anchor=anchor, color=rgb(color), opacity=opacity))
    return blocks, y


class TextLayer(tuple):
    """(colour, L-mask) exactly as before, plus `.emoji`: an RGBA picture with the colour emoji (or None)."""
    emoji = None


def text_masks(blocks, size):
    """[(color, L-mask)] so text can be pasted quickly on every frame. Each item also has `.emoji` (RGBA or None):
    paste it after the mask:  canvas.paste(layer.emoji, pos, layer.emoji)."""
    out = []
    for b in blocks:
        mask = Image.new("L", size, 0)
        d = ImageDraw.Draw(mask)
        emo = Image.new("RGBA", size, (0, 0, 0, 0)) if any(fonts.has_emoji(it[2]) for it in b["items"]) else None
        for x, y, line, font in b["items"]:
            fonts.draw_text(d, (x, y), line, font, 255, b["anchor"], emo)
        if b["opacity"] < 100:
            lut = [int(v * b["opacity"] / 100) for v in range(256)]
            mask = mask.point(lut)
            if emo is not None:
                emo.putalpha(emo.getchannel("A").point(lut))
        layer = TextLayer((b["color"], mask))
        layer.emoji = emo
        out.append(layer)
    return out


# ----------------------------------------------------------------------- phone
@lru_cache(maxsize=1)
def placeholder():
    w, h = 1080, 2340
    ramp = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), (40, 44, 70)), Image.new("RGB", (w, h), (14, 16, 26)), ramp)
    fonts.draw_text(ImageDraw.Draw(img), (w / 2, h / 2), "Select a screenshot", fonts.load_font("Auto", 64, False),
                    (150, 156, 190), "mm")
    return img


def _offset(view, content, pan):
    free = (view - content) / 2
    return free * (1 + pan / 100) if content >= view else free * (1 - pan / 100)


_fit_cache = {}


def fit_screenshot(img, size, mode, zoom, pan_x, pan_y):
    """Scale/crop the screenshot into the phone screen (cached for the last call)."""
    key = (size, mode, zoom, pan_x, pan_y)
    hit = _fit_cache.get("last")
    if hit and hit[0] is img and hit[1] == key:
        return hit[2]
    out = _fit_screenshot(img, size, mode, zoom, pan_x, pan_y)
    _fit_cache["last"] = (img, key, out)
    return out


def _fit_screenshot(img, size, mode, zoom, pan_x, pan_y):
    sw, sh = size
    iw, ih = img.size
    if mode == "Stretch":
        sx, sy = sw / iw, sh / ih
    else:
        sx = sy = (max if mode == "Cover" else min)(sw / iw, sh / ih)
    sx *= zoom / 100
    sy *= zoom / 100
    nw, nh = iw * sx, ih * sy
    ox, oy = _offset(sw, nw, pan_x), _offset(sh, nh, pan_y)
    out = Image.new("RGB", size, (0, 0, 0))
    vx0, vy0 = max(0, math.floor(ox)), max(0, math.floor(oy))
    vx1, vy1 = min(sw, math.ceil(ox + nw)), min(sh, math.ceil(oy + nh))
    if vx1 <= vx0 or vy1 <= vy0:
        return out
    box = ((vx0 - ox) / sx, (vy0 - oy) / sy, (vx1 - ox) / sx, (vy1 - oy) / sy)
    box = (max(0, box[0]), max(0, box[1]), min(iw, box[2]), min(ih, box[3]))
    out.paste(img.resize((vx1 - vx0, vy1 - vy0), LANCZOS, box=box, reducing_gap=2.0), (vx0, vy0))
    return out


def build_phone(s, shot, body_w):
    fa = frames.get_frame(s.frame, body_w, s.frame_color, s.glass_sheen)
    phone = Image.new("RGBA", fa.size, (0, 0, 0, 0))
    if fa.body is not None:
        phone.alpha_composite(fa.body)
    l, t, r, b = fa.screen
    fitted = fit_screenshot(shot if shot is not None else placeholder(), (r - l, b - t),
                            s.shot_fit, s.shot_zoom, s.shot_pan_x, s.shot_pan_y)
    layer = Image.new("RGBA", fa.size, (0, 0, 0, 0))
    layer.paste(fitted, (l, t))
    box = Image.new("L", fa.size, 0)
    box.paste(255, (l, t, r, b))
    layer.putalpha(ImageChops.multiply(box, fa.mask))
    phone.alpha_composite(layer)
    phone.alpha_composite(fa.overlay)
    return phone


def _rotate(img, angle, bleed):
    """Rotate RGBA without dark fringes (transparent pixels carry the frame colour)."""
    a = img.getchannel("A")
    base = Image.new("RGB", img.size, bleed)
    base.paste(img.convert("RGB"), mask=a.point(lambda v: 255 if v else 0))
    base.putalpha(a)
    return base.rotate(angle, resample=BICUBIC, expand=True, fillcolor=bleed + (0,))


def _combined_shadow(alpha, parts):
    """Blend several blurred drop shadows into one mask. Returns (mask, margin)."""
    layers = []
    for blur, dy, opacity in parts:
        pad = int(blur * 3) + 2
        layer = Image.new("L", (alpha.width + 2 * pad, alpha.height + 2 * pad), 0)
        layer.paste(alpha, (pad, pad))
        layer = layer.filter(ImageFilter.GaussianBlur(blur)).point([int(v * opacity) for v in range(256)])
        layers.append((layer, -pad, -pad + int(dy)))
    m = max(max(-ox, -oy, ox + l.width - alpha.width, oy + l.height - alpha.height) for l, ox, oy in layers)
    out = Image.new("L", (alpha.width + 2 * m, alpha.height + 2 * m), 0)
    for layer, ox, oy in layers:
        tmp = Image.new("L", out.size, 0)
        tmp.paste(layer, (m + ox, m + oy))
        out = ImageChops.screen(out, tmp)
    return out, m


class Scene:
    """Everything pre-rendered as layers, so moving the phone is just a few pastes."""

    def __init__(self, s, shot, W, H):
        self.base = background(s, W, H)
        blocks, text_bottom = layout_text(s, W, H)
        self.text = text_masks(blocks, (W, H))
        boxes = []  # bounding box of the whole text block, used for click-to-drag hit testing
        for b in blocks:
            for x, y, line, font in b["items"]:
                if line:
                    w, (asc, desc) = font.getlength(line), font.getmetrics()
                    left = {"la": x, "ma": x - w / 2, "ra": x - w}[b["anchor"]]
                    boxes.append((left, y, left + w, y + asc + desc))
        pad = 0.02 * W
        self.text_bbox = (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
                          max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad) if boxes else None

        body_w = max(40, int(W * s.phone_scale / 100))
        phone = build_phone(s, shot, body_w)
        ph_h = phone.height
        top = text_bottom + s.phone_gap / 100 * H if s.auto_phone_y else s.phone_y / 100 * H
        cx, cy = W / 2 + s.phone_x / 100 * W, top + ph_h / 2

        self.glow = None
        if s.glow_strength > 0:
            rx, ry = body_w * 1.15, ph_h * 0.55
            lut = [int(((v / 255) ** 2) * 255 * s.glow_strength / 100 * 0.9) for v in range(256)]
            g = ImageOps.invert(Image.radial_gradient("L")).point(lut)
            g = g.resize((max(2, int(rx * 2)), max(2, int(ry * 2))), BICUBIC)
            self.glow = (rgb(s.glow_color), g, (int(cx - rx), int(cy - ry)))

        if s.phone_rotation:
            phone = _rotate(phone, s.phone_rotation, rgb(s.frame_color, (21, 23, 28)))
        px, py = round(cx - phone.width / 2), round(cy - phone.height / 2)
        self.phone, self.phone_pos = phone, (px, py)

        self.shadow = None
        k = s.phone_shadow / 100
        if k > 0:
            mask, m = _combined_shadow(phone.getchannel("A"), [(body_w * 0.055, body_w * 0.07, 0.55 * k),
                                                                (body_w * 0.012, body_w * 0.012, 0.45 * k)])
            self.shadow = (mask, (px - m, py - m))

    def compose(self, dx=0, dy=0, tx=0, ty=0):
        """(dx, dy) moves the phone layers, (tx, ty) moves the text."""
        cv = self.base.copy()
        if self.glow:
            color, mask, (x, y) = self.glow
            cv.paste(color, (x + dx, y + dy), mask)
        if self.shadow:
            mask, (x, y) = self.shadow
            cv.paste((0, 0, 0), (x + dx, y + dy), mask)
        cv.paste(self.phone, (self.phone_pos[0] + dx, self.phone_pos[1] + dy), self.phone)
        for layer in self.text:
            color, mask = layer
            cv.paste(color, (tx, ty), mask)
            if layer.emoji is not None:  # colour emoji on top of the text
                cv.paste(layer.emoji, (tx, ty), layer.emoji)
        return cv


def make_scene(s, shot=None, width=None):
    cw, ch = canvas_size(s)
    k = width / cw if width else 1.0
    return Scene(s, shot, max(1, round(cw * k)), max(1, round(ch * k)))


def render(s, shot=None, width=None):
    """Compose the final graphic. `width` renders a scaled preview; None = full size."""
    return make_scene(s, shot, width).compose()