"""Premium visual effects: aurora light, floating bokeh, light sweep across the phone, vignette, film grain.
Everything heavy is precomputed once; per frame it is only a few pastes."""
import colorsys
import math
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from video.animations import smooth

BICUBIC, LANCZOS = Image.Resampling.BICUBIC, Image.Resampling.LANCZOS

FX_DEFAULTS = dict(aurora=True, bokeh=True, sweep=True, vignette=True, grain=False, trail=True, intensity=70)
FX_LABELS = [("aurora", "Aurora light behind the phone"), ("bokeh", "Floating light particles"),
             ("sweep", "Light sweep across the phone"), ("trail", "Motion trail on transitions"),
             ("vignette", "Cinematic vignette"), ("grain", "Film grain")]


class Effects:
    def __init__(self, W, H, accent, fx=None):
        self.W, self.H = W, H
        self.opts = {**FX_DEFAULTS, **(fx or {})}
        self.k = max(0.0, min(1.0, float(self.opts["intensity"]) / 100))
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in accent))

        def shifted(dh):
            r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, min(1.0, s * 0.9 + 0.1), max(v, 0.85))
            return int(r * 255), int(g * 255), int(b * 255)

        self.palette = [tuple(accent), shifted(0.08), shifted(-0.09)]
        k = self.k

        self._blobs = []
        if self.opts["aurora"]:
            base = ImageOps.invert(Image.radial_gradient("L"))
            lut = [int(((i / 255) ** 2) * 255 * 0.5 * k) for i in range(256)]
            for i, col in enumerate(self.palette):
                size = int(W * (1.3 - 0.15 * i))
                self._blobs.append((col, base.point(lut).resize((size, size), BICUBIC), i))

        self._bokeh = []
        if self.opts["bokeh"]:
            rnd = random.Random(7)
            for i in range(22):
                r = W * rnd.uniform(0.008, 0.03)
                mix = tuple(int(c * 0.6 + 255 * 0.4) for c in self.palette[i % 3])
                self._bokeh.append(dict(x=rnd.random(), y=rnd.random(), sp=rnd.uniform(0.012, 0.04),
                                        ph=rnd.uniform(0, 6.28), col=mix, mask=self._disk(r, 0.5 * k)))

        self._vig = None
        if self.opts["vignette"]:
            lut = [int(((i / 255) ** 2.4) * 255 * 0.75 * k) for i in range(256)]
            self._vig = Image.radial_gradient("L").resize((W, H), BICUBIC).point(lut)

        self._grain = []
        if self.opts["grain"]:
            lut = [max(0, int((i - 128) * 0.16 * k)) for i in range(256)]
            self._grain = [Image.effect_noise((W, H), 48).point(lut) for _ in range(4)]

    def on(self, name):
        return bool(self.opts.get(name))

    @staticmethod
    def _disk(r, a):
        S = 4
        n = int(r * 2 * S) + 8
        m = Image.new("L", (n, n), 0)
        ImageDraw.Draw(m).ellipse((4, 4, n - 4, n - 4), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(max(1, S * r * 0.25))).resize((max(1, n // S), max(1, n // S)), LANCZOS)
        return m.point(lambda v: int(v * a))

    # ------------------------------------------------------------ layers
    def background(self, canvas, t):
        """Drawn right after the background, behind everything else."""
        W, H = self.W, self.H
        for col, m, i in self._blobs:
            x = W * (0.5 + 0.38 * math.sin(t * (0.35 + 0.11 * i) + i * 2.1))
            y = H * (0.45 + 0.30 * math.sin(t * (0.27 + 0.09 * i) + i * 1.3 + 1))
            canvas.paste(col, (round(x - m.width / 2), round(y - m.height / 2)), m)
        for b in self._bokeh:
            y = H * (1.07 - ((b["y"] + t * b["sp"]) % 1.14))
            x = W * b["x"] + math.sin(t * 0.7 + b["ph"]) * W * 0.015
            m = b["mask"]
            canvas.paste(b["col"], (round(x - m.width / 2), round(y - m.height / 2)), m)

    def foreground(self, canvas):
        """After the phones, before the text."""
        if self._vig is not None:
            canvas.paste((0, 0, 0), (0, 0), self._vig)

    def grain(self, canvas, t):
        if self._grain:
            canvas.paste((255, 255, 255), (0, 0), self._grain[int(t * 24) % len(self._grain)])

    # ------------------------------------------------------- light sweep
    def sweep_progress(self, lt):
        """0..1 while a sweep is running in this scene (starts 0.9 s in, lasts 1.1 s), else None."""
        if not self.opts["sweep"]:
            return None
        p = (lt - 0.9) / 1.1
        return smooth(p) if 0 <= p <= 1 else None

    def sheen_mask(self, alpha, p):
        """Diagonal soft light band, clipped to the phone's silhouette (`alpha` = sprite alpha channel)."""
        w, h = alpha.size
        q = 4
        sw, sh = max(8, w // q), max(8, h // q)
        m = Image.new("L", (sw, sh), 0)
        cx, bw, sk = sw * (-0.35 + 1.7 * p), sw * 0.28, sh * 0.32
        ImageDraw.Draw(m).polygon([(cx - bw / 2 + sk, 0), (cx + bw / 2 + sk, 0), (cx + bw / 2 - sk, sh), (cx - bw / 2 - sk, sh)], fill=255)
        m = m.filter(ImageFilter.GaussianBlur(bw * 0.3)).resize((w, h), BICUBIC)
        peak = int(150 * self.k * math.sin(math.pi * min(max(p, 0.0), 1.0)))
        m = m.point([v * peak // 255 for v in range(256)])
        return ImageChops.multiply(m, alpha)