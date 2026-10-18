"""Promo video studio: scene list, live scrub/play preview, MP4 export."""
import json
import queue
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import config
from ui import theme
from ui.widgets import Controls, ScrollFrame
from video import animations as A
from video import premium  # noqa: F401  (registers the premium animations; must come before the combos are built)
from video import transitions as T
from video.audio import Player, has_audio, render_pcm
from video.engine import FrameRenderer, find_ffmpeg, load_image, render_video, resolve_image
from video.fx import FX_DEFAULTS, FX_LABELS
from video.picker import AnimationPicker
from video.timeline import Project, make_scene

PREVIEW_W = 420
TEXT_STYLE = ("title_size", "subtitle_size", "text_y", "text_x", "text_align", "title_color", "subtitle_color")
EFFECTS = ["default", "typing", "fade", "static", "off"]
IMG_TYPES = [("Images", "*.png *.jpg *.jpeg *.webp")]


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


class VideoDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.paths = app, app.paths
        self.title("Promo video studio")
        self.geometry("1500x920")
        self.minsize(1280, 780)
        self.configure(bg=theme.BG)
        self.design = app.collect()
        self.cfg = self.paths.presets / "promo_project.json"
        self.fx_cfg = self.paths.presets / "promo_fx.json"
        self.project = self._load_project()
        self.fx = self._load_fx()
        self._picker = None
        self.images, self._fr, self._photo = {}, None, None
        self._playing, self._loading, self._exporting = False, False, False
        self._picking, self._pick_t, self._tap_i, self._shown = False, 0.0, None, None
        self._play_t0 = self._play_base = 0.0
        self._tick_job = None
        self.live = tk.BooleanVar(value=True)
        self.sound = tk.BooleanVar(value=True)
        self.player, self._pcm, self._pcm_key, self._audio_gen, self._warned = Player(), None, None, 0, False
        self.mode = tk.StringVar(value="scene")   # "scene" = loop the selected scene, "all" = whole video
        self._cancel, self._q, self._jobs = threading.Event(), queue.Queue(), {}

        P = self.project
        d, D = self.design, tk.DoubleVar
        self.ev = dict(
            duration=D(value=3.2), motion=tk.StringVar(), transition=tk.StringVar(), transition_duration=D(value=0.0),
            text_effect=tk.StringVar(value="default"), custom=tk.BooleanVar(value=False),
            title=tk.StringVar(), subtitle=tk.StringVar(), type_speed=D(value=0.0), text_delay=D(value=0.0),
            style_on=tk.BooleanVar(value=False), title_size=D(value=d.title_size), subtitle_size=D(value=d.subtitle_size),
            text_y=D(value=d.text_y), text_x=D(value=d.text_x), text_align=tk.StringVar(value=d.text_align),
            title_color=tk.StringVar(value=d.title_color), subtitle_color=tk.StringVar(value=d.subtitle_color),
            phone_scale=D(value=100), phone_dx=D(value=0), phone_dy=D(value=0), phone_rot=D(value=0),
            shot_on=tk.BooleanVar(value=False), shot_fit=tk.StringVar(value=d.shot_fit), shot_zoom=D(value=d.shot_zoom),
            shot_pan_x=D(value=d.shot_pan_x), shot_pan_y=D(value=d.shot_pan_y),
            tap_start=D(value=1.0), tap_end=D(value=2.2), tap_x=D(value=0.5), tap_y=D(value=0.58),
            typing_sound=tk.BooleanVar(value=True), transition_sound=tk.BooleanVar(value=True))
        self.pv = dict(app_name=tk.StringVar(value=P.app_name), tagline=tk.StringVar(value=P.tagline),
                       outro=tk.BooleanVar(value=P.outro), outro_duration=tk.DoubleVar(value=P.outro_duration),
                       cta=tk.StringVar(value=P.cta), fps=tk.StringVar(value=str(P.fps)),
                       width=tk.StringVar(value=str(P.width)),
                       text_effect=tk.StringVar(value=P.text_effect), type_speed=tk.DoubleVar(value=P.type_speed),
                       music=tk.StringVar(value=P.music), music_volume=tk.DoubleVar(value=P.music_volume),
                       music_fade_in=tk.DoubleVar(value=P.music_fade_in), music_fade_out=tk.DoubleVar(value=P.music_fade_out),
                       typing_sound=tk.StringVar(value=P.typing_sound), typing_volume=tk.DoubleVar(value=P.typing_volume),
                       whoosh_sound=tk.StringVar(value=P.whoosh_sound), whoosh_volume=tk.DoubleVar(value=P.whoosh_volume),
                       duck=tk.BooleanVar(value=P.duck))
        self.fxv = {k: (tk.DoubleVar(value=self.fx[k]) if k == "intensity" else tk.BooleanVar(value=bool(self.fx[k])))
                    for k in FX_DEFAULTS}
        self.ec, self.pc, self.fc = Controls(self, self.ev), Controls(self, self.pv), Controls(self, self.fxv)
        self.t_var = tk.DoubleVar(value=0.0)
        self.status = tk.StringVar(value="Live preview: every change plays instantly. Export only when you like it.")

        self._build()
        self._refresh_tree(select=0 if P.scenes else None)
        for v in self.ev.values():
            v.trace_add("write", self._editor_changed)
        for v in self.pv.values():
            v.trace_add("write", self._project_changed)
        for v in self.fxv.values():
            v.trace_add("write", self._fx_changed)
        self.mode.trace_add("write", lambda *_: self._after_change())
        self.sound.trace_add("write", lambda *_: self._audio_start(self._now()) if self._playing else self.player.stop())
        self.after(300, self._refresh_audio)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(350, lambda: self._play_from(0.0) if self.live.get() else self._show(0.9))
        self.after(100, self._poll)

    # ---------------------------------------------------------------- project io
    def _load_project(self):
        if self.cfg.exists():
            try:
                proj = Project.from_dict(json.loads(self.cfg.read_text("utf-8")))
                proj.scenes = [s for s in proj.scenes if resolve_image(self.paths.raw, s.image).exists()]
                if proj.scenes:
                    return proj
            except (OSError, ValueError, TypeError):
                pass
        files = sorted((p for p in self.paths.raw.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")),
                       key=natural_key)
        v = config.VIDEO
        return Project(scenes=[make_scene(i, f.name, v["scene_duration"]) for i, f in enumerate(files)],
                       outro_duration=v["outro_duration"], cta=v["cta"], fps=v["fps"], width=v["width"],
                       transition_duration=v["transition"])

    def _save(self):
        try:
            self.cfg.write_text(json.dumps(self.project.to_dict(), indent=2, ensure_ascii=False), "utf-8")
        except OSError:
            pass

    def _load_fx(self):
        fx = dict(FX_DEFAULTS)
        try:
            fx.update(json.loads(self.fx_cfg.read_text("utf-8")))
        except (OSError, ValueError):
            pass
        return fx

    def _save_fx(self):
        try:
            self.fx_cfg.write_text(json.dumps(self.fx, indent=2), "utf-8")
        except OSError:
            pass

    def _later(self, key, ms, fn):
        if key in self._jobs:
            self.after_cancel(self._jobs[key])
        self._jobs[key] = self.after(ms, fn)

    # -------------------------------------------------------------------- layout
    def _build(self):
        outer = ttk.Frame(self, style="Bg.TFrame")
        outer.pack(fill="both", expand=True)
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w").pack(side="bottom", fill="x")

        left = ttk.Frame(outer, width=450)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ttk.Label(left, text="Scenes", style="Section.TLabel").pack(anchor="w", padx=16, pady=(16, 6))
        tf = ttk.Frame(left)
        tf.pack(fill="x", padx=(12, 0))
        self.tree = ttk.Treeview(tf, columns=("img", "dur", "motion", "tr", "fx"), show="headings", height=6,
                                 selectmode="browse", style="Scenes.Treeview")
        for col, text, w in (("img", "Screenshot", 88), ("dur", "Sec", 36), ("motion", "Motion", 118),
                             ("tr", "Transition", 88), ("fx", "Text", 66)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        for rowdef in ((("Add…", self.add_scenes), ("Add all", self.add_all), ("Duplicate", self.duplicate)),
                       (("Remove", self.remove), ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1)))):
            row = ttk.Frame(left)
            row.pack(fill="x", padx=12, pady=(6, 0))
            for text, cmd in rowdef:
                ttk.Button(row, text=text, command=cmd).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Separator(left).pack(fill="x", padx=16, pady=(12, 0))
        lf = ScrollFrame(left)
        lf.pack(fill="both", expand=True)
        ed = ttk.Frame(lf.inner)
        ed.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        c = self.ec
        c.section(ed, "Timing and motion")
        c.slider(ed, "Duration", "duration", 1.2, 10, 0.1, " s")
        c.combo(ed, "Motion", "motion", A.MOTION_PRESETS)
        ttk.Button(ed, text="Browse and search motions…",
                   command=lambda: self.open_picker("motion")).pack(fill="x", pady=(2, 4))
        c.combo(ed, "Transition into this scene", "transition", T.TRANSITION_NAMES)
        ttk.Button(ed, text="Browse and search transitions…",
                   command=lambda: self.open_picker("transition")).pack(fill="x", pady=(2, 4))
        c.slider(ed, "Transition length (0 = automatic)", "transition_duration", 0, 2, 0.05, " s")
        c.section(ed, "Text")
        c.combo(ed, "Text effect for this scene (default = project setting)", "text_effect", EFFECTS)
        ttk.Button(ed, text="Use this effect for all scenes", command=self.effect_all).pack(fill="x", pady=(2, 4))
        c.check(ed, "Custom text for this scene", "custom")
        c.textbox(ed, "Title", "title", 2)
        c.textbox(ed, "Subtitle", "subtitle", 2)
        c.slider(ed, "Typing speed (0 = project speed)", "type_speed", 0, 40, 1, " chars/s")
        c.slider(ed, "Start delay (0 = automatic)", "text_delay", 0, 3, 0.05, " s")
        c.check(ed, "Custom text style for this scene", "style_on")
        c.segmented(ed, "Alignment", "text_align", ["left", "center", "right"])
        c.slider(ed, "Title size", "title_size", 2, 16, 0.1)
        c.slider(ed, "Subtitle size", "subtitle_size", 1, 9, 0.1)
        c.slider(ed, "Vertical position", "text_y", 0, 70, 0.5, "%")
        c.slider(ed, "Horizontal offset", "text_x", -40, 40, 0.5, "%")
        c.color(ed, "Title color", "title_color")
        c.color(ed, "Subtitle color", "subtitle_color")
        c.section(ed, "Phone in this scene")
        c.slider(ed, "Size", "phone_scale", 50, 140, 1, "%")
        c.slider(ed, "Move horizontally", "phone_dx", -50, 50, 0.5, "%")
        c.slider(ed, "Move vertically", "phone_dy", -40, 60, 0.5, "%")
        c.slider(ed, "Rotation", "phone_rot", -30, 30, 0.5, "°")
        c.section(ed, "Screenshot crop")
        c.check(ed, "Custom crop for this scene", "shot_on")
        c.combo(ed, "Fit", "shot_fit", ["Cover", "Contain", "Stretch"])
        c.slider(ed, "Zoom", "shot_zoom", 50, 300, 1, "%")
        c.slider(ed, "Pan horizontally", "shot_pan_x", -100, 100, 1)
        c.slider(ed, "Pan vertically", "shot_pan_y", -100, 100, 1)
        c.section(ed, "Taps in this scene")
        self.tap_tree = ttk.Treeview(ed, columns=("start", "end", "pos"), show="headings", height=3,
                                     selectmode="browse", style="Scenes.Treeview")
        for col, text, w in (("start", "From", 70), ("end", "Until", 70), ("pos", "Position", 120)):
            self.tap_tree.heading(col, text=text)
            self.tap_tree.column(col, width=w, anchor="w")
        self.tap_tree.pack(fill="x", pady=(2, 4))
        self.tap_tree.bind("<<TreeviewSelect>>", self._on_tap_select)
        trow = ttk.Frame(ed)
        trow.pack(fill="x")
        for text, cmd in (("Add tap", self.add_tap), ("Pick position", self._start_pick), ("Remove", self.remove_tap)):
            ttk.Button(trow, text=text, command=cmd).pack(side="left", expand=True, fill="x", padx=2)
        c.slider(ed, "Show from", "tap_start", 0, 10, 0.05, " s")
        c.slider(ed, "Show until", "tap_end", 0, 10, 0.05, " s")
        c.slider(ed, "Position, across", "tap_x", 0, 1, 0.01)
        c.slider(ed, "Position, down", "tap_y", 0, 1, 0.01)
        c.section(ed, "Sound for this scene")
        c.check(ed, "Play the typing sound while this scene types", "typing_sound")
        c.check(ed, "Play the transition sound when this scene arrives", "transition_sound")

        right = ttk.Frame(outer, width=375)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        foot = ttk.Frame(right)
        foot.pack(side="bottom", fill="x", padx=18, pady=(4, 14))
        self.progress = ttk.Progressbar(foot, maximum=100)
        self.progress.pack(fill="x", pady=(8, 6))
        self.export_btn = ttk.Button(foot, text="Export MP4", style="Accent.TButton", command=self.export)
        self.export_btn.pack(fill="x")
        self.cancel_btn = ttk.Button(foot, text="Cancel", command=self._cancel.set)
        sf = ScrollFrame(right)
        sf.pack(fill="both", expand=True)
        rp = ttk.Frame(sf.inner)
        rp.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        c = self.pc
        c.section(rp, "Text animation")
        c.combo(rp, "Effect", "text_effect", ["typing", "fade"])
        c.slider(rp, "Typing speed", "type_speed", 6, 40, 1, " chars/s")
        fc = self.fc
        fc.section(rp, "Premium effects")
        for key, label in FX_LABELS:
            fc.check(rp, label, key)
        fc.slider(rp, "Effect strength", "intensity", 0, 100, 1, "%")
        c.section(rp, "End card")
        c.check(rp, "Show end card (needs an app name)", "outro")
        c.entry(rp, "App name", "app_name")
        c.entry(rp, "Tagline", "tagline")
        c.entry(rp, "Button text (empty = none)", "cta")
        c.slider(rp, "End card length", "outro_duration", 2, 6, 0.1, " s")
        c.section(rp, "Audio tracks")
        self._file_row(rp, "Music", "music")
        c.slider(rp, "Music volume", "music_volume", 0, 100, 1, "%")
        c.slider(rp, "Fade in", "music_fade_in", 0, 4, 0.1, " s")
        c.slider(rp, "Fade out", "music_fade_out", 0, 4, 0.1, " s")
        self._file_row(rp, "Typing sound (looped while text is typed)", "typing_sound")
        c.slider(rp, "Typing volume", "typing_volume", 0, 100, 1, "%")
        self._file_row(rp, "Transition sound (each scene change)", "whoosh_sound")
        c.slider(rp, "Transition volume", "whoosh_volume", 0, 100, 1, "%")
        c.check(rp, "Lower the music while typing", "duck")
        c.section(rp, "Output")
        c.combo(rp, "Frame rate", "fps", ["30", "60"])
        c.combo(rp, "Width (height follows the canvas)", "width", ["540", "720", "1080", "1440"])
        ttk.Button(rp, text="Refresh look from the main window", command=self.refresh_design).pack(fill="x", pady=(18, 0))

        center = ttk.Frame(outer, style="Bg.TFrame")
        center.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(center, bg=theme.BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=(12, 0))
        bar = ttk.Frame(center, style="Bg.TFrame")
        bar.pack(fill="x", padx=30, pady=(4, 8))
        self.play_btn = ttk.Button(bar, text="Play", width=8, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.scrub = ttk.Scale(bar, from_=0, to=10, variable=self.t_var, command=lambda v: self._scrub(float(v)))
        self.scrub.pack(side="left", fill="x", expand=True, padx=12)
        self.time_lbl = ttk.Label(bar, style="Info.TLabel", width=14)
        self.time_lbl.pack(side="left")
        opts = ttk.Frame(center, style="Bg.TFrame")
        opts.pack(pady=(0, 14))
        for text, val in (("Scene", "scene"), ("Whole video", "all")):
            ttk.Radiobutton(opts, text=text, value=val, variable=self.mode, style="Toolbutton").pack(side="left", padx=(0, 2))
        ttk.Checkbutton(opts, text="Live preview", variable=self.live, style="Bg.TCheckbutton").pack(side="left", padx=(16, 0))
        ttk.Checkbutton(opts, text="Sound", variable=self.sound, style="Bg.TCheckbutton").pack(side="left", padx=(12, 0))
        self.canvas.bind("<Configure>", lambda e: self._show(self.t_var.get()))
        self.canvas.bind("<Button-1>", self._canvas_click)

    # --------------------------------------------------------------- scene list
    def _effect_label(self, sc):
        eff = self.project.text_effect if sc.text_effect == "default" else sc.text_effect
        return eff + ("" if sc.text_effect == "default" else " •")

    def _row(self, i):
        sc = self.project.scenes[i]
        return (sc.image if len(sc.image) < 14 else "…" + sc.image[-12:], f"{sc.duration:g}", sc.motion,
                sc.transition if i else "intro", self._effect_label(sc))

    def _refresh_tree(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i in range(len(self.project.scenes)):
            self.tree.insert("", "end", iid=str(i), values=self._row(i))
        if self.project.scenes and self.project.outro and self.project.app_name.strip():
            self.tree.insert("", "end", iid="outro", values=("End card", f"{self.project.outro_duration:g}", "outro", "—", self.project.text_effect))
        if select is not None and self.project.scenes:
            self.tree.selection_set(str(min(select, len(self.project.scenes) - 1)))

    def _selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel and sel[0] != "outro" else None

    def _range(self):
        """(start, end) in seconds of what the preview plays: the selected scene, or everything."""
        segs, total = self.project.segments()
        if not segs:
            return 0.0, 0.0
        if self.mode.get() == "all":
            return 0.0, total
        sel = self.tree.selection()
        i = len(self.project.scenes) if sel and sel[0] == "outro" else (int(sel[0]) if sel else 0)
        seg = segs[max(0, min(i, len(segs) - 1))]
        return max(0.0, seg.start - (0.25 if seg.idx else 0.0)), seg.start + seg.dur  # small pre-roll shows the old phone

    def _on_select(self, _=None):
        self._picking = False
        self.canvas.config(cursor="")
        i = self._selected()
        if i is None:
            self._goto(self._range()[0])  # End card row: just play it
            return
        sc, e, d = self.project.scenes[i], self.ev, self.design
        self._loading = True
        e["duration"].set(sc.duration)
        e["motion"].set(sc.motion)
        e["transition"].set(sc.transition)
        e["transition_duration"].set(sc.transition_duration)
        e["text_effect"].set(sc.text_effect)
        e["custom"].set(sc.title is not None or sc.subtitle is not None)
        e["title"].set(sc.title or "")
        e["subtitle"].set(sc.subtitle or "")
        e["type_speed"].set(sc.type_speed)
        e["text_delay"].set(sc.text_delay)
        e["style_on"].set(bool(sc.text_style))
        for k in TEXT_STYLE:
            e[k].set(sc.text_style.get(k, getattr(d, k)))
        e["phone_scale"].set(sc.phone_scale)
        e["phone_dx"].set(sc.phone_dx)
        e["phone_dy"].set(sc.phone_dy)
        e["phone_rot"].set(sc.phone_rot)
        e["shot_on"].set(bool(sc.shot))
        for k in ("shot_fit", "shot_zoom", "shot_pan_x", "shot_pan_y"):
            e[k].set(sc.shot.get(k, getattr(d, k)))
        e["typing_sound"].set(sc.typing_sound)
        e["transition_sound"].set(sc.transition_sound)
        self.ec.sync_texts()
        self._refresh_taps(select=0)
        self._loading = False
        self._goto(self._range()[0])

    def _editor_changed(self, *_):
        i = self._selected()
        if self._loading or i is None:
            return
        sc, e = self.project.scenes[i], self.ev
        try:
            sc.duration = round(float(e["duration"].get()), 2)
            sc.transition_duration = round(float(e["transition_duration"].get()), 2)
            sc.type_speed, sc.text_delay = round(float(e["type_speed"].get()), 1), round(float(e["text_delay"].get()), 2)
            sc.phone_scale = int(round(float(e["phone_scale"].get())))
            sc.phone_dx, sc.phone_dy, sc.phone_rot = (round(float(e[k].get()), 2) for k in ("phone_dx", "phone_dy", "phone_rot"))
            if self._tap_i is not None and self._tap_i < len(sc.taps):
                a = round(float(e["tap_start"].get()), 2)
                b = max(round(float(e["tap_end"].get()), 2), a + 0.1)
                tp = sc.taps[self._tap_i]
                tp.update(start=a, end=b, x=round(float(e["tap_x"].get()), 3), y=round(float(e["tap_y"].get()), 3))
                self.tap_tree.item(str(self._tap_i), values=self._tap_row(tp))
            sc.text_style = ({k: (e[k].get() if k in ("text_align", "title_color", "subtitle_color") else round(float(e[k].get()), 2))
                              for k in TEXT_STYLE} if e["style_on"].get() else {})
            sc.shot = ({"shot_fit": e["shot_fit"].get(), "shot_zoom": round(float(e["shot_zoom"].get()), 1),
                        "shot_pan_x": round(float(e["shot_pan_x"].get()), 1), "shot_pan_y": round(float(e["shot_pan_y"].get()), 1)}
                       if e["shot_on"].get() else {})
        except (tk.TclError, ValueError):
            return
        sc.motion, sc.transition, sc.text_effect = e["motion"].get(), e["transition"].get(), e["text_effect"].get()
        sc.typing_sound, sc.transition_sound = e["typing_sound"].get(), e["transition_sound"].get()
        if e["custom"].get():
            sc.title, sc.subtitle = e["title"].get(), e["subtitle"].get()
        else:
            sc.title = sc.subtitle = None
        self.tree.item(str(i), values=self._row(i))
        self._changed()

    def effect_all(self):
        eff = self.ev["text_effect"].get()
        for sc in self.project.scenes:
            sc.text_effect = eff
        self._refresh_tree(select=self._selected())
        self._changed()
        self.status.set(f"Text effect '{eff}' applied to every scene.")

    # ------------------------------------------------- animation picker + effects
    def open_picker(self, kind):
        if self._selected() is None:
            self.status.set("Select a scene first.")
            return
        if self._picker is not None and self._picker.winfo_exists():
            self._picker.destroy()
        self._picker = AnimationPicker(self, kind, self.ev[kind].get(),
                                       lambda spec, all_scenes: self._picked(kind, spec, all_scenes))

    def _picked(self, key, spec, all_scenes):
        self.ev[key].set(spec)
        if all_scenes:
            for i, sc in enumerate(self.project.scenes):
                if key == "transition" and i == 0:  # the first scene has no incoming transition
                    continue
                setattr(sc, key, spec)
            self._refresh_tree(select=self._selected())
            self._changed()
        self.status.set(f"{key.capitalize()} set to {spec}.")

    def _fx_changed(self, *_):
        try:
            self.fx = {k: (int(round(float(v.get()))) if k == "intensity" else bool(v.get())) for k, v in self.fxv.items()}
        except (tk.TclError, ValueError):
            return
        self._changed()
        self._later("savefx", 600, self._save_fx)

    def _project_changed(self, *_):
        P, v = self.project, self.pv
        try:
            P.app_name, P.tagline, P.cta = v["app_name"].get(), v["tagline"].get(), v["cta"].get()
            P.outro, P.outro_duration = v["outro"].get(), round(float(v["outro_duration"].get()), 2)
            P.fps, P.width = int(v["fps"].get() or 30), int(v["width"].get() or 1080)
            P.text_effect, P.type_speed = v["text_effect"].get(), round(float(v["type_speed"].get()), 1)
            P.music, P.typing_sound, P.whoosh_sound = v["music"].get(), v["typing_sound"].get(), v["whoosh_sound"].get()
            P.music_volume, P.typing_volume, P.whoosh_volume = (int(round(float(v[k].get()))) for k in ("music_volume", "typing_volume", "whoosh_volume"))
            P.music_fade_in, P.music_fade_out = round(float(v["music_fade_in"].get()), 2), round(float(v["music_fade_out"].get()), 2)
            P.duck = v["duck"].get()
        except (tk.TclError, ValueError):
            return
        self._refresh_tree(select=self._selected())
        self._changed()

    def _changed(self):
        self._fr = None
        self._later("save", 600, self._save)
        self._later("show", 120, self._after_change)
        self._later("audio", 450, self._refresh_audio)

    def _after_change(self):
        if self._exporting or not self.winfo_exists():
            return
        if self._picking:  # frozen while choosing a tap position
            self._show(self._pick_t)
            return
        if self.live.get() and self.project.scenes:
            self._play_from(self._range()[0])
        else:
            self._show(self.t_var.get())

    # ---------------------------------------------------------------------- taps
    def _taps(self):
        i = self._selected()
        return self.project.scenes[i].taps if i is not None else None

    @staticmethod
    def _tap_row(tp):
        return (f"{tp['start']:g} s", f"{tp['end']:g} s", f"{tp['x']:.2f}, {tp['y']:.2f}")

    def _refresh_taps(self, select=None):
        self.tap_tree.delete(*self.tap_tree.get_children())
        taps = self._taps() or []
        for k, tp in enumerate(taps):
            self.tap_tree.insert("", "end", iid=str(k), values=self._tap_row(tp))
        if select is not None and taps:
            self._tap_i = min(select, len(taps) - 1)
            self.tap_tree.selection_set(str(self._tap_i))
        else:
            self._tap_i = None

    def _on_tap_select(self, _=None):
        sel, taps = self.tap_tree.selection(), self._taps()
        if not sel or taps is None or int(sel[0]) >= len(taps):
            self._tap_i = None
            return
        self._tap_i = int(sel[0])
        tp, e = taps[self._tap_i], self.ev
        self._loading = True
        e["tap_start"].set(tp["start"])
        e["tap_end"].set(tp["end"])
        e["tap_x"].set(tp["x"])
        e["tap_y"].set(tp["y"])
        self._loading = False

    def add_tap(self):
        i = self._selected()
        if i is None:
            return
        sc = self.project.scenes[i]
        segs, _ = self.project.segments()
        lt = self.t_var.get() - segs[i].start  # where the preview is now, inside this scene
        a = round(min(max(lt, 0.4), max(0.4, sc.duration - 1.3)), 2)
        sc.taps.append({"x": 0.5, "y": 0.58, "start": a, "end": round(min(a + 1.25, sc.duration), 2)})
        self._refresh_taps(select=len(sc.taps) - 1)
        self._changed()
        self._start_pick()

    def remove_tap(self):
        taps, k = self._taps(), self._tap_i
        if taps is not None and k is not None and k < len(taps):
            del taps[k]
            self._refresh_taps(select=k)
            self._changed()

    def _start_pick(self):
        taps, i = self._taps(), self._selected()
        if taps is None or self._tap_i is None or self._tap_i >= len(taps):
            self.status.set("Add or select a tap first.")
            return
        tp = taps[self._tap_i]
        segs, _ = self.project.segments()
        self._pick_t = segs[i].start + tp["start"] + (tp["end"] - tp["start"]) * 0.4  # a moment where the dot is visible
        self._picking, self._playing = True, False
        self.player.stop()
        self.play_btn.config(text="Play")
        self.canvas.config(cursor="crosshair")
        self.status.set("Click on the phone screen where the tap should happen.")
        self.t_var.set(self._pick_t)
        self._show(self._pick_t)

    def _canvas_click(self, ev):
        if not self._picking or not self._shown or self._exporting:
            return
        cx, cy, k, w0, h0 = self._shown
        fx, fy = (ev.x - cx) / k + w0 / 2, (ev.y - cy) / k + h0 / 2
        x, y = self._renderer().screen_point(self._pick_t, fx, fy)
        self._picking = False
        self.canvas.config(cursor="")
        self.ev["tap_x"].set(round(x, 3))
        self.ev["tap_y"].set(round(y, 3))
        self.status.set("Tap position set.")

    # scene actions
    def _add(self, names):
        v = config.VIDEO["scene_duration"]
        for n in names:
            self.project.scenes.append(make_scene(len(self.project.scenes), n, v))
        self._refresh_tree(select=len(self.project.scenes) - 1)
        self._changed()

    def add_scenes(self):
        for_ = filedialog.askopenfilenames(parent=self, initialdir=self.paths.raw, filetypes=IMG_TYPES)
        self._add([Path(p).name if Path(p).parent.resolve() == self.paths.raw.resolve() else str(Path(p)) for p in for_])

    def add_all(self):
        files = sorted((p for p in self.paths.raw.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")),
                       key=natural_key)
        self._add([f.name for f in files])

    def duplicate(self):
        i = self._selected()
        if i is not None:
            import copy
            self.project.scenes.insert(i + 1, copy.deepcopy(self.project.scenes[i]))
            self._refresh_tree(select=i + 1)
            self._changed()

    def remove(self):
        i = self._selected()
        if i is not None:
            del self.project.scenes[i]
            self._refresh_tree(select=i)
            self._changed()

    def move(self, d):
        i = self._selected()
        if i is not None and 0 <= i + d < len(self.project.scenes):
            s = self.project.scenes
            s[i], s[i + d] = s[i + d], s[i]
            self._refresh_tree(select=i + d)
            self._changed()

    def _file_row(self, parent, label, key):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=(8, 0))
        ttk.Label(f, text=label).pack(anchor="w")
        ttk.Entry(f, textvariable=self.pv[key]).pack(fill="x", pady=(3, 4))
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Button(r, text="Choose…", command=lambda: self._pick_file(key)).pack(side="left", expand=True, fill="x", padx=(0, 3))
        ttk.Button(r, text="Clear", command=lambda: self.pv[key].set("")).pack(side="left", expand=True, fill="x")

    def _pick_file(self, key):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"), ("All files", "*.*")])
        if p:
            self.pv[key].set(p)

    # -- audio preview: the same FFmpeg mix as the export, rendered to memory and played from the current position
    def _now(self):
        return self._play_base + time.time() - self._play_t0

    def _audio_start(self, t):
        if self._pcm and self.sound.get() and self.player.available:
            self.player.play(self._pcm, t)
        else:
            self.player.stop()

    def _refresh_audio(self):
        if not self.winfo_exists() or self._exporting:
            return
        P = self.project
        if not P.scenes or not has_audio(P):
            self._audio_gen += 1
            self._pcm, self._pcm_key = None, None
            self.player.stop()
            return
        if not self.player.available:
            if not self._warned:
                self._warned = True
                self.status.set("Audio preview needs pygame:  pip install pygame   (the exported video still includes the audio)")
            return
        fr = self._renderer()
        total, iv, wt = self._total(), fr.typing_intervals(), fr.whoosh_times()
        key = json.dumps([P.music, P.music_volume, P.music_fade_in, P.music_fade_out, P.typing_sound, P.typing_volume,
                          P.whoosh_sound, P.whoosh_volume, P.duck, round(total, 2),
                          [(round(a, 2), round(b, 2)) for a, b in iv], [round(x, 2) for x in wt]])
        if key == self._pcm_key:
            return
        try:
            ffmpeg = find_ffmpeg()
        except RuntimeError:
            return
        self._pcm_key = key
        self._audio_gen += 1
        gen, proj = self._audio_gen, Project.from_dict(P.to_dict())

        def work():
            try:
                self._q.put(("pcm", gen, render_pcm(ffmpeg, proj, total, iv, wt)))
            except Exception as exc:
                self._q.put(("pcm_err", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def refresh_design(self):
        self.design = self.app.collect()
        self._changed()
        self.status.set("Look refreshed from the main window.")

    # ------------------------------------------------------------------ preview
    def _img(self, name):
        if name not in self.images:
            try:
                self.images[name] = load_image(resolve_image(self.paths.raw, name))
            except Exception:
                self.images[name] = Image.new("RGB", (1080, 2400), (60, 60, 70))
        return self.images[name]

    def _renderer(self):
        if self._fr is None:
            for sc in self.project.scenes:
                self._img(sc.image)
            self._fr = FrameRenderer(self.design, self.project, self.images, PREVIEW_W, self.paths.video_assets,
                                     fx=self.fx)
        return self._fr

    def _total(self):
        return self.project.segments()[1] if self.project.scenes else 0.0

    def _show(self, t):
        if self._exporting or not self.winfo_exists():
            return
        lo, hi = self._range()
        self.scrub.configure(from_=lo, to=max(lo + 0.1, hi))
        if not self.project.scenes:
            self.canvas.delete("all")
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                    text="Add screenshots to start", fill=theme.MUTED)
            return
        t = max(lo, min(t, hi - 1e-3))
        try:
            img = self._renderer().frame(t)
        except Exception as exc:
            self.status.set(f"Preview error: {exc}")
            return
        self._blit(img)
        self.time_lbl.config(text=f"{t - lo:4.1f} / {hi - lo:.1f} s")

    def _blit(self, img):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 120 or ch < 120:  # not laid out yet; the <Configure> event will draw it
            return
        w0, h0 = img.size
        k = min((cw - 40) / img.width, (ch - 20) / img.height, 1.35)
        ke = 1.0
        if abs(k - 1) > 0.02:
            img = img.resize((int(img.width * k), int(img.height * k)), Image.Resampling.BILINEAR)
            ke = img.width / w0
        self._shown = (cw // 2, ch // 2, ke, w0, h0)  # remembered so a click can be turned into a frame position
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)

    def _goto(self, t):
        self.t_var.set(t)
        if self._playing or self.live.get():
            self._play_from(t)
        else:
            self._show(t)

    def _scrub(self, t):
        if self._picking:  # moving the slider while picking changes the moment the tap is placed on
            self._pick_t = t
            self._show(t)
        elif self._playing:  # seek while playing
            self._play_base = t
            self._audio_start(t)
            self._play_t0 = time.time()
        else:
            self._show(t)

    def toggle_play(self):
        self._picking = False
        self.canvas.config(cursor="")
        if self._playing:
            self._playing = False
            self.player.stop()
            self.play_btn.config(text="Play")
        else:
            lo, hi = self._range()
            t = self.t_var.get()
            self._play_from(lo if t >= hi - 0.2 or t < lo else t)

    def _play_from(self, t):
        if self._exporting or not self.project.scenes:
            return
        lo, hi = self._range()
        self._playing = True
        self.play_btn.config(text="Pause")
        self._play_base = min(max(t, lo), hi - 0.01)
        self._audio_start(self._play_base)
        self._play_t0 = time.time()
        if self._tick_job:
            self.after_cancel(self._tick_job)
        self._tick()

    def _tick(self):
        self._tick_job = None
        if not self._playing or not self.winfo_exists():
            return
        lo, hi = self._range()
        t = self._play_base + time.time() - self._play_t0
        if t >= hi:  # loop inside the current range (one scene, or the whole video)
            t, self._play_base = lo, lo
            self._audio_start(lo)
            self._play_t0 = time.time()
        self.t_var.set(t)
        self._show(t)
        self._tick_job = self.after(1, self._tick)

    # ------------------------------------------------------------------- export
    def export(self):
        if not self.project.scenes:
            messagebox.showinfo("Nothing to export", "Add at least one screenshot.", parent=self)
            return
        try:
            find_ffmpeg()
        except RuntimeError as exc:
            messagebox.showerror("FFmpeg needed", str(exc), parent=self)
            return
        name = re.sub(r"\W+", "_", self.project.app_name).strip("_") or "promo"
        out = filedialog.asksaveasfilename(parent=self, initialdir=self.paths.output, initialfile=f"{name}_promo.mp4",
                                           defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")])
        if not out:
            return
        self._playing = False
        self._picking = False
        self.canvas.config(cursor="")
        self.player.stop()
        self.play_btn.config(text="Play")
        self._save()
        for sc in self.project.scenes:
            self._img(sc.image)
        self._exporting = True
        self._cancel.clear()
        self.export_btn.state(["disabled"])
        self.cancel_btn.pack(fill="x", pady=(6, 0))
        project, design, images = Project.from_dict(self.project.to_dict()), self.design, dict(self.images)
        fx = dict(self.fx)

        def work():
            try:
                def prog(i, n, frame):
                    small = frame.reduce(max(1, frame.width // PREVIEW_W)) if i % 3 == 0 or i == n else None
                    self._q.put(("p", i, n, small))

                ok = render_video(design, project, images, out, project.width, assets_dir=self.paths.video_assets,
                                  progress=prog, cancel=self._cancel.is_set, fx=fx)
                self._q.put(("done", out) if ok else ("cancel",))
            except Exception as exc:
                self._q.put(("err", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        latest = None
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "p":
                    self.progress["value"] = msg[1] * 100 / msg[2]
                    self.status.set(f"Rendering frame {msg[1]} of {msg[2]}…  (you are watching the export live)")
                    latest = msg[3] if msg[3] is not None else latest
                elif msg[0] == "pcm":
                    if msg[1] == self._audio_gen:
                        self._pcm = msg[2]
                        if self._playing:
                            self._audio_start(self._now())
                elif msg[0] == "pcm_err":
                    self.status.set("Audio preview failed: " + msg[1][-160:])
                else:
                    self._exporting = False
                    self.export_btn.state(["!disabled"])
                    self.cancel_btn.pack_forget()
                    self.progress["value"] = 100 if msg[0] == "done" else 0
                    if msg[0] == "done":
                        self.status.set(f"Saved {msg[1]}")
                    elif msg[0] == "cancel":
                        self.status.set("Export cancelled.")
                    else:
                        self.status.set("Export failed.")
                        messagebox.showerror("Export failed", msg[1], parent=self)
                    self._after_change()
        except queue.Empty:
            pass
        if latest is not None and self._exporting:
            self._blit(latest)
        self.after(100, self._poll)

    def _close(self):
        self._playing = False
        self.player.stop()
        self._cancel.set()
        self._save()
        self.destroy()