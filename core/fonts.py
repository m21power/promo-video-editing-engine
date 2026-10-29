"""Font discovery and loading (fully offline, uses fonts installed on the system).

Emoji: normal text fonts (Inter, Poppins...) have no emoji, so they showed up as empty squares. `load_font` now returns a
`MixedFont`: letters are drawn with the font you chose, emoji with the system colour emoji font (Segoe UI Emoji on
Windows, Apple Color Emoji on macOS, Noto Color Emoji on Linux). Always draw text with `draw_text(...)`, and measure it
with `font.getlength(...)` as before. To use a specific emoji font, drop it into assets/fonts (e.g. NotoColorEmoji.ttf).
"""
import os
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config

_custom_dir = None
_SUFFIXES = {".ttf", ".otf", ".ttc"}


def set_custom_dir(path):
    global _custom_dir
    _custom_dir = Path(path)
    available_fonts.cache_clear()
    _emoji_face.cache_clear()
    _mono_emoji_path.cache_clear()


def _font_dirs():
    home = Path.home()
    dirs = [_custom_dir] if _custom_dir else []
    if sys.platform.startswith("win"):
        dirs += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library/Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts", home / ".local/share/fonts"]
    return [d for d in dirs if d and d.exists()]


@lru_cache(maxsize=1)
def available_fonts():
    """{display name: path}; fonts in assets/fonts win over system fonts."""
    found = {}
    for d in _font_dirs():
        for p in d.rglob("*"):
            if p.suffix.lower() in _SUFFIXES and p.stem not in found:
                found[p.stem] = str(p)
    return dict(sorted(found.items(), key=lambda kv: kv[0].lower()))


WEIGHT_ORDER = ["Thin", "ExtraLight", "Light", "Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Black"]


def families():
    """{family: {weight: font name}} built from file names like Family-Bold.ttf.
    Variable fonts are skipped (use the static files instead)."""
    fams = {}
    for stem in available_fonts():
        if "variable" in stem.lower() or _is_emoji_name(stem):
            continue
        fam, _, weight = stem.rpartition("-")
        if not fam:
            fam, weight = stem, "Regular"
        fams.setdefault(fam, {})[weight] = stem
    ordered = {}
    for fam, ws in sorted(fams.items(), key=lambda kv: kv[0].lower()):
        key = lambda w: WEIGHT_ORDER.index(w) if w in WEIGHT_ORDER else 99
        ordered[fam] = dict(sorted(ws.items(), key=lambda kv: (key(kv[0]), kv[0])))
    return ordered


def resolve(name, bold=True):
    fonts = available_fonts()
    if name in fonts:
        return fonts[name]
    lower = {k.lower(): v for k, v in fonts.items()}
    for cand in (config.DEFAULT_TITLE_FONTS if bold else config.DEFAULT_BODY_FONTS):
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


@lru_cache(maxsize=64)
def _truetype(path, size):
    return ImageFont.truetype(path, size)


# ------------------------------------------------------------------------------ emoji
_ZWJ, _VS16, _KEYCAP = 0x200D, 0xFE0F, 0x20E3
# characters below U+1F000 that are emoji by default (the others need the U+FE0F "emoji" selector after them)
_EMOJI_DEFAULT = (
    (0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3), (0x25FD, 0x25FE), (0x2614, 0x2615),
    (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE),
    (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5),
    (0x26FA, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C),
    (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55))
_QUICK_MIN = 0xA9   # nothing below this can be (part of) an emoji except digits / # / * keycaps, handled separately


def _emoji_start(cp):
    return 0x1F000 <= cp <= 0x1FAFF or any(a <= cp <= b for a, b in _EMOJI_DEFAULT)


