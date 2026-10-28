"""Trim and crop editor for one video clip.

 * Timeline with a filmstrip: drag the yellow handles to cut the start and the end, drag anywhere else to scrub.
 * Crop box over the frame: drag it to move, drag the handles to resize, pick an aspect ratio to lock it
   ("Match phone screen" gives the exact shape of the phone, so nothing gets cropped again by the frame).
 * Play loops the part you kept, at the chosen speed.
Calls `on_apply({trim_in, trim_out, crop, speed})` when you press Apply.
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from ui import theme
from ui.widgets import Controls
from video import clips as C

TL_H, PAD, MIN_SPAN, HANDLE = 78, 14, 0.2, 7
ASPECTS = {"Free": None, "Match phone screen": "phone", "9:16 portrait": 9 / 16, "16:9 landscape": 16 / 9,
           "1:1 square": 1.0, "4:5": 4 / 5, "3:4": 3 / 4}
YELLOW = "#FFC83D"


class ClipEditor(tk.Toplevel):
    def __init__(self, parent, ffmpeg, path, o, phone_aspect, on_apply):
        super().__init__(parent)
        self.title("Trim and crop video")
        self.geometry("1100x780")
        self.minsize(900, 640)
        self.configure(bg=theme.BG)
        self.transient(parent)
        self.ff, self.path, self.on_apply, self.phone_aspect = ffmpeg, path, on_apply, phone_aspect
        self.info = C.probe(ffmpeg, path)
        D = self.D = self.info.duration
        o = C.opts(o)
        self.t_in = max(0.0, min(float(o["trim_in"]), D - MIN_SPAN))
        self.t_out = D if o["trim_out"] <= 0 else min(float(o["trim_out"]), D)
        self.head = self.t_in
        self.crop = list(o["crop"])                     # x, y, w, h as fractions of the source frame
        self.aspect = None
        self.vars = {"speed": tk.DoubleVar(value=o["speed"])}
        self.asp_var = tk.StringVar(value="Free")
        self.txt = {k: tk.StringVar() for k in ("time", "range", "crop")}
        self.playing, self._t0, self._base = False, 0.0, 0.0
        self._photo, self._frame, self._geom = None, None, (0, 0, 1, 1, 1.0)   # frame image, then (ox, oy, w, h, scale)
        self._drag = None
        self._thumbs, self._thumb_imgs, self._q, self._alive, self._job = [], [], queue.Queue(), True, None
        # low-res decode of the whole clip so scrubbing and playback are instant
        self.pclip = C.get_clip(ffmpeg, path, C.opts({}), self.info, 480, 12)

        side = ttk.Frame(self, width=300)
        side.pack(side="right", fill="y", padx=(0, 14), pady=14)
        side.pack_propagate(False)
        main = ttk.Frame(self, style="Bg.TFrame")
        main.pack(side="left", fill="both", expand=True, padx=14, pady=14)

        self.cv = tk.Canvas(main, bg="#000000", highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.tl = tk.Canvas(main, bg=theme.PANEL, height=TL_H, highlightthickness=0, cursor="hand2")
        self.tl.pack(fill="x", pady=(10, 0))
        bar = ttk.Frame(main, style="Bg.TFrame")
        bar.pack(fill="x", pady=(8, 0))
        self.play_btn = ttk.Button(bar, text="Play", width=8, command=self.toggle)
        self.play_btn.pack(side="left")
        ttk.Button(bar, text="Set start here  [", command=self.set_in).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Set end here  ]", command=self.set_out).pack(side="left", padx=(4, 0))
        ttk.Label(bar, textvariable=self.txt["time"], style="Info.TLabel").pack(side="right")

        ttk.Label(side, text="Trim", style="Section.TLabel").pack(anchor="w")
        ttk.Label(side, textvariable=self.txt["range"], wraplength=270, justify="left").pack(anchor="w", pady=(4, 2))
        ttk.Label(side, text="Drag the yellow handles on the timeline, or press [ and ] while playing.",
                  style="Muted.TLabel", wraplength=270, justify="left").pack(anchor="w")
        c = Controls(self, self.vars)
        c.section(side, "Speed")
        c.slider(side, "Playback speed", "speed", 0.1, 8, 0.05, "×")
        c.section(side, "Crop")
        c.combo(side, "Aspect ratio", None, ASPECTS, var=self.asp_var, on_pick=self._pick_aspect)
        ttk.Label(side, textvariable=self.txt["crop"], style="Muted.TLabel", wraplength=270).pack(anchor="w", pady=(4, 0))
        ttk.Button(side, text="Reset crop", command=self._reset_crop).pack(fill="x", pady=(6, 0))
        foot = ttk.Frame(side)
        foot.pack(side="bottom", fill="x")
        ttk.Button(foot, text="Apply", style="Accent.TButton", command=self.apply).pack(fill="x")
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(fill="x", pady=(6, 0))

        self.cv.bind("<Configure>", lambda e: self._redraw())
        self.tl.bind("<Configure>", lambda e: self._draw_timeline())
        self.cv.bind("<ButtonPress-1>", self._crop_press)
        self.cv.bind("<B1-Motion>", self._crop_move)
        self.cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.cv.bind("<Motion>", self._crop_hover)
        self.tl.bind("<ButtonPress-1>", self._tl_press)
        self.tl.bind("<B1-Motion>", self._tl_move)
        self.tl.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.bind("<space>", lambda e: self.toggle())
        self.bind("[", lambda e: self.set_in())
        self.bind("]", lambda e: self.set_out())
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Left>", lambda e: self._seek(self.head - 1 / 12))
        self.bind("<Right>", lambda e: self._seek(self.head + 1 / 12))
        self.vars["speed"].trace_add("write", lambda *_: self._update_text())

        threading.Thread(target=self._make_filmstrip, daemon=True).start()
        self._update_text()
        self.after(60, self._poll)
        self.after(120, lambda: self._seek(self.head))

    # --------------------------------------------------------------- geometry
    def _tx(self, t):
        w = max(10, self.tl.winfo_width() - 2 * PAD)
        return PAD + t / self.D * w

    def _tt(self, x):
        w = max(10, self.tl.winfo_width() - 2 * PAD)
        return min(max((x - PAD) / w * self.D, 0.0), self.D)

    # ----------------------------------------------------------------- frame
    def _load_frame(self, t):
        _, img = self.pclip.frame_at(t)
        if img is None:  # the background decode has not reached this point yet
            try:
                img = C.grab(self.ff, self.path, t, 640)
            except Exception:
                return
        self._frame = img
        self._redraw()

    def _seek(self, t, grab_delay=0):
        self.head = min(max(t, 0.0), self.D)
        self._update_text()
        self._draw_timeline()
        if grab_delay:
            if self._job:
                self.after_cancel(self._job)
            self._job = self.after(grab_delay, lambda: self._load_frame(self.head))
        else:
            self._load_frame(self.head)

    def _redraw(self):
        cv = self.cv
        cw, ch = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        if self._frame is None or cw < 60 or ch < 60:
            return
        k = min((cw - 20) / self._frame.width, (ch - 20) / self._frame.height)
        w, h = max(2, int(self._frame.width * k)), max(2, int(self._frame.height * k))
        ox, oy = (cw - w) // 2, (ch - h) // 2
        self._geom = (ox, oy, w, h, k)
        x, y, cw_, ch_ = self.crop
        img = self._frame.resize((w, h), Image.Resampling.BILINEAR)
        keep = Image.new("L", (w, h), 0)  # darken everything outside the crop (done in PIL: Tk stipples are not portable)
        ImageDraw.Draw(keep).rectangle((x * w, y * h, (x + cw_) * w, (y + ch_) * h), fill=255)
        self._photo = ImageTk.PhotoImage(Image.composite(img, img.point(lambda v: int(v * 0.3)), keep))
        cv.create_image(ox, oy, anchor="nw", image=self._photo)
        l, t, r, b = ox + x * w, oy + y * h, ox + (x + cw_) * w, oy + (y + ch_) * h
        cv.create_rectangle(l, t, r, b, outline=YELLOW, width=2)
        for i in (1, 2):  # rule-of-thirds guides
            cv.create_line(l + (r - l) * i / 3, t, l + (r - l) * i / 3, b, fill=YELLOW, dash=(2, 5))
            cv.create_line(l, t + (b - t) * i / 3, r, t + (b - t) * i / 3, fill=YELLOW, dash=(2, 5))
        for hx, hy in self._handles(l, t, r, b).values():
            cv.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill=YELLOW, outline="#000000")

    @staticmethod
    def _handles(l, t, r, b):
        mx, my = (l + r) / 2, (t + b) / 2
        return {"tl": (l, t), "t": (mx, t), "tr": (r, t), "r": (r, my), "br": (r, b), "b": (mx, b), "bl": (l, b), "l": (l, my)}

    # ------------------------------------------------------------------ crop
    def _hit(self, ev):
        ox, oy, w, h, _ = self._geom
        x, y, cw, ch = self.crop
        l, t, r, b = ox + x * w, oy + y * h, ox + (x + cw) * w, oy + (y + ch) * h
        for name, (hx, hy) in self._handles(l, t, r, b).items():
            if abs(ev.x - hx) <= 9 and abs(ev.y - hy) <= 9:
                return name
        return "move" if l <= ev.x <= r and t <= ev.y <= b else None

    _CURSORS = {"tl": "top_left_corner", "br": "bottom_right_corner", "tr": "top_right_corner", "bl": "bottom_left_corner",
                "t": "sb_v_double_arrow", "b": "sb_v_double_arrow", "l": "sb_h_double_arrow", "r": "sb_h_double_arrow",
                "move": "fleur", None: ""}

    def _crop_hover(self, ev):
        if self._drag is None:
            self.cv.config(cursor=self._CURSORS[self._hit(ev)])

    def _crop_press(self, ev):
        mode = self._hit(ev)
        if mode:
            self._drag = ("crop", mode, ev.x, ev.y, list(self.crop))

    def _crop_move(self, ev):
        if not self._drag or self._drag[0] != "crop":
            return
        _, mode, x0, y0, (cx, cy, cw, ch) = self._drag
        _, _, w, h, _ = self._geom
        dx, dy = (ev.x - x0) / w, (ev.y - y0) / h
        if mode == "move":
            self.crop = [min(max(cx + dx, 0), 1 - cw), min(max(cy + dy, 0), 1 - ch), cw, ch]
        else:
            l, t, r, b = cx, cy, cx + cw, cy + ch
            l, r = (l + dx, r) if "l" in mode else ((l, r + dx) if "r" in mode else (l, r))
            t, b = (t + dy, b) if "t" in mode else ((t, b + dy) if "b" in mode else (t, b))
            if self.aspect:  # keep the pixel aspect ratio w/h = aspect
                vw, vh = self.info.width, self.info.height
                if mode in ("l", "r"):
                    nh = (r - l) * vw / self.aspect / vh
                    mid = (cy + cy + ch) / 2
                    t, b = mid - nh / 2, mid + nh / 2
                elif mode in ("t", "b"):
                    nw = (b - t) * vh * self.aspect / vw
                    mid = (cx + cx + cw) / 2
                    l, r = mid - nw / 2, mid + nw / 2
                else:
                    nh = (r - l) * vw / self.aspect / vh
                    t, b = (b - nh, b) if "t" in mode else (t, t + nh)
                if l < 0 or t < 0 or r > 1 or b > 1:
                    return  # would leave the picture: stay where we were
            else:
                l, t, r, b = max(l, 0), max(t, 0), min(r, 1), min(b, 1)
            if r - l < 0.05 or b - t < 0.05:
                return
            self.crop = [l, t, r - l, b - t]
        self._redraw()
        self._update_text()

    def _pick_aspect(self, name):
        a = ASPECTS[name]
        self.aspect = self.phone_aspect if a == "phone" else a
        if self.aspect:
            self._fit_aspect()

    def _fit_aspect(self):
        """Biggest box with the locked aspect ratio, centred on the current crop."""
        vw, vh, a = self.info.width, self.info.height, self.aspect
        w, h = (a * vh / vw, 1.0) if vw / vh >= a else (1.0, vw / (a * vh))
        mx, my = self.crop[0] + self.crop[2] / 2, self.crop[1] + self.crop[3] / 2
        self.crop = [min(max(mx - w / 2, 0), 1 - w), min(max(my - h / 2, 0), 1 - h), w, h]
        self._redraw()
        self._update_text()

    def _reset_crop(self):
        self.crop = [0.0, 0.0, 1.0, 1.0]
        self.aspect = None
        self.asp_var.set("Free")
        self._redraw()
        self._update_text()

    # -------------------------------------------------------------- timeline
    def _make_filmstrip(self):  # background thread: only talks to the UI through the queue
        n = 14
        for k in range(n):
            if not self._alive:
                return
            try:
                self._q.put(("thumb", k, C.grab(self.ff, self.path, (k + 0.5) / n * self.D, 160)))
            except Exception:
                pass

    def _poll(self):
        if not self._alive:
            return
        try:
            while True:
                kind, k, img = self._q.get_nowait()
                tile = img.resize((max(1, int(img.width * (TL_H - 24) / img.height)), TL_H - 24), Image.Resampling.BILINEAR)
                self._thumbs.append((k, tile))
        except queue.Empty:
            pass
        else:
            self._draw_timeline()
        self.after(150, self._poll)

    def _draw_timeline(self):
        cv, W = self.tl, self.tl.winfo_width()
        cv.delete("all")
        if W < 60:
            return
        y0, y1 = 10, TL_H - 14
        cv.create_rectangle(PAD, y0, W - PAD, y1, fill=theme.CARD, outline="")
        self._thumb_imgs = []
        n, span_w = 14, W - 2 * PAD
        for k, tile in sorted(self._thumbs):
            ph = ImageTk.PhotoImage(tile)
            self._thumb_imgs.append(ph)
            x = PAD + k / n * span_w
            cv.create_image(x, y0, anchor="nw", image=ph)
        xi, xo = self._tx(self.t_in), self._tx(self.t_out)
        for a, b in ((PAD, xi), (xo, W - PAD)):  # trimmed-away parts
            if b > a:
                cv.create_rectangle(a, y0, b, y1, fill=theme.BG, outline="")
        cv.create_rectangle(xi, y0 - 2, xo, y1 + 2, outline=YELLOW, width=2)
        for x in (xi, xo):
            cv.create_rectangle(x - 4, y0 - 4, x + 4, y1 + 4, fill=YELLOW, outline="")
        hx = self._tx(self.head)
        cv.create_line(hx, 2, hx, TL_H - 4, fill="#FF4D6D", width=2)
        cv.create_polygon(hx - 6, 2, hx + 6, 2, hx, 11, fill="#FF4D6D", outline="")

    def _tl_press(self, ev):
        xi, xo = self._tx(self.t_in), self._tx(self.t_out)
        if abs(ev.x - xi) <= HANDLE:
            self._drag = ("in",)
        elif abs(ev.x - xo) <= HANDLE:
            self._drag = ("out",)
        else:
            self._drag = ("head",)
        self._tl_move(ev)

    def _tl_move(self, ev):
        if not self._drag or self._drag[0] == "crop":
            return
        t, what = self._tt(ev.x), self._drag[0]
        if what == "in":
            self.t_in = min(t, self.t_out - MIN_SPAN)
            self._seek(self.t_in, grab_delay=40)
        elif what == "out":
            self.t_out = max(t, self.t_in + MIN_SPAN)
            self._seek(self.t_out, grab_delay=40)
        else:
            self._seek(t, grab_delay=25)
        self._update_text()

    def set_in(self):
        self.t_in = min(self.head, self.t_out - MIN_SPAN)
        self._after_trim()

    def set_out(self):
        self.t_out = max(self.head, self.t_in + MIN_SPAN)
        self._after_trim()

    def _after_trim(self):
        self._update_text()
        self._draw_timeline()

    # ------------------------------------------------------------- playback
    def toggle(self):
        if self.playing:
            self.playing = False
            self.play_btn.config(text="Play")
            return
        if not self.t_in <= self.head < self.t_out - 0.05:
            self.head = self.t_in
        self.playing, self._t0, self._base = True, time.time(), self.head
        self.play_btn.config(text="Pause")
        self._tick()

    def _tick(self):
        if not self.playing or not self._alive:
            return
        t = self._base + (time.time() - self._t0) * C.opts({"speed": self.vars["speed"].get()})["speed"]
        if t >= self.t_out:  # loop the part that is kept
            self._t0, self._base, t = time.time(), self.t_in, self.t_in
        self.head = t
        self._load_frame(t)
        self._update_text()
        self._draw_timeline()
        self.after(30, self._tick)

    # ------------------------------------------------------------------ misc
    def _update_text(self):
        try:
            speed = C.opts({"speed": self.vars["speed"].get()})["speed"]
        except tk.TclError:
            return
        keep = self.t_out - self.t_in
        self.txt["range"].set(f"Start {self.t_in:.2f} s   ·   End {self.t_out:.2f} s\n"
                              f"Keeps {keep:.2f} s of {self.D:.2f} s  →  plays for {keep / speed:.2f} s")
        self.txt["time"].set(f"{self.head:5.2f} / {self.D:.2f} s")
        x, y, w, h = self.crop
        pw, ph = int(self.info.width * w), int(self.info.height * h)
        self.txt["crop"].set(f"Crop: {pw}×{ph} px" + ("" if (w, h) != (1.0, 1.0) else "  (whole picture)"))

    def apply(self):
        speed = round(float(self.vars["speed"].get()), 2)
        self.on_apply({"trim_in": round(self.t_in, 3), "trim_out": 0.0 if self.t_out >= self.D - 0.02 else round(self.t_out, 3),
                       "crop": [round(v, 4) for v in self.crop], "speed": speed})
        self.destroy()

    def destroy(self):
        self._alive = self.playing = False
        if self._job:
            self.after_cancel(self._job)
        super().destroy()