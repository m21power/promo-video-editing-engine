"""Frame renderer + FFmpeg encoder. Reuses the same look (background, frame, text) as the still images."""
import math
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

import config
from core import fonts, frames
from core import renderer as R
from core.settings import Settings
from video import animations as A
from video import callouts
from video import clips as C
from video import premium  # noqa: F401  (registers the premium motions and transitions)
from video import transitions as T
from video.audio import build_graph
from video.fx import Effects
from video.timeline import is_blank, is_video

BICUBIC, LANCZOS = Image.Resampling.BICUBIC, Image.Resampling.LANCZOS
# per-scene text style overrides allowed in a scene's `text_style`
TEXT_STYLE_KEYS = {"title_font", "subtitle_font", "title_size", "subtitle_size", "title_color", "subtitle_color",
                   "subtitle_opacity", "text_align", "text_x", "text_y", "text_margin", "line_spacing", "text_gap"}


def _has_ethiopic(text):
    return any(0x1200 <= ord(c) <= 0x139F or 0x2D80 <= ord(c) <= 0x2DDF or 0xAB00 <= ord(c) <= 0xAB2F for c in str(text or ""))


@lru_cache(maxsize=4)
def _ethiopic_font(bold):
    """File name of an installed / dropped-in font that can draw Amharic (Ethiopic), or None."""
    try:
        fams = fonts.families()
    except Exception:
        return None
    keys = ("ethiopic", "abyssinica", "nyala", "jiret", "washra", "wookianos", "ethi")
    for fam, ws in fams.items():
        if ws and any(k in fam.lower() for k in keys):
            return (ws.get("Bold") if bold else ws.get("Regular")) or ws.get("Regular") or ws.get("Bold") or next(iter(ws.values()))
    for ws in fams.values():
        for name in ws.values():
            if any(k in str(name).lower() for k in keys):
                return name
    return None


def _font_for(name, text, bold):
    """The chosen font, or for "Auto" a font that can draw the text (Amharic needs an Ethiopic font)."""
    if name in ("", None, "Auto") and _has_ethiopic(text):
        return _ethiopic_font(bool(bold)) or "Auto"
    return name or "Auto"


def load_image(path):
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def resolve_image(raw_dir, name):
    p = Path(name)
    return p if p.is_absolute() else Path(raw_dir) / p


@lru_cache(maxsize=128)
def _lut(q):
    return [int(v * q / 100) for v in range(256)]


