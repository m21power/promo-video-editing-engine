"""Crop a song: see its waveform, choose where it starts and stops (drag the handles or type times), listen to the
cut, then use exactly that part in the video.

    AudioCropper(parent, ffmpeg, path, length, start, end, on_apply)
    on_apply(start, end, fit)   seconds inside the song; `fit` = also make the track on the timeline that long
"""
import array
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ui import theme
from video.audio import Player

RATE = 4000        # waveform analysis rate (Hz, mono): small and fast
BUCKETS = 1600     # waveform resolution
MIN_LEN = 0.2


def fmt(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:04.1f}"


def parse(txt, default):
    """'75', '1:15' or '1:15.5' -> seconds (default when it cannot be read)."""
    txt = str(txt).strip().replace(",", ".")
    try:
        sec = 0.0
        for part in txt.split(":"):
            sec = sec * 60 + float(part)
        return sec
    except ValueError:
        return default


class AudioCropper(tk.Toplevel):
    PAD, H = 12, 200

    def __init__(self, parent, ffmpeg, path, length, start, end, on_apply):
        super().__init__(parent)
        self.title(f"Crop audio · {Path(path).name}")
        self.geometry("960x500")
        self.minsize(760, 440)
        self.configure(bg=theme.BG)
        self.transient(parent)
        self.ff, self.path, self.on_apply = ffmpeg, str(path), on_apply
        self.len = float(length) if length and length > 0 else 0.0
        self.a = max(0.0, float(start))
        self.b = float(end) if end and end > self.a + MIN_LEN else self.len
        self.env, self._drag, self._q = None, None, queue.Queue()
        self._play_t0 = self._play_len = None
        self._pcm_gen = 0
        self.player = Player()
        self.a_txt, self.b_txt = tk.StringVar(value=fmt(self.a)), tk.StringVar(value=fmt(self.b))
        self.info, self.fit = tk.StringVar(value="Reading the song…"), tk.BooleanVar(value=True)

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=16, pady=14)
        ttk.Label(outer, text="Drag the two handles (or the lit area) to choose the part you want. Click anywhere "
                  "outside to move the nearest handle.", style="Muted.TLabel", wraplength=900).pack(anchor="w")
        self.cv = tk.Canvas(outer, height=self.H, bg=theme.BG, highlightthickness=1, highlightbackground=theme.BORDER)
        self.cv.pack(fill="x", pady=(8, 10))
        self.cv.bind("<Configure>", lambda e: self._draw_all())
        self.cv.bind("<Button-1>", self._press)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.cv.bind("<Motion>", self._hover)

        row = ttk.Frame(outer)
        row.pack(fill="x")
        for label, var, which in (("Start", self.a_txt, "a"), ("End", self.b_txt, "b")):
            ttk.Label(row, text=label).pack(side="left", padx=(0, 6))
            e = ttk.Entry(row, textvariable=var, width=9)
            e.pack(side="left", padx=(0, 4))
            e.bind("<Return>", lambda ev, w=which: self._typed(w))
            e.bind("<FocusOut>", lambda ev, w=which: self._typed(w))
            ttk.Button(row, text="−0.1", width=5, command=lambda w=which: self._nudge(w, -0.1)).pack(side="left")
            ttk.Button(row, text="+0.1", width=5, command=lambda w=which: self._nudge(w, 0.1)).pack(side="left", padx=(2, 18))
        ttk.Label(row, textvariable=self.info, style="Value.TLabel").pack(side="left")

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="▶  Play the cut", style="Accent.TButton", command=lambda: self._play(self.a, self.b)).pack(side="left")
        ttk.Button(row, text="▶  Last 3 s (check the ending)", command=lambda: self._play(max(self.a, self.b - 3), self.b)).pack(side="left", padx=6)
        ttk.Button(row, text="■  Stop", command=self._stop).pack(side="left")
        ttk.Button(row, text="Whole song", command=self._whole).pack(side="left", padx=(18, 0))

        ttk.Checkbutton(outer, text="Make this track on the timeline exactly as long as the cut (turns repeat off)",
                        variable=self.fit).pack(anchor="w", pady=(16, 0))
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="Use this cut", style="Accent.TButton", command=self._use).pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))
        self.msg = tk.StringVar()
        ttk.Label(row, textvariable=self.msg, style="Muted.TLabel").pack(side="left")

        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        threading.Thread(target=self._analyse, daemon=True).start()
        self._poll()
        self._refresh_info()

    # ------------------------------------------------------------ waveform
    def _analyse(self):
        try:
            r = subprocess.run([self.ff, "-v", "error", "-i", self.path, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                               capture_output=True)
            raw = r.stdout[: len(r.stdout) // 2 * 2]
            if not raw:
                raise RuntimeError(r.stderr.decode("utf-8", "replace")[-200:] or "no audio found")
            smp = array.array("h")
            smp.frombytes(raw)
            if sys.byteorder == "big":
                smp.byteswap()
            bs = max(1, len(smp) // BUCKETS)
            env = []
            for i in range(0, len(smp) - bs + 1, bs):
                ch = smp[i:i + bs]
                env.append(max(max(ch), -min(ch)) / 32768.0)
            self._q.put(("wave", env, len(smp) / RATE))
        except Exception as exc:
            self._q.put(("err", str(exc)))

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "wave":
                    self.env = msg[1]
                    if not self.len or abs(self.len - msg[2]) > 1.5:
                        self.len = msg[2]
                    if self.b <= self.a + MIN_LEN or self.b > self.len:
                        self.b = self.len
                    self._sync_text()
                    self._draw_all()
                elif msg[0] == "err":
                    self.info.set("Could not read the song")
                    self.msg.set(msg[1][:120])
                elif msg[0] == "pcm" and msg[1] == self._pcm_gen:
                    if not self.player.available:
                        self.msg.set("Listening needs pygame:  pip install pygame")
                    else:
                        self.player.play(msg[2], 0)
                        self._play_t0, self._play_len = time.time(), msg[3]
        except queue.Empty:
            pass
        if self._play_t0 is not None:
            if time.time() - self._play_t0 > self._play_len:
                self._play_t0 = None
            self._draw_head()
        self.after(40, self._poll)

    # -------------------------------------------------------------- drawing
    def _total(self):
        return max(self.len, self.b, 1.0)

    def _x(self, t):
        w = max(self.cv.winfo_width(), 200)
        return self.PAD + max(0.0, min(t, self._total())) / self._total() * (w - 2 * self.PAD)

    def _t(self, x):
        w = max(self.cv.winfo_width(), 200)
        return max(0.0, min(self._total(), (x - self.PAD) / (w - 2 * self.PAD) * self._total()))

    def _draw_all(self):
        cv = self.cv
        cv.delete("all")
        w, h = max(cv.winfo_width(), 200), self.H
        mid = h / 2 + 6
        total = self._total()
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300) if s / total * (w - 2 * self.PAD) >= 60), 600)
        t = 0.0
        while t <= total:
            x = self._x(t)
            cv.create_line(x, 0, x, h, fill=theme.BORDER)
            cv.create_text(x + 3, 8, text=fmt(t).split(".")[0], anchor="w", fill=theme.MUTED, font=(theme.FAMILY, 8))
            t += step
        if self.env:
            n = len(self.env)
            for px in range(int(self.PAD), int(w - self.PAD)):
                v = self.env[min(n - 1, int((px - self.PAD) / (w - 2 * self.PAD) * n))]
                amp = max(1.0, v * (h / 2 - 22) * 1.9)
                cv.create_line(px, mid - amp, px, mid + amp, fill="#5B6490", tags="wave")
        else:
            cv.create_text(w / 2, h / 2, text="Reading the song…", fill=theme.MUTED)
        self._draw_sel()

    def _draw_sel(self):
        cv = self.cv
        cv.delete("sel")
        w, h = max(cv.winfo_width(), 200), self.H
        xa, xb = self._x(self.a), self._x(self.b)
        cv.create_rectangle(self.PAD, 16, xa, h, fill=theme.BG, stipple="gray50", outline="", tags="sel")
        cv.create_rectangle(xb, 16, w - self.PAD, h, fill=theme.BG, stipple="gray50", outline="", tags="sel")
        cv.create_rectangle(xa, 16, xb, h - 1, outline=theme.ACCENT, width=2, tags="sel")
        for x in (xa, xb):
            cv.create_rectangle(x - 4, h / 2 - 20, x + 4, h / 2 + 20, fill=theme.ACCENT_SOFT, outline="#FFFFFF", tags="sel")
        self._draw_head()

    def _draw_head(self):
        self.cv.delete("head")
        if self._play_t0 is not None:
            t = self.a + min(time.time() - self._play_t0, self._play_len) + self._play_from_offset()
            self.cv.create_line(self._x(t), 0, self._x(t), self.H, fill="#FF5A5A", width=2, tags="head")

    def _play_from_offset(self):
        return getattr(self, "_play_off", 0.0)

    # --------------------------------------------------------------- mouse
    def _hover(self, ev):
        xa, xb = self._x(self.a), self._x(self.b)
        near = abs(ev.x - xa) <= 8 or abs(ev.x - xb) <= 8
        self.cv.config(cursor="sb_h_double_arrow" if near else ("fleur" if xa < ev.x < xb else "arrow"))

    def _press(self, ev):
        xa, xb = self._x(self.a), self._x(self.b)
        if abs(ev.x - xa) <= 8:
            mode = "a"
        elif abs(ev.x - xb) <= 8:
            mode = "b"
        elif xa < ev.x < xb:
            mode = "move"
        else:
            mode = "a" if abs(ev.x - xa) < abs(ev.x - xb) else "b"
            self._set(mode, self._t(ev.x))
        self._drag = dict(mode=mode, x=ev.x, a=self.a, b=self.b)

    def _move(self, ev):
        d = self._drag
        if not d:
            return
        w = max(self.cv.winfo_width(), 200)
        dt = (ev.x - d["x"]) / (w - 2 * self.PAD) * self._total()
        if d["mode"] == "move":
            span = d["b"] - d["a"]
            a = min(max(0.0, d["a"] + dt), self._total() - span)
            self.a, self.b = round(a, 1), round(a + span, 1)
            self._sync_text()
            self._draw_sel()
        else:
            self._set(d["mode"], self._t(ev.x))

    def _set(self, which, t):
        t = round(t, 1)
        if which == "a":
            self.a = min(max(0.0, t), self.b - MIN_LEN)
        else:
            self.b = max(min(self._total(), t), self.a + MIN_LEN)
        self._sync_text()
        self._draw_sel()

    def _sync_text(self):
        self.a_txt.set(fmt(self.a))
        self.b_txt.set(fmt(self.b))
        self._refresh_info()

    def _refresh_info(self):
        if self.len:
            self.info.set(f"Length {fmt(self.b - self.a)}   ·   song {fmt(self.len)}")

    def _typed(self, which):
        var = self.a_txt if which == "a" else self.b_txt
        self._set(which, parse(var.get(), self.a if which == "a" else self.b))

    def _nudge(self, which, d):
        self._set(which, (self.a if which == "a" else self.b) + d)

    def _whole(self):
        self.a, self.b = 0.0, self.len or self.b
        self._sync_text()
        self._draw_sel()

    # ------------------------------------------------------------ listening
    def _play(self, a, b):
        self._stop()
        if b - a < 0.1:
            return
        self._pcm_gen += 1
        gen, self._play_off = self._pcm_gen, a - self.a
        self.msg.set("Preparing…")

        def work():
            r = subprocess.run([self.ff, "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", self.path, "-vn",
                                "-f", "s16le", "-ar", "44100", "-ac", "2", "-"], capture_output=True)
            if r.stdout:
                self._q.put(("pcm", gen, r.stdout, b - a))

        threading.Thread(target=work, daemon=True).start()
        self.after(500, lambda: self.msg.set(""))

    def _stop(self):
        self._pcm_gen += 1
        self._play_t0 = None
        self.player.stop()
        self._draw_head()

    # ----------------------------------------------------------------- done
    def _use(self):
        self._typed("a")
        self._typed("b")
        self._stop()
        self.on_apply(round(self.a, 2), round(self.b, 2), bool(self.fit.get()))
        self.destroy()

    def destroy(self):
        try:
            self.player.stop()
        except Exception:
            pass
        super().destroy()