def _emoji_end(text, i):
    """End index of the emoji sequence starting at text[i] (a flag, a family, a skin tone, a keycap...), or None."""
    n, cp = len(text), ord(text[i])
    nxt = ord(text[i + 1]) if i + 1 < n else 0
    keycap = text[i] in "0123456789#*" and (nxt == _KEYCAP or (nxt == _VS16 and i + 2 < n and ord(text[i + 2]) == _KEYCAP))
    if not (_emoji_start(cp) or keycap or (nxt == _VS16 and cp >= _QUICK_MIN)):
        return None
    j = i + 1
    if 0x1F1E6 <= cp <= 0x1F1FF and j < n and 0x1F1E6 <= ord(text[j]) <= 0x1F1FF:  # flag = two letters
        return j + 1
    while j < n:
        c = ord(text[j])
        if c in (_VS16, _KEYCAP) or 0x1F3FB <= c <= 0x1F3FF or 0xE0020 <= c <= 0xE007F:   # selector, tone, tags
            j += 1
        elif c == _ZWJ and j + 1 < n and (_emoji_start(ord(text[j + 1])) or (j + 2 < n and ord(text[j + 2]) == _VS16)):
            j += 2   # joined emoji: 👨‍👩‍👧
        else:
            break
    return j


@lru_cache(maxsize=512)
def split_runs(text):
    """[(is_emoji, substring)] in order."""
    out, i, start, n = [], 0, 0, len(text)
    while i < n:
        end = _emoji_end(text, i) if ord(text[i]) >= 0x23 else None
        if end:
            if start < i:
                out.append((False, text[start:i]))
            out.append((True, text[i:end]))
            i = start = end
        else:
            i += 1
    if start < n:
        out.append((False, text[start:]))
    return tuple(out)


def has_emoji(text):
    return any(c >= "\u00a9" or c in "#*0123456789" for c in text or "") and any(e for e, _ in split_runs(text))


def _is_emoji_name(stem):
    s = stem.lower().replace(" ", "").replace("_", "").replace("-", "")
    return any(k in s for k in ("coloremoji", "seguiemj", "twemoji", "emojione", "tossface", "notoemoji"))


_EMOJI_NAMES = ("notocoloremoji", "seguiemj", "applecoloremoji", "twemoji", "tossface", "emojione")


@lru_cache(maxsize=1)
def _emoji_face():
    """(font, native size) of the colour emoji font, or None. Bitmap emoji fonts only exist at a fixed size, so the
    font is opened at that size and the pictures are scaled afterwards."""
    found = {}
    for stem, path in available_fonts().items():
        key = stem.lower().replace(" ", "").replace("_", "").replace("-", "")
        for rank, name in enumerate(_EMOJI_NAMES):
            if key.startswith(name):
                found.setdefault(rank, path)
    for _, path in sorted(found.items()):
        for size in (160, 109, 136, 128, 96, 64):
            try:
                return ImageFont.truetype(path, size), size
            except OSError:
                continue
    return None


@lru_cache(maxsize=1)
def _mono_emoji_path():
    """No colour emoji font: a black-and-white symbol font is used so emoji at least show as shapes (not squares)."""
    names = ("notoemoji", "seguisym", "symbola", "dejavusans", "arialuni", "freeserif", "applesymbols")
    fonts = {k.lower().replace(" ", "").replace("_", "").replace("-", ""): v for k, v in available_fonts().items()}
    for n in names:
        for k, v in fonts.items():
            if k.startswith(n):
                return v
    return None


@lru_cache(maxsize=256)
def emoji_cell(seq, size):
    """(RGBA picture, advance width) of one emoji sequence for text of pixel size `size`, or None without an emoji font."""
    face = _emoji_face()
    if face is None:
        return None
    f, native = face
    asc, desc = f.getmetrics()
    adv = max(1, int(round(f.getlength(seq))))
    im = Image.new("RGBA", (adv + 8, asc + desc + 8), (0, 0, 0, 0))
    try:
        ImageDraw.Draw(im).text((4, 4), seq, font=f, embedded_color=True, anchor="la")
    except (OSError, ValueError, TypeError):
        return None
    k = size / native
    w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
    return im.resize((w, h), Image.Resampling.LANCZOS), max(1.0, adv * k)


