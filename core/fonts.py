"""Font discovery and loading (fully offline, uses fonts installed on the system)."""
import os
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

import config

_custom_dir = None
_SUFFIXES = {".ttf", ".otf", ".ttc"}


def set_custom_dir(path):
    global _custom_dir
    _custom_dir = Path(path)
    available_fonts.cache_clear()


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
        if "variable" in stem.lower():
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


def load_font(name, size, bold=True):
    path = resolve(name, bold)
    if path:
        try:
            return _truetype(path, int(size))
        except OSError:
            pass
    return ImageFont.load_default(size=int(size))