def _even(n):
    return max(2, int(round(n)) // 2 * 2)


class FrameRenderer:
    def __init__(self, design, project, images, width, assets_dir=None, fx=None, raw_dir=None, live=False):
        """images: {scene.image: PIL RGB image} for screenshot scenes. Renders RGB frames at `width` px.
        fx: premium effect options. raw_dir: where relative media names live (needed for video scenes).
        live=True is the editor preview: video frames decode in the background at up to 30 fps."""
        self.s, self.p, self.images = design, project, images
        self.raw_dir, self._ff = raw_dir, None
        self.clip_fps = min(30, int(project.fps)) if live else int(project.fps)
        self._cl, self._posters, self._vs = {}, {}, OrderedDict()
        self._cs = {}                                # blank scenes: cached "centre the text" shifts
        self.outro_boxes = {}                        # end card: pixel box of every element at the last drawn frame
        self.errors, self._reported = [], set()     # video problems, shown by the editor in its status bar
        cw, ch = R.canvas_size(design)
        k = width / cw
        self.W, self.H = _even(cw * k), _even(ch * k)
        W, H = self.W, self.H
        self.segs, self.total = project.segments()
        self.bg = R.background(design, W, H)
        self.fx = Effects(W, H, R.rgb(design.glow_color, (60, 120, 240)), fx)

        body_w = max(40, int(W * design.phone_scale / 100))
        self.body_w = body_w
        fa = self.fa = frames.get_frame(design.frame, body_w, design.frame_color, design.glass_sheen)
        l, t, r, b = fa.screen
        self.screen_pos, self.screen_size = (l, t), (r - l, b - t)
        box = Image.new("L", fa.size, 0)
        box.paste(255, (l, t, r, b))
        self.mask_crop = ImageChops.multiply(box, fa.mask).crop((l, t, r, b))
        self.body = fa.body if fa.body is not None else Image.new("RGBA", fa.size, (0, 0, 0, 0))

        _, bottom = R.layout_text(design, W, H)
        top = bottom + design.phone_gap / 100 * H if design.auto_phone_y else design.phone_y / 100 * H
        self.cx0, self.cy0 = W / 2 + design.phone_x / 100 * W, top + fa.size[1] / 2

        self.glow = None
        if design.glow_strength > 0:
            rx, ry = body_w * 1.15, fa.size[1] * 0.55
            lut = [int(((v / 255) ** 2) * 255 * design.glow_strength / 100 * 0.9) for v in range(256)]
            g = ImageOps.invert(Image.radial_gradient("L")).point(lut)
            self.glow = (R.rgb(design.glow_color), g.resize((int(rx * 2), int(ry * 2)), BICUBIC))

        self._tc, self._screens, self._sprites, self._bl, self._plans = {}, {}, {}, {}, {}
        assets = Path(assets_dir) if assets_dir else None
        self.logo = self._load_asset(assets, "logo.png", height=W * 0.1)
        self.logo_big = self._load_asset(assets, "logo.png", height=W * 0.30)
        self._og, self._ob, self._assets = None, {}, assets
        self.logo_shift = ((self.logo.height + W * 0.03) / H * 100) if self.logo else 0.0
        self.cta = self._make_cta(assets)

    # ------------------------------------------------------------------ assets
    @staticmethod
    def _load_asset(assets, name, height=None, width=None):
        if not assets or not (assets / name).exists():
            return None
        im = Image.open(assets / name).convert("RGBA")
        k = (height / im.height) if height else (width / im.width)
        return im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), LANCZOS)

    def _make_cta(self, assets):
        W = self.W
        img = self._load_asset(assets, "play_badge.png", width=W * 0.56)
        label = self.p.cta.strip()
        if img is None:
            if not label:
                return None
            S = 3
            font = fonts.load_font(self.s.title_font, int(W * 0.036 * S), True)
            tw = font.getlength(label)
            h = int(W * 0.115 * S)
            icon = h * 0.30
            padx = W * 0.05 * S
            w = int(tw + icon + padx * 2 + W * 0.025 * S)
            pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(pill)
            d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h / 2, fill=(255, 255, 255, 255))
            x0, cy = padx, h / 2
            d.polygon([(x0, cy - icon * 0.62), (x0, cy + icon * 0.62), (x0 + icon, cy)], fill=R.rgb(self.s.glow_color, (60, 120, 240)))
            fonts.draw_text(d, (x0 + icon + W * 0.025 * S, cy), label, font, (16, 18, 28, 255), "lm", pill)
            img = pill.resize((w // S, h // S), LANCZOS)
        m = int(W * 0.04)  # room for a soft shadow
        out = Image.new("RGBA", (img.width + 2 * m, img.height + 2 * m), (0, 0, 0, 0))
        sh = Image.new("L", out.size, 0)
        sh.paste(img.getchannel("A"), (m, m + int(W * 0.012)))
        out.paste((0, 0, 0), (0, 0), sh.filter(ImageFilter.GaussianBlur(W * 0.014)).point(_lut(45)))
        out.alpha_composite(img, (m, m))
        return out

    # -------------------------------------------------------------------- text
    def _text_for(self, seg):
        """(title, subtitle, shift, style) shown during this scene. Hashable, so equal values mean 'same text'."""
        sc = seg.scene
        if seg.outro:
            return (self.p.app_name, self.p.tagline, self.logo_shift, ())
        if sc.text_effect == "off":
            return ("", "", 0.0, ())
        style = tuple(sorted((k, v) for k, v in (sc.text_style or {}).items() if k in TEXT_STYLE_KEYS))
        title = sc.title if sc.title is not None else self.s.title
        subtitle = sc.subtitle if sc.subtitle is not None else self.s.subtitle
        shift = 0.0
        if is_blank(sc.image):  # no phone: centre the text block vertically ("phone Y offset" nudges it)
            shift = self._center_shift(title, subtitle, style) + sc.phone_dy
        return (title, subtitle, shift, style)

    def _center_shift(self, title, subtitle, style):
        """How far (in % of the height) the text must move from its normal place to sit in the middle of the canvas."""
        key = (title, subtitle, style)
        if key not in self._cs:
            if len(self._cs) > 40:
                self._cs.clear()
            blocks, _ = R.layout_text(self._settings_for(title, subtitle, 0.0, style), self.W, self.H)
            spans = [(y, y + sum(font.getmetrics())) for b in blocks for _x, y, _line, font in b["items"]]
            self._cs[key] = ((self.H - (max(e for _, e in spans) - min(s for s, _ in spans))) / 2
                             - min(s for s, _ in spans)) / self.H * 100 if spans else 0.0
        return self._cs[key]

    def _effect(self, seg):
        e = seg.scene.text_effect
        return self.p.text_effect if seg.outro or e in ("", "default") else e

    def _is_static(self, seg):
        """Scenes that keep the previous text (and don't force an effect) just leave it on screen."""
        prev = self.segs[seg.idx - 1] if seg.idx else None
        return prev is not None and seg.scene.text_effect == "default" and self._text_for(prev) == self._text_for(seg)

    def _settings_for(self, title, subtitle, shift, style):
        d = {**self.s.to_dict(), **dict(style), "title": title, "subtitle": subtitle}
        d["text_y"] = d["text_y"] + shift
        for key, text, bold in (("title_font", title, True), ("subtitle_font", subtitle, False)):
            d[key] = _font_for(d.get(key, "Auto"), text, bold)   # Amharic text finds an Ethiopic font by itself
        return Settings(**d)

    def _text_layers(self, title, subtitle, shift, style):
        key = (title, subtitle, round(shift, 2), style)
        if key not in self._tc:
            if len(self._tc) > 40:      # dragging text creates many layouts: keep memory bounded
                self._tc.clear()
            blocks, _ = R.layout_text(self._settings_for(*key[:2], shift, style), self.W, self.H)
            kinds = [k for k, v in (("title", title), ("subtitle", subtitle)) if v and v.strip()]
            self._tc[key] = list(zip(kinds, R.text_masks(blocks, (self.W, self.H))))
        return self._tc[key]

    def _draw_text(self, canvas, text, a_title, a_sub, dy_title, dy_sub):
        for kind, layer in self._text_layers(*text):
            color, mask = layer
            a, dy = (a_title, dy_title) if kind == "title" else (a_sub, dy_sub)
            if a <= 0.004:
                continue
            emo = getattr(layer, "emoji", None)   # colour emoji of this block
            if a < 0.996:
                mask = mask.point(_lut(round(a * 100)))
                if emo is not None:
                    emo = emo.copy()
                    emo.putalpha(emo.getchannel("A").point(_lut(round(a * 100))))
            canvas.paste(color, (0, int(dy)), mask)
            if emo is not None:
                canvas.paste(emo, (0, int(dy)), emo)

    # ------------------------------------------------------------------ typing
    def _blocks(self, title, subtitle, shift, style):
        """Text laid out once; each line remembers its left edge so letters never shift while typing."""
        key = (title, subtitle, round(shift, 2), style)
        if key not in self._bl:
            if len(self._bl) > 40:
                self._bl.clear()
            blocks, _ = R.layout_text(self._settings_for(title, subtitle, shift, style), self.W, self.H)
            kinds = [k for k, v in (("title", title), ("subtitle", subtitle)) if v and v.strip()]
            out = []
            for kind, b in zip(kinds, blocks):
                items = []
                for x, y, line, font in b["items"]:
                    w = font.getlength(line) if line else 0
                    items.append(({"la": x, "ma": x - w / 2, "ra": x - w}[b["anchor"]], y, line, font))
                out.append(dict(kind=kind, items=items, color=b["color"], opacity=b["opacity"],
                                n=sum(len(i[2]) for i in items)))
            self._bl[key] = out
        return self._bl[key]

    def text_box(self, seg):
        """Pixel box (x0, y0, x1, y1) of a scene's title + subtitle block, or None when it shows no text.
        The editor uses it to let you grab the text with the mouse."""
        if seg.outro:
            return None
        try:
            blocks = self._blocks(*self._text_for(seg))
        except Exception:
            return None
        xs0, ys0, xs1, ys1 = [], [], [], []
        for b in blocks:
            for x, y, line, font in b["items"]:
                if not line:
                    continue
                asc, desc = font.getmetrics()
                xs0.append(x)
                xs1.append(x + font.getlength(line))
                ys0.append(y)
                ys1.append(y + asc + desc)
        return (min(xs0), min(ys0), max(xs1), max(ys1)) if xs0 else None

    def _plan(self, seg):
        """When the title and subtitle are typed (seconds from the start of the scene)."""
        if seg.idx not in self._plans:
            plan, sc = None, seg.scene
            if self._effect(seg) == "typing":
                n = {b["kind"]: b["n"] for b in self._blocks(*self._text_for(seg))}
                nt, ns = n.get("title", 0), n.get("subtitle", 0)
                if nt + ns:
                    v = max(4.0, float(sc.type_speed) if (sc.type_speed > 0 and not seg.outro) else float(self.p.type_speed))
                    d0 = sc.text_delay if (sc.text_delay > 0 and not seg.outro) else (0.35 if seg.outro else (0.55 if seg.idx == 0 else 0.45))
                    gap = 0.18 if nt and ns else 0.0
                    avail = max(0.6, seg.dur * (0.7 if seg.outro else 0.8) - d0)
                    need = nt / v + gap + ns / (v * 1.25)
                    if need > avail:  # scene too short for this speed: type faster so it still finishes
                        v *= need / avail
                    t1 = d0 + nt / v
                    s0 = t1 + gap
                    plan = dict(nt=nt, ns=ns, t0=d0, t1=t1, s0=s0, s1=s0 + ns / (v * 1.25), vt=v, vs=v * 1.25)
            self._plans[seg.idx] = plan
        return self._plans[seg.idx]

    def typing_intervals(self):
        """Absolute (start, end) seconds when text is being typed: drives the typing sound."""
        out = []
        for seg in self.segs:
            if self._is_static(seg) or seg.outro or not seg.scene.typing_sound:
                continue
            plan = self._plan(seg)
            if plan:
                out.append((seg.start + plan["t0"], seg.start + (plan["s1"] if plan["ns"] else plan["t1"])))
        return out

    def tap_times(self):
        """Absolute seconds at which each tap indicator presses (drives the tap sound)."""
        out = []
        for seg in self.segs:
            if seg.outro or is_blank(seg.scene.image):
                continue
            for tp in seg.scene.taps:
                a, b = float(tp["start"]), float(tp["end"])
                if b > a and a < seg.dur:
                    out.append(seg.start + a + (b - a) * 0.22)  # the dot presses ~20-30% into a tap
        return sorted(out)

    def whoosh_times(self):
        return [max(0.0, seg.start - 0.05) for seg in self.segs
                if seg.idx and not seg.outro and seg.scene.transition != "cut" and seg.scene.transition_sound]

    def _draw_typed(self, canvas, seg, lt, text):
        plan = self._plan(seg)
        if plan is None:
            return
        nt, ns = plan["nt"], plan["ns"]
        count = {"title": 0 if (not nt or lt < plan["t0"]) else min(nt, int((lt - plan["t0"]) * plan["vt"]) + 1),
                 "subtitle": 0 if (not ns or lt < plan["s0"]) else min(ns, int((lt - plan["s0"]) * plan["vs"]) + 1)}
        end = plan["s1"] if ns else plan["t1"]
        active = plan["t0"] <= lt < end
        caret = None
        if plan["t0"] - 0.35 <= lt < end + 0.9 and (active or int(lt * 2.4) % 2 == 0):  # solid while typing, then blinks
            caret = "subtitle" if ns and (lt >= plan["s0"] or not nt) else "title"
        for b in self._blocks(*text):
            mask = Image.new("L", canvas.size, 0)
            d = ImageDraw.Draw(mask)
            emo = Image.new("RGBA", canvas.size, (0, 0, 0, 0)) if any(fonts.has_emoji(it[2]) for it in b["items"]) else None
            left = count[b["kind"]]
            last = first = None
            for x, y, line, font in b["items"]:
                first = first or (x, y, font)
                if left <= 0:
                    break
                part = line[:left]
                left -= len(line)
                if part:
                    fonts.draw_text(d, (x, y), part, font, 255, "la", emo)
                    last = (x + font.getlength(part), y, font)
            if caret == b["kind"] and (last or first):
                cx, cy, font = last or first
                asc, desc = font.getmetrics()
                bw = max(2, round(font.size * 0.055))
                d.rectangle((cx + bw * 0.4, cy + (asc + desc) * 0.1, cx + bw * 1.4, cy + (asc + desc) * 0.92), fill=255)
            if b["opacity"] < 100:
                mask = mask.point(_lut(b["opacity"]))
                if emo is not None:
                    emo.putalpha(emo.getchannel("A").point(_lut(b["opacity"])))
            canvas.paste(b["color"], (0, 0), mask)
            if emo is not None:
                canvas.paste(emo, (0, 0), emo)

    # ------------------------------------------------------------------ phones
    def _seg_at(self, t):
        for seg in self.segs:
            if t < seg.start + seg.dur:
                return seg
        return self.segs[-1]

    @staticmethod
    def _pose(seg, lt):
        sc = seg.scene  # per-scene base placement, plus the animated motion
        base = A.Pose(x=sc.phone_dx, y=sc.phone_dy, scale=sc.phone_scale / 100, rot=sc.phone_rot)
        return base.combine(A.motion_pose(sc.motion, lt / seg.dur, lt, seg.dur))

    def _shot_key(self, scene):
        o, s = scene.shot or {}, self.s
        return (scene.image, o.get("shot_fit", s.shot_fit), o.get("shot_zoom", s.shot_zoom),
                o.get("shot_pan_x", s.shot_pan_x), o.get("shot_pan_y", s.shot_pan_y))

    def _screen_img(self, scene):
        key = self._shot_key(scene)
        if key not in self._screens:
            self._screens[key] = R.fit_screenshot(self.images[scene.image], self.screen_size, *key[1:])
        return self._screens[key]

    # ------------------------------------------------------------------ video scenes
    def _ffmpeg(self):
        if self._ff is None:
            self._ff = find_ffmpeg()
        return self._ff

    def _media_path(self, scene):
        return resolve_image(self.raw_dir or ".", scene.image)

    def _clip_for(self, scene):
        """(Clip, loop) for a video scene. The Clip is shared and cached, so rebuilding the renderer is cheap."""
        k = id(scene)
        if k not in self._cl:
            path, o = self._media_path(scene), C.opts(scene.clip)
            info = C.probe(self._ffmpeg(), path)
            width = -(-int(self.screen_size[0] * 2) // 64) * 64  # 2x the screen: room for zoom, rounded so it re-uses
            self._cl[k] = (C.get_clip(self._ffmpeg(), path, o, info, width, self.clip_fps), bool(o["loop"]))
        return self._cl[k]

    def _poster(self, scene):
        path = str(self._media_path(scene))
        if path not in self._posters:
            try:
                tin = C.span(C.probe(self._ffmpeg(), path), C.opts(scene.clip))[0]
                self._posters[path] = C.grab(self._ffmpeg(), path, tin, max_w=self.screen_size[0] * 2)
            except Exception as exc:
                self.errors.append(f"Could not read {Path(path).name}: {str(exc).strip().splitlines()[0] if str(exc).strip() else 'unknown error'}")
                self._posters[path] = Image.new("RGB", self.screen_size, (24, 26, 36))
        return self._posters[path]

    def _video_screen(self, scene, lt):
        """(frame index, the video frame fitted to the phone screen) at scene-time `lt`."""
        clip, loop = self._clip_for(scene)
        if clip.failed and id(clip) not in self._reported:
            self._reported.add(id(clip))
            self.errors.append(f"FFmpeg could not decode {scene.image}: {clip.error_text()[-200:]}")
        idx, frame = clip.frame_at(lt, loop)
        fit = self._shot_key(scene)[1:]
        return idx, R.fit_screenshot(frame if frame is not None else self._poster(scene), self.screen_size, *fit)

    def _screen_for(self, scene, lt):
        return self._video_screen(scene, lt)[1] if is_video(scene.image) else self._screen_img(scene)

    def _video_sprite(self, scene, lt):
        clip = self._clip_for(scene)[0]
        idx, screen = self._video_screen(scene, lt)
        key = (id(clip), idx, self._shot_key(scene)[1:])
        if idx >= 0 and key in self._vs:
            self._vs.move_to_end(key)
            return self._vs[key]
        sprite = self._build_sprite(screen)
        if idx >= 0:  # only real frames are cached (a poster stand-in must not stick)
            self._vs[key] = sprite
            while len(self._vs) > 5:
                self._vs.popitem(last=False)
        return sprite

    def prepare(self, cancel=None):
        """Export: wait until every video clip is fully decoded. False if cancelled."""
        for seg in self.segs:
            if not seg.outro and is_video(seg.scene.image) and not self._clip_for(seg.scene)[0].wait(cancel):
                return False
        return True

    def clip_audio(self):
        """The sound of every video scene as placement info for the audio mix (see video/audio.py)."""
        out = []
        for seg in self.segs:
            sc = seg.scene
            if seg.outro or not is_video(sc.image):
                continue
            o = C.opts(sc.clip)
            if o["mute"] or o["volume"] <= 0:
                continue
            path = self._media_path(sc)
            try:
                info = C.probe(self._ffmpeg(), path)
            except Exception:
                continue
            if not info.has_audio:
                continue
            tin, sp = C.span(info, o)
            dur = min(sp / o["speed"], seg.dur)  # never longer than its scene
            if dur >= 0.05:
                out.append(dict(path=str(path), ss=tin, span=dur * o["speed"], speed=o["speed"],
                                vol=o["volume"], start=seg.start, dur=dur))
        return out

    def _build_sprite(self, screen):
        sprite = self.body.copy()
        layer = screen.convert("RGBA")
        layer.putalpha(self.mask_crop)
        sprite.alpha_composite(layer, dest=self.screen_pos)
        sprite.alpha_composite(self.fa.overlay)
        return sprite

    def _sprite(self, scene, lt=0.0):  # screenshots: built once per crop; videos: one sprite per decoded frame
        if is_video(scene.image):
            return self._video_sprite(scene, lt)
        key = self._shot_key(scene)
        if key not in self._sprites:
            self._sprites[key] = self._build_sprite(self._screen_img(scene))
        return self._sprites[key]

    def _tap_sprite(self, scene, lt):
        """The scene's screen with every tap that is active at scene-time `lt` drawn on it (or None)."""
        sw, sh = self.screen_size
        screen = None
        for color, mask, pos in callouts.layers(self.screen_size, getattr(scene, "callouts", None), lt):
            if screen is None:  # highlights go under the tap indicators
                screen = self._screen_for(scene, lt).copy()
            screen.paste(color, pos, mask)
        for tp in scene.taps:
            a, b = float(tp["start"]), float(tp["end"])
            if b <= a or not a <= lt <= b:
                continue
            patch = A.touch_patch(sw * 0.075, (lt - a) / (b - a))
            if patch is None:
                continue
            if screen is None:
                screen = self._screen_for(scene, lt).copy()
            screen.paste((255, 255, 255), (int(tp["x"] * sw - patch.width / 2), int(tp["y"] * sh - patch.height / 2)),
                         patch.getchannel("A"))
        return self._build_sprite(screen) if screen is not None else None

    def _scene_sprite(self, scene, lt):
        """The phone sprite for a scene at scene-time `lt`: with tap indicators when a tap is active."""
        return self._tap_sprite(scene, lt) or self._sprite(scene, lt)

    def screen_point(self, t, fx, fy):
        """Frame pixel (fx, fy) -> (x, y) as fractions of the phone screen at time t. Used to pick a tap position."""
        t = min(max(t, 0.0), self.total - 1e-4)
        seg = self._seg_at(t)
        lt = t - seg.start
        pose = self._pose(seg, lt)
        if seg.idx == 0:
            pose = pose.combine(A.intro_pose(lt))
        else:
            tdur = seg.scene.transition_duration if seg.scene.transition_duration > 0 else self.p.transition_duration
            if lt < tdur and seg.scene.transition != "cut":
                tr = T.TRANSITIONS.get(seg.scene.transition) or T.TRANSITIONS["slide_left"]
                pose = pose.combine(tr.inn(tr.ease(lt / tdur)))
        cx, cy = self.cx0 + pose.x / 100 * self.W, self.cy0 + pose.y / 100 * self.H
        th = math.radians(pose.rot)
        dx, dy = fx - cx, fy - cy
        u = (dx * math.cos(th) - dy * math.sin(th)) / pose.scale + self.fa.size[0] / 2
        v = (dx * math.sin(th) + dy * math.cos(th)) / pose.scale + self.fa.size[1] / 2
        sw, sh = self.screen_size
        x, y = (u - self.screen_pos[0]) / sw, (v - self.screen_pos[1]) / sh
        return min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)

    def _draw_phone(self, canvas, sprite, pose, sheen=None):
        W, H, sc = self.W, self.H, pose.scale
        if pose.alpha <= 0.004:
            return
        cx, cy = self.cx0 + pose.x / 100 * W, self.cy0 + pose.y / 100 * H
        sw, sh = sprite.size
        sn, cs = abs(math.sin(math.radians(pose.rot))), abs(math.cos(math.radians(pose.rot)))
        hw, hh = (sw * cs + sh * sn) * sc / 2, (sh * cs + sw * sn) * sc / 2
        if cx + hw < 0 or cx - hw > W or cy + hh < 0 or cy - hh > H:  # completely off screen
            return
        if abs(sc - 1) > 1e-3:
            sprite = sprite.resize((max(2, round(sw * sc)), max(2, round(sh * sc))), BICUBIC)
        if abs(pose.rot) > 0.02:
            sprite = R._rotate(sprite, pose.rot, R.rgb(self.s.frame_color, (21, 23, 28)))
        if pose.alpha < 0.999:
            sprite = sprite.copy()
            sprite.putalpha(sprite.getchannel("A").point(_lut(round(pose.alpha * 100))))
        k = self.s.phone_shadow / 100
        if k > 0:  # shadow from the phone's current silhouette, at 1/4 size for speed
            q = 4
            a = sprite.getchannel("A")
            small = a.resize((max(1, a.width // q), max(1, a.height // q)), Image.Resampling.BILINEAR)
            bw = self.body_w * sc / q
            mask, _ = R._combined_shadow(small, [(bw * 0.055, bw * 0.07, 0.55 * k), (bw * 0.012, bw * 0.012, 0.45 * k)])
            mask = mask.resize((mask.width * q, mask.height * q), BICUBIC)
            canvas.paste((0, 0, 0), (round(cx - mask.width / 2), round(cy - mask.height / 2)), mask)
        pos = (round(cx - sprite.width / 2), round(cy - sprite.height / 2))
        canvas.paste(sprite, pos, sprite)
        if sheen is not None:  # light sweep across the phone
            canvas.paste((255, 255, 255), pos, self.fx.sheen_mask(sprite.getchannel("A"), sheen))

    # ------------------------------------------------------------------- frame
    def frame(self, t):
        s, W, H = self.s, self.W, self.H
        t = min(max(t, 0.0), self.total - 1e-4)
        seg = self._seg_at(t)
        lt = t - seg.start
        scene = seg.scene
        prev = self.segs[seg.idx - 1] if seg.idx else None
        tdur = scene.transition_duration if scene.transition_duration > 0 else self.p.transition_duration
        cur = self._pose(seg, lt)
        in_trans = prev is not None and lt < tdur
        name = scene.transition

        canvas = self.bg.copy()
        self.fx.background(canvas, t)
        if seg.outro:  # the end card is drawn on its own, without the phone
            self._draw_outro(canvas, lt)
            return self._fade(canvas, t)

        intro = A.intro_pose(lt) if prev is None else None
        if self.glow:
            color, g = self.glow
            ga = intro.alpha if intro else 1.0
            if ga < 0.999:
                g = g.point(_lut(round(ga * 100)))
            canvas.paste(color, (round(self.cx0 - g.width / 2), round(self.cy0 - g.height / 2)), g)

        sheen = self.fx.sweep_progress(lt)
        blank, prev_blank = is_blank(scene.image), prev is not None and is_blank(prev.scene.image)
        if prev is None:  # first scene: the phone fades and scales in
            if not blank:
                self._draw_phone(canvas, self._scene_sprite(scene, lt), cur.combine(intro), sheen)
        elif in_trans and name != "cut":  # two phones: the leaving one recedes, the new one arrives on top
            tr = T.TRANSITIONS.get(name) or T.TRANSITIONS["slide_left"]
            x = lt / tdur
            p = tr.ease(x)
            if not prev_blank:
                self._draw_phone(canvas, self._scene_sprite(prev.scene, max(0.0, prev.dur - 1e-3)), self._pose(prev, prev.dur).combine(tr.out(p)))
            if not blank:
                sp = self._scene_sprite(scene, lt)
                if self.fx.on("trail") and 0 < x < 1:  # fake motion blur: faded ghosts of the arriving phone
                    for back, a in ((0.07, 0.22), (0.14, 0.10)):
                        ghost = cur.combine(tr.inn(tr.ease(max(0.0, x - back))))
                        ghost.alpha *= a
                        self._draw_phone(canvas, sp, ghost)
                self._draw_phone(canvas, sp, cur.combine(tr.inn(p)), sheen)
        elif not blank:
            self._draw_phone(canvas, self._scene_sprite(scene, lt), cur, sheen)

        self.fx.foreground(canvas)  # vignette: after the phones, before the text

        cur_text, effect = self._text_for(seg), self._effect(seg)
        ease = lambda delay: A.ease_out(A.clamp01((lt - delay) / 0.55))
        if self._is_static(seg):
            self._draw_text(canvas, cur_text, 1, 1, 0, 0)
        else:
            if prev is not None and lt < 0.3:  # old text leaves while the new one arrives
                xx = A.ease_in(lt / 0.3)
                self._draw_text(canvas, self._text_for(prev), 1 - xx, 1 - xx, -xx * H * 0.02, -xx * H * 0.02)
            if effect == "typing":
                self._draw_typed(canvas, seg, lt, cur_text)
            elif effect == "static":
                if lt >= (0.3 if prev is not None else 0.0):
                    self._draw_text(canvas, cur_text, 1, 1, 0, 0)
            elif effect != "off":  # fade & slide
                d1 = scene.text_delay if scene.text_delay > 0 else (0.35 if prev is None else 0.2)
                et, es = ease(d1), ease(d1 + 0.15)
                self._draw_text(canvas, cur_text, et, es, (1 - et) * H * 0.03, (1 - es) * H * 0.03)

        return self._fade(canvas, t)

    def _fade(self, canvas, t):
        self.fx.grain(canvas, t)
        f = min(A.smooth(t / 0.3), A.smooth((self.total - t) / 0.45))
        if f < 0.999:
            canvas = Image.blend(Image.new("RGB", canvas.size, (0, 0, 0)), canvas, f)
        return canvas

    # ------------------------------------------------------- premium end card
    def _outro_glow(self):
        if self._og is None:
            n = int(self.W * 1.7)
            lut = [int(((v / 255) ** 2) * 255 * 0.8) for v in range(256)]
            self._og = ImageOps.invert(Image.radial_gradient("L")).point(lut).resize((n, n), BICUBIC)
        return self._og

    def _lay(self, key):
        """(dx px, dy px, scale) the end card element `key` was moved / resized to in the editor."""
        d = (getattr(self.p, "outro_layout", None) or {}).get(key) or {}
        try:
            return (float(d.get("dx", 0.0)) / 100 * self.W, float(d.get("dy", 0.0)) / 100 * self.H,
                    min(4.0, max(0.2, float(d.get("scale", 1.0)))))
        except (TypeError, ValueError):
            return 0.0, 0.0, 1.0

    def _ofont(self, which, text, bold):
        """Font name for end card text: the end card font if one is chosen, else the look's title / subtitle font."""
        f = getattr(self.p, "outro_font", "") or ""
        if f and f != "Auto":
            return f
        return _font_for(self.s.title_font if which == "title" else self.s.subtitle_font, text, bold)

    def _outro_badge(self):
        """Store badge sized to fit a fixed box; if the PNG is only the icon (or missing) it is wrapped in a pill whose
        two lines of text are yours (any language). A real wide badge PNG is used as it is."""
        p = self.p
        small, big = (getattr(p, "outro_badge_small", "") or "").strip(), (getattr(p, "outro_badge_big", "") or "").strip()
        key = (small, big, getattr(p, "outro_font", ""), self.s.title_font, self.s.subtitle_font)
        if key not in self._ob:
            W, H = self.W, self.H
            raw, a = None, self._assets
            if a and (a / "play_badge.png").exists():
                raw = Image.open(a / "play_badge.png").convert("RGBA")
                bb = raw.getbbox()
                if bb:
                    raw = raw.crop(bb)
            if raw is not None and raw.width / raw.height >= 2.2:  # a real wide badge
                k = min(W * 0.56 / raw.width, H * 0.075 / raw.height)
                badge = raw.resize((max(1, round(raw.width * k)), max(1, round(raw.height * k))), LANCZOS)
            else:  # icon only (or missing): build the pill
                S = 3
                h, w = int(H * 0.07 * S), int(W * 0.58 * S)
                pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                d = ImageDraw.Draw(pill)
                d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h * 0.28, fill=(8, 9, 12, 255),
                                    outline=(150, 155, 170, 255), width=max(2, int(S * 1.5)))
                x = h * 0.28
                if raw is not None:
                    ih = h * 0.56
                    ic = raw.resize((max(1, round(raw.width * ih / raw.height)), round(ih)), LANCZOS)
                    pill.alpha_composite(ic, (round(x), round((h - ic.height) / 2)))
                    x += ic.width + h * 0.2
                room = w - x - h * 0.28

                def fit(text, which, size, bold):
                    name = self._ofont(which, text, bold)
                    f = fonts.load_font(name, int(size), bold)
                    while size > 8 and f.getlength(text) > room:
                        size *= 0.92
                        f = fonts.load_font(name, int(size), bold)
                    return f

                if small and big:
                    fonts.draw_text(d, (x, h * 0.32), small, fit(small, "sub", h * 0.19, False), (210, 214, 226, 255), "lm", pill)
                    fonts.draw_text(d, (x, h * 0.65), big, fit(big, "title", h * 0.34, True), (255, 255, 255, 255), "lm", pill)
                elif small or big:  # one line: centred vertically and a little bigger
                    t1 = big or small
                    fonts.draw_text(d, (x, h * 0.5), t1, fit(t1, "title", h * 0.36, True), (255, 255, 255, 255), "lm", pill)
                badge = pill.resize((w // S, h // S), LANCZOS)
            m = int(W * 0.04)
            out = Image.new("RGBA", (badge.width + 2 * m, badge.height + 2 * m), (0, 0, 0, 0))
            sh = Image.new("L", out.size, 0)
            sh.paste(badge.getchannel("A"), (m, m + int(W * 0.012)))
            out.paste((0, 0, 0), (0, 0), sh.filter(ImageFilter.GaussianBlur(W * 0.014)).point(_lut(45)))
            out.alpha_composite(badge, (m, m))
            if len(self._ob) > 6:
                self._ob.clear()
            self._ob[key] = (out, m)
        return self._ob[key]

    @staticmethod
    def _fit_font(name, text, size, max_w, bold=True):
        font = fonts.load_font(name, int(size), bold)
        while size > 10 and font.getlength(text) > max_w:
            size *= 0.92
            font = fonts.load_font(name, int(size), bold)
        return font

    @staticmethod
    def _o_text(canvas, text, font, color, cx, cy, a, opacity=100):
        if a <= 0.004 or not text:
            return
        mask = Image.new("L", canvas.size, 0)
        emo = Image.new("RGBA", canvas.size, (0, 0, 0, 0)) if fonts.has_emoji(text) else None
        fonts.draw_text(ImageDraw.Draw(mask), (cx, cy), text, font, 255, "mm", emo)
        canvas.paste(color, (0, 0), mask.point(_lut(round(a * opacity))))
        if emo is not None:
            emo.putalpha(emo.getchannel("A").point(_lut(round(a * opacity))))
            canvas.paste(emo, (0, 0), emo)

    @staticmethod
    def _tbox(font, text, cx, cy):
        l, t, r, b = font.getbbox(text, anchor="mm")
        return (cx + l, cy + t, cx + r, cy + b)

    @staticmethod
    def _union(a, b):
        return b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

    def _draw_outro(self, canvas, lt):
        """End card without the phone: logo, name, tagline, divider, two lines of text and the store badge.
        Every text is yours (any language); every element can be moved and resized (project.outro_layout).
        The pixel box of each element is stored in self.outro_boxes for the editor."""
        s, p, W, H = self.s, self.p, self.W, self.H
        ease = lambda d, dur=0.7: A.ease_out(A.clamp01((lt - d) / dur))
        accent = R.rgb(s.glow_color, (60, 120, 240))
        white = R.rgb(s.title_color, (255, 255, 255))
        soft = R.rgb(s.subtitle_color, (205, 210, 225))
        boxes = {}
        ldx, ldy, lsc = self._lay("logo")
        lcx, lcy = W / 2 + ldx, H * 0.28 + ldy
        bob = math.sin(lt * 1.6) * H * 0.004

        # background glow (slowly pulsing) follows the logo
        g = self._outro_glow()
        ga = ease(0, 1.0) * (0.8 + 0.2 * math.sin(lt * 2.2)) * 0.75
        canvas.paste(accent, (round(lcx - g.width / 2), round(lcy - g.height / 2)), g.point(_lut(round(ga * 100))))

        # floating sparkles
        sp = Image.new("L", canvas.size, 0)
        sd = ImageDraw.Draw(sp)
        fade_in = ease(0.2, 1.0)
        for i in range(16):
            rx = (math.sin(i * 12.9898) * 43758.5) % 1
            ry = (math.sin(i * 78.233) * 12345.6) % 1
            x = W * (0.06 + 0.88 * rx) + math.sin(lt * 0.8 + i) * W * 0.012
            y = (H * (1.02 - ((ry + lt * (0.018 + 0.012 * (i % 3))) % 1) * 1.04))
            tw = 0.5 + 0.5 * math.sin(lt * 2.4 + i * 1.7)
            r = W * (0.003 + 0.004 * ((i * 7) % 5) / 4)
            sd.ellipse((x - r, y - r, x + r, y + r), fill=int(255 * (0.15 + 0.5 * tw) * fade_in))
        canvas.paste(accent, (0, 0), sp)

        # logo + expanding ripples
        lg = self.logo_big
        r = (W * 0.14 if lg is None else max(lg.width, lg.height) / 2) * lsc
        for i in range(2):
            ph = (lt * 0.45 + i * 0.5) % 1
            al = (1 - ph) ** 1.5 * 0.5 * ease(0.5, 0.6)
            if al > 0.01:
                S, rr = 2, r * (1.05 + ph * 0.9)
                n = int(rr * 2 * S) + 8
                m = Image.new("L", (n, n), 0)
                ImageDraw.Draw(m).ellipse((4, 4, n - 4, n - 4), outline=255, width=max(2, int(W * 0.003 * S)))
                m = m.resize((n // S, n // S), LANCZOS).point(_lut(round(al * 100)))
                canvas.paste(accent, (round(lcx - m.width / 2), round(lcy + bob - m.height / 2)), m)
        if lg is not None:
            e = ease(0.1, 0.9)
            k = (0.75 + 0.25 * e) * lsc
            im = lg.resize((max(2, round(lg.width * k)), max(2, round(lg.height * k))), BICUBIC)
            a = im.getchannel("A").point(_lut(round(e * 100)))
            canvas.paste(im, (round(lcx - im.width / 2), round(lcy + bob - im.height / 2)), a)
            boxes["logo"] = (lcx - lg.width * lsc / 2, lcy + bob - lg.height * lsc / 2,
                             lcx + lg.width * lsc / 2, lcy + bob + lg.height * lsc / 2)
        else:
            boxes["logo"] = (lcx - r, lcy + bob - r, lcx + r, lcy + bob + r)

        # app name
        name = (p.app_name or "").strip()
        if name:
            dx, dy, sc = self._lay("name")
            e = ease(0.45)
            f = self._fit_font(self._ofont("title", name, True), name, W * 0.095 * sc, W * 0.94)
            cx, cy = W / 2 + dx, H * 0.445 + dy + (1 - e) * H * 0.02
            self._o_text(canvas, name, f, white, cx, cy, e)
            boxes["name"] = self._tbox(f, name, cx, cy)

        # tagline (wraps; any language)
        tag = (p.tagline or "").strip()
        if tag:
            dx, dy, sc = self._lay("tagline")
            e = ease(0.65)
            tf = fonts.load_font(self._ofont("sub", tag, False), max(6, int(W * 0.038 * sc)), False)
            lines, cur = [], ""
            for word in tag.split():
                t2 = (cur + " " + word).strip()
                if cur and tf.getlength(t2) > W * 0.86:
                    lines.append(cur)
                    cur = word
                else:
                    cur = t2
            lines.append(cur)
            box = None
            for i, ln in enumerate(lines[:4]):
                cx, cy = W / 2 + dx, H * 0.495 + dy + i * W * 0.052 * sc + (1 - e) * H * 0.02
                self._o_text(canvas, ln, tf, soft, cx, cy, e, 80)
                box = self._union(box, self._tbox(tf, ln, cx, cy))
            boxes["tagline"] = box

        # divider
        dx, dy, sc = self._lay("divider")
        e = ease(0.85, 0.6)
        if e > 0.01:
            w, y, cx = W * 0.18 * e * sc, H * 0.575 + dy, W / 2 + dx
            th = max(2, W * 0.004)
            ImageDraw.Draw(canvas).rounded_rectangle((cx - w / 2, y, cx + w / 2, y + th), radius=2, fill=accent)
            boxes["divider"] = (cx - W * 0.09 * sc, y - 2, cx + W * 0.09 * sc, y + th + 2)

        # big line (default "GO DOWNLOAD IT NOW") and small line (default "from the Play Store")
        head = (p.outro_headline or "").strip()
        if head:
            dx, dy, sc = self._lay("headline")
            e = ease(1.0)
            hf = self._fit_font(self._ofont("title", head, True), head, W * 0.058 * sc, W * 0.94)
            cx, cy = W / 2 + dx, H * 0.635 + dy + (1 - e) * H * 0.02
            self._o_text(canvas, head, hf, white, cx, cy, e)
            boxes["headline"] = self._tbox(hf, head, cx, cy)
        subl = (p.outro_subline or "").strip()
        if subl:
            dx, dy, sc = self._lay("subline")
            e = ease(1.15)
            sf = self._fit_font(self._ofont("sub", subl, False), subl, W * 0.04 * sc, W * 0.94, bold=False)
            cx, cy = W / 2 + dx, H * 0.677 + dy + (1 - e) * H * 0.02
            self._o_text(canvas, subl, sf, soft, cx, cy, e, 85)
            boxes["subline"] = self._tbox(sf, subl, cx, cy)

        # store badge, floating gently
        if getattr(p, "outro_badge", True):
            dx, dy, sc = self._lay("badge")
            c, m = self._outro_badge()
            e = ease(1.3)
            if e > 0.004:
                if abs(sc - 1) > 1e-3:
                    c = c.resize((max(2, round(c.width * sc)), max(2, round(c.height * sc))), BICUBIC)
                    m = m * sc
                cx = W / 2 + dx
                cy = H * 0.775 + dy + (1 - e) * H * 0.04 + math.sin(lt * 2.0) * H * 0.004
                pos = (round(cx - c.width / 2), round(cy - c.height / 2))
                canvas.paste(c, pos, c.getchannel("A").point(_lut(round(e * 100))))
                boxes["badge"] = (pos[0] + m, pos[1] + m, pos[0] + c.width - m, pos[1] + c.height - m)
        self.outro_boxes = boxes


# ---------------------------------------------------------------------- FFmpeg
def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("FFmpeg was not found.\n\nInstall it and try again:\n"
                           "  Ubuntu/Debian: sudo apt install ffmpeg\n  macOS: brew install ffmpeg\n"
                           "  Windows: winget install ffmpeg\n(or: pip install imageio-ffmpeg)")


def render_video(design, project, images, out_path, width=None, progress=None, cancel=None, assets_dir=None, fx=None,
                 raw_dir=None):
    """Render the project to an H.264 MP4. Returns True on success, False if cancelled."""
    ffmpeg = find_ffmpeg()
    fr = FrameRenderer(design, project, images, width or project.width, assets_dir, fx, raw_dir=raw_dir)
    if not fr.prepare(cancel):  # decode the video clips first (they are then random-access, exact frames)
        return False
    fps = int(project.fps)
    n = max(1, round(fr.total * fps))
    crf, preset = config.QUALITY.get(project.quality, config.QUALITY["Standard"])
    g = build_graph(project, fr.total, fr.typing_intervals(), fr.whoosh_times(), first=1, clips=fr.clip_audio(),
                    tap_t=fr.tap_times())
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-video_size", f"{fr.W}x{fr.H}", "-framerate", str(fps), "-i", "-"]
    if g:
        cmd += g[0] + ["-filter_complex", g[1], "-map", "0:v", "-map", "[aout]"]
    # Top-quality H.264 for TikTok / Reels / Shorts (they re-encode, so we give them a very clean, sharp source)
    vf = "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic+accurate_rnd+full_chroma_int,format=yuv420p"
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-x264-params", "aq-mode=3:aq-strength=0.9:deblock=-1,-1:bframes=3:ref=4",  # aq-mode 3 = less banding in gradients
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    if crf <= 12:  # cap spikes so every phone decodes it smoothly
        cmd += ["-maxrate", "80M", "-bufsize", "160M"]
    cmd += ["-g", str(fps * 2), "-movflags", "+faststart", "-r", str(fps), "-t", f"{fr.total:.3f}"]
    cmd += ["-c:a", "aac", "-b:a", "256k", "-ar", "44100"] if g else ["-an"]
    cmd.append(str(out_path))

    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
    try:
        for i in range(n):
            if cancel and cancel():
                proc.kill()
                proc.wait()
                Path(out_path).unlink(missing_ok=True)
                return False
            frame = fr.frame(i / fps)
            proc.stdin.write(frame.tobytes())
            if progress:
                progress(i + 1, n, frame)
        proc.stdin.close()
        proc.wait()
    except BrokenPipeError:
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        err.seek(0)
        raise RuntimeError("FFmpeg failed:\n" + err.read().decode("utf-8", "replace")[-1500:])
    return True