class MixedFont:
    """A text font plus emoji. Quacks like a PIL font for measuring; draw it with `draw_text`."""

    def __init__(self, base):
        self.base = base

    def __getattr__(self, name):          # size, path, getmask2...: everything else comes from the real font
        return getattr(self.base, name)

    def getmetrics(self):
        return self.base.getmetrics()

    def _adv(self, seq):
        cell = emoji_cell(seq, self.base.size)
        if cell:
            return cell[1]
        return self._mono(self.base.size).getlength(seq) if _mono_emoji_path() else self.base.getlength(seq)

    @staticmethod
    def _mono(size):
        return _truetype(_mono_emoji_path(), int(size))

    def getlength(self, text, mode="", direction=None, features=None, language=None):
        if not has_emoji(text):
            return self.base.getlength(text, mode, direction, features, language)
        return sum(self._adv(s) if e else self.base.getlength(s) for e, s in split_runs(text))

    def getbbox(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None):
        if not has_emoji(text):
            return self.base.getbbox(text, mode, direction, features, language, stroke_width, anchor)
        w, (asc, desc) = self.getlength(text), self.getmetrics()
        a = anchor or "la"
        x0 = -{"l": 0, "m": w / 2, "r": w}.get(a[0], 0)
        y0 = -{"a": 0, "t": 0, "m": (asc + desc) / 2, "s": asc, "b": asc + desc, "d": asc + desc}.get(a[1], 0)
        return (x0, y0, x0 + w, y0 + asc + desc)


def _paste_rgba(layer, img, x, y):
    """alpha_composite that tolerates positions partly outside the layer."""
    x, y = int(round(x)), int(round(y))
    l, t = max(0, -x), max(0, -y)
    r, b = min(img.width, layer.width - x), min(img.height, layer.height - y)
    if r > l and b > t:
        layer.alpha_composite(img.crop((l, t, r, b)), (x + l, y + t))


def draw_text(draw, xy, text, font, fill=255, anchor="la", emoji=None):
    """Draw text with emoji. `draw` is an ImageDraw (an "L" mask or an RGBA picture), `fill` its colour.
    Colour emoji are painted into `emoji` (an RGBA picture of the same size, e.g. one made for the mask and pasted
    over it afterwards); pass the drawn picture itself when `draw` draws on RGBA. Without `emoji` they are skipped."""
    if not isinstance(font, MixedFont) or not has_emoji(text):
        draw.text(xy, text, font=font.base if isinstance(font, MixedFont) else font, fill=fill, anchor=anchor)
        return
    x, y = xy
    asc, desc = font.getmetrics()
    h, w = asc + desc, font.getlength(text)
    x -= {"l": 0, "m": w / 2, "r": w}.get(anchor[0], 0)
    y -= {"a": 0, "t": 0, "m": h / 2, "s": asc, "b": h, "d": h}.get(anchor[1], 0)
    size = font.base.size
    for is_emoji, run in split_runs(text):
        if not is_emoji:
            draw.text((x, y), run, font=font.base, fill=fill, anchor="la")
            x += font.base.getlength(run)
            continue
        cell = emoji_cell(run, size)
        if cell:
            if emoji is not None:
                _paste_rgba(emoji, cell[0], x - 4 * size / _emoji_face()[1], y + (h - cell[0].height) / 2)
            x += cell[1]
        elif _mono_emoji_path():       # no colour emoji font on this computer: draw the plain symbol instead
            draw.text((x, y), run, font=font._mono(size), fill=fill, anchor="la")
            x += font._mono(size).getlength(run)
        else:
            draw.text((x, y), run, font=font.base, fill=fill, anchor="la")
            x += font.base.getlength(run)


def load_font(name, size, bold=True):
    path = resolve(name, bold)
    if path:
        try:
            return MixedFont(_truetype(path, int(size)))
        except OSError:
            pass
    return MixedFont(ImageFont.load_default(size=int(size)))