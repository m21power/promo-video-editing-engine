"""Promo video studio: scene list, live scrub/play preview, MP4 export."""
import copy
import json
import math
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
from ui.audio_cropper import AudioCropper, fmt as fmt_time
from ui.clip_editor import ClipEditor
from ui.widgets import Controls, ScrollFrame
from video import animations as A
from video import callouts as CO
from video import clips as C
from video import premium  # noqa: F401  (registers the premium animations; must come before the combos are built)
from video import transitions as T
from video.audio import TRACK_DEFAULTS, Player, audio_len, has_audio, render_pcm, track_end, usable_len
from video.engine import FrameRenderer, find_ffmpeg, load_image, render_video, resolve_image
from video.fx import FX_DEFAULTS, FX_LABELS
from video.picker import AnimationPicker
from video.timeline import Project, is_blank, is_video, make_blank, make_scene

PREVIEW_W = 420
TEXT_STYLE = ("title_size", "subtitle_size", "text_y", "text_x", "text_align", "title_color", "subtitle_color", "text_margin")
STR_STYLE = ("text_align", "title_color", "subtitle_color")
EDIT_HINT = ("Drag the text to move it · drag a corner (or use the mouse wheel) to resize · scrub to look at another moment · "
             "press the button again when you are done.")
EDIT_LABEL = "✥  Move / resize text on the preview"
EFFECTS = ["default", "typing", "fade", "static", "off"]
MEDIA_TYPES = [("Screenshots and videos", " ".join("*" + e for e in sorted(config.MEDIA_EXT)))]
AMBIENT = {"none", "float", "orbit", "handheld", "breathe", "levitate", "pendulum", "slow_orbit", "gyro", "heartbeat", "beat_bounce"}


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


class VideoDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.paths = app, app.paths
        self.title("Promo video studio")
        self.geometry("1780x920")
        self.minsize(1500, 780)
        self.configure(bg=theme.BG)
        self.design = app.collect()
        self.cfg = self.paths.presets / "promo_project.json"
        self.fx_cfg = self.paths.presets / "promo_fx.json"
        self._restoring = False
        self.project = self._adopt_music(self._load_project())
        self.fx = self._load_fx()
        self._hist, self._hi = [self._snapshot()], 0          # undo / redo history of the project
        self._picker = None
        self.images, self._fr, self._photo = {}, None, None
        self._playing, self._loading, self._exporting = False, False, False
        self._picking, self._pick_t, self._tap_i, self._shown = False, 0.0, None, None
        self._tr_i, self._tloading, self._ldrag, self._lane_geo = None, False, None, None   # music tracks panel
        self._co_i, self._pick_kind, self._drag = None, "tap", None
        self._edit_sel, self._edit_drag, self._edit_guide = None, None, None   # moving / resizing text on the preview
        self.edit_txt = tk.StringVar(value=EDIT_LABEL)   # highlights: selected row, what a canvas click sets
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
            text_margin=D(value=d.text_margin),
            title_color=tk.StringVar(value=d.title_color), subtitle_color=tk.StringVar(value=d.subtitle_color),
            phone_scale=D(value=100), phone_dx=D(value=0), phone_dy=D(value=0), phone_rot=D(value=0),
            shot_on=tk.BooleanVar(value=False), shot_fit=tk.StringVar(value=d.shot_fit), shot_zoom=D(value=d.shot_zoom),
            shot_pan_x=D(value=d.shot_pan_x), shot_pan_y=D(value=d.shot_pan_y),
            tap_start=D(value=1.0), tap_end=D(value=2.2), tap_x=D(value=0.5), tap_y=D(value=0.58),
            co_shape=tk.StringVar(value="ellipse"), co_style=tk.StringVar(value="Outline"), co_color=tk.StringVar(value="#FFD43B"),
            co_x=D(value=0.5), co_y=D(value=0.5), co_w=D(value=0.34), co_h=D(value=0.16), co_rot=D(value=0),
            co_thickness=D(value=1.0), co_opacity=D(value=35), co_start=D(value=0.8), co_end=D(value=2.6),
            co_in=tk.StringVar(value="pop"), co_out=tk.StringVar(value="fade"), co_time=D(value=0.45),
            co_pulse=tk.BooleanVar(value=True),
            typing_sound=tk.BooleanVar(value=True), transition_sound=tk.BooleanVar(value=True),
            clip_speed=D(value=1.0), clip_fit=tk.BooleanVar(value=False), clip_volume=D(value=100), clip_mute=tk.BooleanVar(value=False),
            clip_loop=tk.BooleanVar(value=False), clip_brightness=D(value=0), clip_contrast=D(value=100),
            clip_saturation=D(value=100))
        self.pv = dict(app_name=tk.StringVar(value=P.app_name), tagline=tk.StringVar(value=P.tagline),
                       outro=tk.BooleanVar(value=P.outro), outro_duration=tk.DoubleVar(value=P.outro_duration),
                       cta=tk.StringVar(value=P.cta), fps=tk.StringVar(value=str(P.fps)),
                       width=tk.StringVar(value=str(P.width)),
                       text_effect=tk.StringVar(value=P.text_effect), type_speed=tk.DoubleVar(value=P.type_speed),
                       music=tk.StringVar(value=P.music), music_volume=tk.DoubleVar(value=P.music_volume),
                       music_fade_in=tk.DoubleVar(value=P.music_fade_in), music_fade_out=tk.DoubleVar(value=P.music_fade_out),
                       typing_sound=tk.StringVar(value=P.typing_sound), typing_volume=tk.DoubleVar(value=P.typing_volume),
                       whoosh_sound=tk.StringVar(value=P.whoosh_sound), whoosh_volume=tk.DoubleVar(value=P.whoosh_volume),
                       tap_sound=tk.StringVar(value=P.tap_sound), tap_volume=tk.DoubleVar(value=P.tap_volume),
                       duck=tk.BooleanVar(value=P.duck), quality=tk.StringVar(value=P.quality),
                       crossfade=tk.DoubleVar(value=P.crossfade),
                       outro_headline=tk.StringVar(value=P.outro_headline), outro_subline=tk.StringVar(value=P.outro_subline),
                       outro_badge=tk.BooleanVar(value=P.outro_badge), outro_badge_small=tk.StringVar(value=P.outro_badge_small),
                       outro_badge_big=tk.StringVar(value=P.outro_badge_big), outro_font=tk.StringVar(value=P.outro_font or "Auto"))
        self.fxv = {k: (tk.DoubleVar(value=self.fx[k]) if k == "intensity" else tk.BooleanVar(value=bool(self.fx[k])))
                    for k in FX_DEFAULTS}
        self.tv = dict(t_start=D(value=0.0), t_end=D(value=0.0), t_offset=D(value=0.0), t_volume=D(value=100),
                       t_fade_in=D(value=0.0), t_fade_out=D(value=0.0), t_loop=tk.BooleanVar(value=False), t_src_end=D(value=0.0))
        self.t_at, self.t_until = tk.StringVar(), tk.StringVar()
        self.ec, self.pc, self.fc = Controls(self, self.ev), Controls(self, self.pv), Controls(self, self.fxv)
        self.tc = Controls(self, self.tv)
        self.t_var = tk.DoubleVar(value=0.0)
        self.status = tk.StringVar(value="Live preview: every change plays instantly. Export only when you like it.")

        self._build()
        self._refresh_tracks()
        self._refresh_tree(select=0 if P.scenes else None)
        for v in self.ev.values():
            v.trace_add("write", self._editor_changed)
        for v in self.pv.values():
            v.trace_add("write", self._project_changed)
        for v in self.tv.values():
            v.trace_add("write", self._track_changed)
        for v in self.fxv.values():
            v.trace_add("write", self._fx_changed)
        self.mode.trace_add("write", lambda *_: self._after_change())
        self.sound.trace_add("write", lambda *_: self._audio_start(self._now()) if self._playing else self.player.stop())
        self.after(300, self._refresh_audio)
        self.bind("<space>", self._key(self.toggle_play, strict=True))
        self.bind("s", self._key(self.split_clip))
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-y>", lambda e: self.undo(redo=True))
        self.bind("<Control-Shift-Z>", lambda e: self.undo(redo=True))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(150, self._sync_new_media)
        self.after(350, lambda: self._play_from(0.0) if self.live.get() else self._show(0.9))
        self.after(100, self._poll)

    # ---------------------------------------------------------------- project io
    def _load_project(self):
        if self.cfg.exists():
            try:
                proj = Project.from_dict(json.loads(self.cfg.read_text("utf-8")))
                proj.scenes = [s for s in proj.scenes if is_blank(s.image) or resolve_image(self.paths.raw, s.image).exists()]
                if proj.scenes:
                    return proj
            except (OSError, ValueError, TypeError):
                pass
        files = sorted((p for p in self.paths.raw.iterdir() if p.suffix.lower() in config.MEDIA_EXT), key=natural_key)
        v = config.VIDEO
        scenes = [make_scene(i, f.name, v["scene_duration"]) for i, f in enumerate(files)]
        for sc in scenes:
            self._init_video(sc)
        return Project(scenes=scenes, outro_duration=v["outro_duration"], cta=v["cta"], fps=v["fps"], width=v["width"],
                       transition_duration=v["transition"])

    def _sync_new_media(self):
        """Pick up files that were added to the folder since this project was saved (the saved scene list never re-scans).
        Each file is offered once, so a scene you deleted on purpose stays deleted. The very first time, only videos are
        added automatically (screenshots you may have removed deliberately are left alone)."""
        seen_file = self.paths.presets / "promo_seen.json"
        try:
            files = sorted((p for p in self.paths.raw.iterdir() if p.suffix.lower() in config.MEDIA_EXT), key=natural_key)
        except OSError:
            return
        names = [f.name for f in files]
        have = {Path(sc.image).name for sc in self.project.scenes}
        try:
            seen = set(json.loads(seen_file.read_text("utf-8")))
        except (OSError, ValueError):
            seen = None
        new = [n for n in names if n not in have and (is_video(n) if seen is None else n not in seen)]
        try:
            seen_file.write_text(json.dumps(names), "utf-8")
        except OSError:
            pass
        if new:
            self._add(new)
            if not self.status.get().startswith("Could not read"):
                self.status.set(f"Added {len(new)} new file(s) from the folder: " + ", ".join(new[:4]) + ("…" if len(new) > 4 else ""))

    def _init_video(self, sc):
        """Make a freshly added video scene as long as its clip. Returns an error text, or None."""
        if not is_video(sc.image):
            return None
        try:
            C.init_scene(find_ffmpeg(), sc, resolve_image(self.paths.raw, sc.image))
            sc.motion = "float"
        except Exception as exc:
            return str(exc).splitlines()[0]
        return None

    # -- undo / redo: a snapshot of the whole project is taken whenever it settles (see _save)
    def _snapshot(self):
        return json.dumps(self.project.to_dict(), sort_keys=True)

    def _push_history(self):
        snap = self._snapshot()
        if snap != self._hist[self._hi]:
            del self._hist[self._hi + 1:]
            self._hist.append(snap)
            self._hist = self._hist[-80:]
            self._hi = len(self._hist) - 1

    def undo(self, redo=False):
        if "save" in self._jobs:  # commit what is pending first so it can be undone too
            self.after_cancel(self._jobs.pop("save"))
        self._save()
        j = self._hi + (1 if redo else -1)
        if self._exporting or not 0 <= j < len(self._hist):
            self.status.set("Nothing to redo." if redo else "Nothing to undo.")
            return
        self._hi, sel = j, self._selected()
        self.project = Project.from_dict(json.loads(self._hist[j]))
        self._sync_vars()
        self._fr = None
        self._refresh_tree(select=sel if sel is not None else 0)
        self._save()
        self._changed()
        self.status.set("Redone." if redo else "Undone.")

    def _sync_vars(self):
        """Project -> the right-hand panel variables (without echoing the values back into the project)."""
        self._restoring = True
        try:
            for k, var in self.pv.items():
                v = getattr(self.project, k)
                var.set(str(v) if isinstance(var, tk.StringVar) and not isinstance(v, str) else v)
        finally:
            self._restoring = False
        self._refresh_tracks()

    def _key(self, fn, strict=False):
        """Keyboard shortcut that stays out of the way while you are typing or pressing a button."""
        typing = (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, tk.Spinbox)
        pressing = (ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Scale)

        def handler(_e):
            try:
                w = self.focus_get()
            except KeyError:  # Tk reports a combobox popdown as an unknown widget
                return None
            if isinstance(w, typing) or (strict and isinstance(w, pressing)):
                return None
            fn()
            return "break"
        return handler

    def reload_workspace(self):
        """The main window switched to another raw folder: load that folder's own promo project."""
        self._playing = False
        self.player.stop()
        self.play_btn.config(text="Play")
        self.cfg = self.paths.presets / "promo_project.json"
        self.fx_cfg = self.paths.presets / "promo_fx.json"
        self.project, self.fx = self._adopt_music(self._load_project()), self._load_fx()
        self.images.clear()
        self._fr, self._pcm, self._pcm_key = None, None, None
        self._sync_vars()
        for k, var in self.fxv.items():
            var.set(self.fx[k])
        self._hist, self._hi = [self._snapshot()], 0
        self._refresh_tree(select=0 if self.project.scenes else None)
        self.title(f"Promo video studio · {self.paths.raw.name}")
        self._sync_new_media()
        self._changed()
        if not self.project.scenes:
            self._show(0.0)
        self.status.set(f"Folder: {self.paths.raw}  ·  {len(self.project.scenes)} scenes")

    def _save(self):
        self._push_history()
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
        for col, text, w in (("img", "Media", 96), ("dur", "Sec", 36), ("motion", "Motion", 112),
                             ("tr", "Transition", 88), ("fx", "Text", 62)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        for rowdef in ((("Add…", self.add_scenes), ("Add all", self.add_all), ("Duplicate", self.duplicate)),
                       (("Remove", self.remove), ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1))),
                       (("Folder…", self.app.choose_folder), ("Split  (S)", self.split_clip), ("Trim / crop…", self.edit_clip)),
                       (("Add blank canvas (text only)", self.add_blank),)):
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
        c.slider(ed, "Duration", "duration", 0.5, 90, 0.1, " s")
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
        ttk.Label(ed, text="Tip: you can also drag the text on the preview (button under it).", style="Muted.TLabel",
                  wraplength=300).pack(anchor="w", pady=(4, 0))
        c.slider(ed, "Title size", "title_size", 1, 30, 0.1)
        c.slider(ed, "Subtitle size", "subtitle_size", 0.5, 16, 0.1)
        c.slider(ed, "Vertical position", "text_y", -20, 100, 0.5, "%")
        c.slider(ed, "Horizontal offset", "text_x", -100, 100, 0.5, "%")
        c.slider(ed, "Side margin (smaller = wider text)", "text_margin", 0, 40, 0.5, "%")
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
        c.section(ed, "Video clip (video scenes only)")
        ttk.Button(ed, text="Trim and crop the video…", style="Accent.TButton", command=self.edit_clip).pack(fill="x", pady=(2, 4))
        row = ttk.Frame(ed)
        row.pack(fill="x")
        ttk.Button(row, text="Split at playhead  (S)", command=self.split_clip).pack(side="left", expand=True, fill="x", padx=(0, 3))
        ttk.Button(row, text="Fit scene to clip", command=self.fit_scene).pack(side="left", expand=True, fill="x")
        c.check(ed, "Stretch the video to the scene length", "clip_fit")
        ttk.Label(ed, text="On: make the Duration longer and the video plays slower, shorter and it plays faster "
                  "(its sound follows). Off: the duration only adds or cuts time.", style="Muted.TLabel",
                  wraplength=300).pack(anchor="w", pady=(0, 2))
        ttk.Button(ed, text="Normal speed  (1×)", command=lambda: self.ev["clip_speed"].set(1.0)).pack(fill="x", pady=(4, 0))
        c.slider(ed, "Speed", "clip_speed", 0.1, 8, 0.05, "×")
        c.slider(ed, "Volume", "clip_volume", 0, 150, 1, "%")
        c.check(ed, "Mute the video's own sound", "clip_mute")
        c.check(ed, "Loop when the scene is longer than the clip", "clip_loop")
        c.slider(ed, "Brightness", "clip_brightness", -50, 50, 1)
        c.slider(ed, "Contrast", "clip_contrast", 50, 150, 1, "%")
        c.slider(ed, "Saturation", "clip_saturation", 0, 200, 1, "%")
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
        c.section(ed, "Highlights in this scene")
        ttk.Label(ed, text="Mark an area with a shape for a few seconds: it animates in, stays, then animates out.",
                  style="Muted.TLabel", wraplength=300).pack(anchor="w", pady=(2, 4))
        self.co_tree = ttk.Treeview(ed, columns=("shape", "time", "style"), show="headings", height=3,
                                    selectmode="browse", style="Scenes.Treeview")
        for col, text, w in (("shape", "Shape", 90), ("time", "Shown", 100), ("style", "Style", 100)):
            self.co_tree.heading(col, text=text)
            self.co_tree.column(col, width=w, anchor="w")
        self.co_tree.pack(fill="x", pady=(2, 4))
        self.co_tree.bind("<<TreeviewSelect>>", self._on_co_select)
        crow = ttk.Frame(ed)
        crow.pack(fill="x")
        for text, cmd in (("Add highlight", self.add_callout), ("Draw area", self._start_region), ("Remove", self.remove_callout)):
            ttk.Button(crow, text=text, command=cmd).pack(side="left", expand=True, fill="x", padx=2)
        c.combo(ed, "Shape", "co_shape", CO.SHAPES)
        c.combo(ed, "Style", "co_style", CO.STYLES)
        c.color(ed, "Colour", "co_color")
        c.slider(ed, "Fill / dimming opacity", "co_opacity", 0, 100, 1, "%")
        c.slider(ed, "Outline thickness", "co_thickness", 0.2, 4, 0.1, "%")
        c.slider(ed, "Show from", "co_start", 0, 10, 0.05, " s")
        c.slider(ed, "Show until", "co_end", 0, 10, 0.05, " s")
        c.combo(ed, "Appear animation", "co_in", CO.ANIM_IN)
        c.combo(ed, "Disappear animation", "co_out", CO.ANIM_OUT)
        c.slider(ed, "Animation length", "co_time", 0.1, 1.5, 0.05, " s")
        c.check(ed, "Ripple ring while it is shown", "co_pulse")
        c.slider(ed, "Centre, across", "co_x", 0, 1, 0.01)
        c.slider(ed, "Centre, down", "co_y", 0, 1, 0.01)
        c.slider(ed, "Width", "co_w", 0.03, 1, 0.01)
        c.slider(ed, "Height", "co_h", 0.03, 1, 0.01)
        c.slider(ed, "Rotation", "co_rot", -180, 180, 1, "°")
        c.section(ed, "Sound for this scene")
        c.check(ed, "Play the typing sound while this scene types", "typing_sound")
        c.check(ed, "Play the transition sound when this scene arrives", "transition_sound")

        # ---------------------------------------------------------- music tracks column (right of the editing panel)
        ttk.Style(self).configure("TSpinbox", fieldbackground=theme.CARD, background=theme.CARD, foreground=theme.TEXT,
                                  insertcolor=theme.TEXT, bordercolor=theme.BORDER, arrowcolor=theme.MUTED, padding=4)
        mid = ttk.Frame(outer, width=345)
        mid.pack(side="left", fill="y")
        mid.pack_propagate(False)
        ttk.Label(mid, text="Music tracks", style="Section.TLabel").pack(anchor="w", padx=16, pady=(16, 6))
        msf = ScrollFrame(mid)
        msf.pack(fill="both", expand=True)
        tp = ttk.Frame(msf.inner)
        tp.pack(fill="both", expand=True, padx=(14, 18), pady=(0, 16))
        tc = self.tc
        self.tr_tree = ttk.Treeview(tp, columns=("n", "name", "start", "end"), show="headings", height=5,
                                    selectmode="browse", style="Scenes.Treeview")
        for col, text, w, anc in (("n", "#", 24, "center"), ("name", "Track", 150, "w"), ("start", "Start", 50, "w"), ("end", "End", 50, "w")):
            self.tr_tree.heading(col, text=text)
            self.tr_tree.column(col, width=w, anchor=anc, stretch=(col == "name"))
        self.tr_tree.pack(fill="x", pady=(0, 4))
        self.tr_tree.bind("<<TreeviewSelect>>", self._on_track_select)
        for rowdef in ((("Add tracks…", self.add_tracks), ("Remove", self.remove_track)),
                       (("Move up", lambda: self.move_track(-1)), ("Move down", lambda: self.move_track(1)))):
            row = ttk.Frame(tp)
            row.pack(fill="x", pady=(4, 0))
            for text, cmd in rowdef:
                ttk.Button(row, text=text, command=cmd).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Label(tp, text="Timeline  (drag a bar to move it, drag its edges to trim; it snaps to scene starts)",
                  style="Muted.TLabel", wraplength=300).pack(anchor="w", pady=(12, 3))
        self.lane_cv = tk.Canvas(tp, height=150, bg=theme.BG, highlightthickness=1, highlightbackground=theme.BORDER)
        self.lane_cv.pack(fill="x")
        self.lane_cv.bind("<Configure>", lambda e: self._draw_lanes())
        self.lane_cv.bind("<Button-1>", self._lane_press)
        self.lane_cv.bind("<B1-Motion>", self._lane_move)
        self.lane_cv.bind("<ButtonRelease-1>", self._lane_release)
        ttk.Button(tp, text="▶  Play the whole video with the music", style="Accent.TButton",
                   command=self.play_all).pack(fill="x", pady=(8, 0))
        row = ttk.Frame(tp)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Spread evenly", command=lambda: self.arrange(True)).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(row, text="Back to back", command=lambda: self.arrange(False)).pack(side="left", expand=True, fill="x", padx=2)
        self.pc.slider(tp, "Crossfade length", "crossfade", 0, 8, 0.1, " s")
        ttk.Label(tp, text="Spread evenly splits the video between the tracks. Back to back plays each song at its natural "
                  "length. Both overlap neighbours by the crossfade.", style="Muted.TLabel", wraplength=300).pack(anchor="w", pady=(2, 0))
        tc.section(tp, "Selected track")
        ttk.Button(tp, text="✂  Crop the song…  (waveform, start / end, listen)", style="Accent.TButton",
                   command=self.crop_track).pack(fill="x", pady=(4, 2))
        self._spin(tp, "Starts at", "t_start", 0, 3600, 0.1)
        self._spin(tp, "Ends at  (0 = end of video)", "t_end", 0, 3600, 0.1)
        row = ttk.Frame(tp)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Start = playhead", command=lambda: self._set_from_playhead("t_start")).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(row, text="End = playhead", command=lambda: self._set_from_playhead("t_end")).pack(side="left", expand=True, fill="x", padx=2)
        self.t_at_cb = tc.combo(tp, "Start at the beginning of scene", "t_start", [], var=self.t_at, on_pick=lambda v: self._to_scene(v, False))
        self.t_until_cb = tc.combo(tp, "End at the end of scene", "t_end", [], var=self.t_until, on_pick=lambda v: self._to_scene(v, True))
        self._spin(tp, "Begin inside the song at", "t_offset", 0, 3600, 0.5)
        self._spin(tp, "Stop inside the song at  (0 = end)", "t_src_end", 0, 3600, 0.5)
        tc.slider(tp, "Volume", "t_volume", 0, 150, 1, "%")
        tc.slider(tp, "Fade in", "t_fade_in", 0, 8, 0.1, " s")
        tc.slider(tp, "Fade out", "t_fade_out", 0, 8, 0.1, " s")
        tc.check(tp, "Repeat the song to fill its time on the timeline", "t_loop")

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
        c.check(rp, "Show end card", "outro")
        ttk.Label(rp, text="Write anything, in any language (Amharic needs an Ethiopic font, see below). An empty line is "
                  "hidden. Select the End card row, then use the button under the preview to drag and resize every part.",
                  style="Muted.TLabel", wraplength=320).pack(anchor="w", pady=(2, 0))
        c.entry(rp, "App name", "app_name")
        c.entry(rp, "Tagline", "tagline")
        c.entry(rp, "Big line  (empty = hidden)", "outro_headline")
        c.entry(rp, "Small line  (empty = hidden)", "outro_subline")
        c.check(rp, "Show the store badge", "outro_badge")
        c.entry(rp, "Badge text, small top line", "outro_badge_small")
        c.entry(rp, "Badge text, big line", "outro_badge_big")
        c.font_picker(rp, "End card font  (Auto picks an Ethiopic font for Amharic)", "outro_font", prefer="Bold")
        c.slider(rp, "End card length", "outro_duration", 2, 6, 0.1, " s")
        ttk.Button(rp, text="Reset end card positions", command=self.reset_layout).pack(fill="x", pady=(8, 0))
        c.section(rp, "Audio tracks")
        ttk.Label(rp, text="Background music is set up in the Music tracks column.", style="Muted.TLabel",
                  wraplength=320).pack(anchor="w", pady=(2, 0))
        c.slider(rp, "Overall music volume", "music_volume", 0, 100, 1, "%")
        c.slider(rp, "Fade in at the start of the video", "music_fade_in", 0, 4, 0.1, " s")
        c.slider(rp, "Fade out at the end of the video", "music_fade_out", 0, 4, 0.1, " s")
        self._file_row(rp, "Typing sound (looped while text is typed)", "typing_sound")
        c.slider(rp, "Typing volume", "typing_volume", 0, 100, 1, "%")
        self._file_row(rp, "Transition sound (each scene change)", "whoosh_sound")
        c.slider(rp, "Transition volume", "whoosh_volume", 0, 100, 1, "%")
        self._file_row(rp, "Tap sound (a click on every tap)", "tap_sound")
        c.slider(rp, "Tap volume", "tap_volume", 0, 100, 1, "%")
        c.check(rp, "Lower the music while typing", "duck")
        c.section(rp, "Output")
        c.combo(rp, "Quality", "quality", list(config.QUALITY))
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
        row = ttk.Frame(center, style="Bg.TFrame")
        row.pack(pady=(0, 12))
        ttk.Button(row, textvariable=self.edit_txt, command=self.toggle_edit).pack(side="left")
        ttk.Button(row, text="Reset text position", command=self.reset_layout).pack(side="left", padx=(6, 0))
        self.canvas.bind("<Configure>", lambda e: self._show(self.t_var.get()))
        self.canvas.bind("<Motion>", self._canvas_motion)
        self.canvas.bind("<MouseWheel>", self._edit_wheel)
        self.canvas.bind("<Button-4>", self._edit_wheel)
        self.canvas.bind("<Button-5>", self._edit_wheel)
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)

    # --------------------------------------------------------------- scene list
    def _effect_label(self, sc):
        eff = self.project.text_effect if sc.text_effect == "default" else sc.text_effect
        return eff + ("" if sc.text_effect == "default" else " •")

    def _row(self, i):
        sc = self.project.scenes[i]
        name = "Blank canvas" if is_blank(sc.image) else (sc.image if len(sc.image) < 14 else "…" + sc.image[-12:])
        return (("▶ " if is_video(sc.image) else "") + name, f"{sc.duration:g}", sc.motion,
                sc.transition if i else "intro", self._effect_label(sc))

    def _refresh_tree(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i in range(len(self.project.scenes)):
            self.tree.insert("", "end", iid=str(i), values=self._row(i))
        if self.project.has_outro():
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
        self._leave_edit()
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
        o = C.opts(sc.clip)
        for k in ("speed", "volume", "mute", "loop", "brightness", "contrast", "saturation"):
            e["clip_" + k].set(o[k])
        e["clip_fit"].set(bool(o["fit"]))
        self.ec.sync_texts()
        self._refresh_taps(select=0)
        self._refresh_callouts(select=0)
        self._loading = False
        self._goto(self._range()[0])

    def _editor_changed(self, *_):
        i = self._selected()
        if self._loading or i is None:
            return
        sc, e = self.project.scenes[i], self.ev
        try:
            prev_dur, sc.duration = sc.duration, round(float(e["duration"].get()), 2)
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
            if self._co_i is not None and self._co_i < len(sc.callouts):
                a = round(float(e["co_start"].get()), 2)
                sc.callouts[self._co_i].update(
                    shape=e["co_shape"].get(), style=e["co_style"].get(), color=e["co_color"].get(),
                    x=round(float(e["co_x"].get()), 3), y=round(float(e["co_y"].get()), 3),
                    w=round(float(e["co_w"].get()), 3), h=round(float(e["co_h"].get()), 3),
                    rot=round(float(e["co_rot"].get()), 1), thickness=round(float(e["co_thickness"].get()), 2),
                    opacity=int(round(float(e["co_opacity"].get()))), start=a,
                    end=max(round(float(e["co_end"].get()), 2), a + 0.1),
                    anim_in=e["co_in"].get(), anim_out=e["co_out"].get(),
                    anim_time=round(float(e["co_time"].get()), 2), pulse=bool(e["co_pulse"].get()))
                self.co_tree.item(str(self._co_i), values=self._co_row(sc.callouts[self._co_i]))
            sc.text_style = ({k: (e[k].get() if k in ("text_align", "title_color", "subtitle_color") else round(float(e[k].get()), 2))
                              for k in TEXT_STYLE} if e["style_on"].get() else {})
            sc.shot = ({"shot_fit": e["shot_fit"].get(), "shot_zoom": round(float(e["shot_zoom"].get()), 1),
                        "shot_pan_x": round(float(e["shot_pan_x"].get()), 1), "shot_pan_y": round(float(e["shot_pan_y"].get()), 1)}
                       if e["shot_on"].get() else {})
            if is_video(sc.image):
                before = C.opts(sc.clip)["speed"]
                raw = float(e["clip_speed"].get())
                # keep the exact stretched speed unless the slider was really moved (so other edits never nudge the length)
                speed = round(before, 4) if abs(raw - before) < 0.005 else round(raw, 2)
                sc.clip = {**sc.clip, "speed": speed, "fit": bool(e["clip_fit"].get()),
                           "volume": int(round(float(e["clip_volume"].get()))), "mute": bool(e["clip_mute"].get()),
                           "loop": bool(e["clip_loop"].get()), "brightness": int(round(float(e["clip_brightness"].get()))),
                           "contrast": int(round(float(e["clip_contrast"].get()))),
                           "saturation": int(round(float(e["clip_saturation"].get())))}
                if abs(sc.clip["speed"] - before) > 1e-6:  # a different speed means a different length
                    self._fit_duration(sc)
                elif sc.clip["fit"] and abs(sc.duration - prev_dur) > 1e-6:  # a different length means a different speed
                    self._fit_speed(sc)
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
        if self._restoring:
            return
        P, v = self.project, self.pv
        try:
            P.app_name, P.tagline, P.cta = v["app_name"].get(), v["tagline"].get(), v["cta"].get()
            P.outro, P.outro_duration = v["outro"].get(), round(float(v["outro_duration"].get()), 2)
            P.fps, P.width = int(v["fps"].get() or 30), int(v["width"].get() or 1080)
            P.text_effect, P.type_speed = v["text_effect"].get(), round(float(v["type_speed"].get()), 1)
            P.music, P.typing_sound, P.whoosh_sound = v["music"].get(), v["typing_sound"].get(), v["whoosh_sound"].get()
            P.music_volume, P.typing_volume, P.whoosh_volume = (int(round(float(v[k].get()))) for k in ("music_volume", "typing_volume", "whoosh_volume"))
            P.music_fade_in, P.music_fade_out = round(float(v["music_fade_in"].get()), 2), round(float(v["music_fade_out"].get()), 2)
            P.duck, P.quality = v["duck"].get(), v["quality"].get()
            P.crossfade = round(float(v["crossfade"].get()), 2)
            P.tap_sound, P.tap_volume = v["tap_sound"].get(), int(round(float(v["tap_volume"].get())))
            P.outro_headline, P.outro_subline = v["outro_headline"].get(), v["outro_subline"].get()
            P.outro_badge = bool(v["outro_badge"].get())
            P.outro_badge_small, P.outro_badge_big = v["outro_badge_small"].get(), v["outro_badge_big"].get()
            P.outro_font = "" if v["outro_font"].get() in ("", "Auto") else v["outro_font"].get()
        except (tk.TclError, ValueError):
            return
        self._refresh_tree(select=self._selected())
        self._changed()

    def _changed(self):
        self._fr = None
        self._later("save", 600, self._save)
        self._later("show", 120, self._after_change)
        self._later("audio", 450, self._refresh_audio)
        self._later("lanes", 150, self._draw_lanes)

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
        if is_blank(sc.image):
            self.status.set("A blank canvas has no phone screen to tap. Select a phone scene.")
            return
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
        self._picking, self._playing, self._pick_kind = True, False, "tap"
        self.player.stop()
        self.play_btn.config(text="Play")
        self.canvas.config(cursor="crosshair")
        self.status.set("Click on the phone screen where the tap should happen.")
        self.t_var.set(self._pick_t)
        self._show(self._pick_t)

    def _canvas_click(self, ev):
        if not self._picking or not self._shown or self._exporting:
            return
        if self._pick_kind == "text":
            self._edit_press(ev)
            return
        if self._pick_kind == "region":  # a highlight is placed by dragging: press here, release at the far corner
            self._drag = (ev.x, ev.y)
            return
        cx, cy, k, w0, h0 = self._shown
        fx, fy = (ev.x - cx) / k + w0 / 2, (ev.y - cy) / k + h0 / 2
        x, y = self._renderer().screen_point(self._pick_t, fx, fy)
        self._picking = False
        self.canvas.config(cursor="")
        self.ev["tap_x"].set(round(x, 3))
        self.ev["tap_y"].set(round(y, 3))
        self.status.set("Tap position set.")

    # ----------------------------------------------------------------- highlights
    def _callouts(self):
        i = self._selected()
        return self.project.scenes[i].callouts if i is not None else None

    @staticmethod
    def _co_row(c):
        return (c["shape"], f"{c['start']:g} - {c['end']:g} s", c["style"])

    def _refresh_callouts(self, select=None):
        self.co_tree.delete(*self.co_tree.get_children())
        items = self._callouts() or []
        for k, c in enumerate(items):
            self.co_tree.insert("", "end", iid=str(k), values=self._co_row({**CO.DEFAULTS, **c}))
        if select is not None and items:
            self._co_i = min(select, len(items) - 1)
            self.co_tree.selection_set(str(self._co_i))
        else:
            self._co_i = None

    def _on_co_select(self, _=None):
        sel, items = self.co_tree.selection(), self._callouts()
        if not sel or items is None or int(sel[0]) >= len(items):
            self._co_i = None
            return
        self._co_i = int(sel[0])
        c, e = {**CO.DEFAULTS, **items[self._co_i]}, self.ev
        self._loading = True
        for key, var in (("shape", "co_shape"), ("style", "co_style"), ("color", "co_color"), ("x", "co_x"), ("y", "co_y"),
                         ("w", "co_w"), ("h", "co_h"), ("rot", "co_rot"), ("thickness", "co_thickness"),
                         ("opacity", "co_opacity"), ("start", "co_start"), ("end", "co_end"), ("anim_in", "co_in"),
                         ("anim_out", "co_out"), ("anim_time", "co_time"), ("pulse", "co_pulse")):
            e[var].set(c[key])
        self._loading = False

    def add_callout(self):
        i = self._selected()
        if i is None:
            return
        sc = self.project.scenes[i]
        if is_blank(sc.image):
            self.status.set("A blank canvas has no phone screen to highlight. Select a phone scene.")
            return
        segs, _ = self.project.segments()
        lt = self.t_var.get() - segs[i].start  # start where the preview is now
        a = round(min(max(lt, 0.3), max(0.3, sc.duration - 1.8)), 2)
        sc.callouts.append(CO.new_callout(a, round(min(a + 1.8, sc.duration), 2)))
        self._refresh_callouts(select=len(sc.callouts) - 1)
        self._changed()
        self._start_region()

    def remove_callout(self):
        items, k = self._callouts(), self._co_i
        if items is not None and k is not None and k < len(items):
            del items[k]
            self._refresh_callouts(select=k)
            self._changed()

    def _start_region(self):
        items, i = self._callouts(), self._selected()
        if items is None or self._co_i is None or self._co_i >= len(items):
            self.status.set("Add or select a highlight first.")
            return
        c = {**CO.DEFAULTS, **items[self._co_i]}
        segs, _ = self.project.segments()
        self._pick_t = segs[i].start + c["start"] + (c["end"] - c["start"]) * 0.5  # a moment where it is fully shown
        self._picking, self._playing, self._pick_kind, self._drag = True, False, "region", None
        self.player.stop()
        self.play_btn.config(text="Play")
        self.canvas.config(cursor="crosshair")
        self.status.set("Drag on the phone screen to cover the area (hold Shift for a square). Click once to just move it.")
        self.t_var.set(self._pick_t)
        self._show(self._pick_t)

    def _canvas_drag(self, ev):
        if self._picking and self._pick_kind == "text":
            self._edit_move(ev)
            return
        if not (self._picking and self._pick_kind == "region" and self._drag):
            return
        x0, y0 = self._drag
        x1, y1 = ev.x, ev.y
        if ev.state & 0x1:  # Shift: square
            side = max(abs(x1 - x0), abs(y1 - y0))
            x1, y1 = x0 + math.copysign(side, x1 - x0 or 1), y0 + math.copysign(side, y1 - y0 or 1)
        self.canvas.delete("rubber")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#FFFFFF", dash=(5, 3), width=2, tags="rubber")

    def _canvas_release(self, ev):
        if self._picking and self._pick_kind == "text":
            self._edit_release(ev)
            return
        if not (self._picking and self._pick_kind == "region" and self._drag and self._shown and not self._exporting):
            return
        x0, y0 = self._drag
        x1, y1 = ev.x, ev.y
        if ev.state & 0x1:
            side = max(abs(x1 - x0), abs(y1 - y0))
            x1, y1 = x0 + math.copysign(side, x1 - x0 or 1), y0 + math.copysign(side, y1 - y0 or 1)
        self._drag = None
        self.canvas.delete("rubber")
        cx, cy, k, w0, h0 = self._shown
        r = self._renderer()
        ax, ay = r.screen_point(self._pick_t, (x0 - cx) / k + w0 / 2, (y0 - cy) / k + h0 / 2)
        bx, by = r.screen_point(self._pick_t, (x1 - cx) / k + w0 / 2, (y1 - cy) / k + h0 / 2)
        self._picking = False
        self.canvas.config(cursor="")
        e = self.ev
        if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:  # a plain click: keep the size, move the centre
            e["co_x"].set(round(ax, 3))
            e["co_y"].set(round(ay, 3))
        else:
            e["co_x"].set(round((ax + bx) / 2, 3))
            e["co_y"].set(round((ay + by) / 2, 3))
            e["co_w"].set(round(max(0.03, abs(bx - ax)), 3))
            e["co_h"].set(round(max(0.03, abs(by - ay)), 3))
        self.status.set("Highlight placed. Press play to watch it animate.")

    # scene actions
    def _add(self, names):
        v, errors = config.VIDEO["scene_duration"], []
        for n in names:
            sc = make_scene(len(self.project.scenes), n, v)
            err = self._init_video(sc)
            if err:
                errors.append(f"{n}: {err}")
            self.project.scenes.append(sc)
        self._refresh_tree(select=len(self.project.scenes) - 1)
        self._changed()
        if errors:
            self.status.set("Could not read " + "; ".join(errors))

    def add_scenes(self):
        for_ = filedialog.askopenfilenames(parent=self, initialdir=self.paths.raw, filetypes=MEDIA_TYPES)
        self._add([Path(p).name if Path(p).parent.resolve() == self.paths.raw.resolve() else str(Path(p)) for p in for_])

    def add_blank(self):
        """A scene with no phone: just the background and your text. Inserted after the selected scene."""
        i = self._selected()
        at = len(self.project.scenes) if i is None else i + 1
        self.project.scenes.insert(at, make_blank(at, config.VIDEO["scene_duration"]))
        self._refresh_tree(select=at)
        self._changed()
        self.status.set("Blank canvas added. Edit its title and subtitle on the right; 'Move vertically' nudges the text up or down.")

    def add_all(self):
        files = sorted((p for p in self.paths.raw.iterdir() if p.suffix.lower() in config.MEDIA_EXT), key=natural_key)
        self._add([f.name for f in files])

    # -- video clip editing
    def _video_scene(self):
        """(index, scene) of the selected scene if it is a video, else (None, None) with a hint."""
        i = self._selected()
        if i is None or not is_video(self.project.scenes[i].image):
            self.status.set("Select a video scene first (they have a ▶ in the list).")
            return None, None
        return i, self.project.scenes[i]

    def _fit_duration(self, sc):
        """Scene length = length of the (trimmed, speed-adjusted) clip."""
        try:
            info = C.probe(find_ffmpeg(), resolve_image(self.paths.raw, sc.image))
        except Exception:
            return
        sc.duration = round(C.play_len(info, C.opts(sc.clip)), 2)
        was, self._loading = self._loading, True
        self.ev["duration"].set(sc.duration)
        self._loading = was

    def _fit_speed(self, sc):
        """Stretch: speed = (kept part of the clip) / (scene length), so the whole clip fills the scene."""
        try:
            info = C.probe(find_ffmpeg(), resolve_image(self.paths.raw, sc.image))
        except Exception:
            return
        o = C.opts(sc.clip)
        kept = C.span(info, o)[1]
        want = kept / max(0.1, sc.duration)
        speed = min(16.0, max(0.1, want))
        sc.clip = {**sc.clip, "speed": round(speed, 4)}
        was, self._loading = self._loading, True
        self.ev["clip_speed"].set(sc.clip["speed"])
        if abs(speed - want) > 1e-6:  # beyond the supported range: the scene length follows instead
            self._fit_duration(sc)
            self.status.set(f"Speed is limited to {speed:g}×, so the scene is {sc.duration:g} s.")
        else:
            self.status.set(f"The {kept:.1f} s of video now plays at {speed:.2f}× to fill {sc.duration:g} s.")
        self._loading = was

    def fit_scene(self):
        i, sc = self._video_scene()
        if sc is not None:
            self._fit_duration(sc)
            self._refresh_tree(select=i)
            self._changed()
            self.status.set(f"Scene length set to {sc.duration:g} s, the length of the clip.")

    def edit_clip(self):
        i, sc = self._video_scene()
        if sc is None:
            return
        try:
            ff = find_ffmpeg()
            sw, sh = self._renderer().screen_size
        except Exception as exc:
            messagebox.showerror("Cannot open the editor", str(exc), parent=self)
            return
        self._playing = False
        self.player.stop()
        self.play_btn.config(text="Play")
        ClipEditor(self, ff, resolve_image(self.paths.raw, sc.image), sc.clip, sw / sh,
                   lambda new: self._clip_edited(i, new))

    def _clip_edited(self, i, new):
        sc = self.project.scenes[i]
        sc.clip = {**sc.clip, **new}
        self._fit_duration(sc)
        self._refresh_tree(select=i)
        self._changed()
        self.status.set(f"Clip updated: this scene now runs {sc.duration:g} s.")

    def split_clip(self):
        """Cut the video scene in two at the playhead (like the blade tool). Delete either half to remove a section."""
        i, sc = self._video_scene()
        if sc is None:
            return
        try:
            info = C.probe(find_ffmpeg(), resolve_image(self.paths.raw, sc.image))
        except Exception as exc:
            self.status.set(str(exc))
            return
        o = C.opts(sc.clip)
        tin, sp = C.span(info, o)
        lt = self.t_var.get() - self.project.segments()[0][i].start
        src = tin + lt * o["speed"]  # playhead -> position in the source video
        if lt >= sc.duration or not tin + 0.2 < src < tin + sp - 0.2:
            self.status.set("Move the playhead inside this clip, at least 0.2 s away from both ends, then split.")
            return
        a, b = copy.deepcopy(sc), copy.deepcopy(sc)
        a.clip, b.clip = {**sc.clip, "trim_out": round(src, 3)}, {**sc.clip, "trim_in": round(src, 3)}
        b.transition, b.transition_duration, b.taps, b.callouts, b.text_effect = "cut", 0.0, [], [], "default"  # seamless continuation
        b.typing_sound = b.transition_sound = False
        b.motion = "+".join(m for m in b.motion.split("+") if m in AMBIENT) or "float"  # no second entrance animation
        for part in (a, b):
            part.duration = round(C.play_len(info, C.opts(part.clip)), 2)
        self.project.scenes[i:i + 1] = [a, b]
        self._refresh_tree(select=i)
        self._changed()
        self.status.set("Split in two. Remove either half to cut that section out, or give the second one a transition.")

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

    # ----------------------------------------------------------------- music tracks
    PALETTE = ("#7B61FF", "#2DD4BF", "#FF7A59", "#4F9DFF", "#F5C542", "#E86AA6", "#8BD450", "#B3A5FF")

    def _adopt_music(self, proj):
        """A project from before multi-track music has one file in `music`: it becomes the first track."""
        if proj.music and not proj.tracks:
            ln = 0.0
            try:
                ln = audio_len(find_ffmpeg(), proj.music)
            except Exception:
                pass
            proj.tracks = [dict(TRACK_DEFAULTS, path=proj.music, loop=True, len=round(ln, 2))]
            proj.music = ""
        return proj

    def _spin(self, parent, label, key, lo, hi, inc):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=(6, 0))
        ttk.Label(f, text=label).pack(side="left")
        ttk.Label(f, text="s", style="Muted.TLabel").pack(side="right")
        ttk.Spinbox(f, textvariable=self.tv[key], from_=lo, to=hi, increment=inc, width=8).pack(side="right", padx=(0, 4))

    @staticmethod
    def _tr_row(k, t):
        t = {**TRACK_DEFAULTS, **t}
        name = Path(t["path"]).name
        name = ("✂ " if (t["offset"] > 0 or t["src_end"] > 0) else "") + ("↻ " if t["loop"] else "") + ("⚠ " if not Path(t["path"]).is_file() else "") + (name if len(name) < 24 else name[:22] + "…")
        return (str(k + 1), name, f"{t['start']:g}", "end" if t["end"] <= 0 else f"{t['end']:g}")

    def _refresh_tracks(self, select=None):
        if not hasattr(self, "tr_tree"):
            return
        tracks = self.project.tracks
        self.tr_tree.delete(*self.tr_tree.get_children())
        for k, t in enumerate(tracks):
            self.tr_tree.insert("", "end", iid=str(k), values=self._tr_row(k, t))
        k = select if select is not None else self._tr_i
        if tracks:
            self._tr_i = min(max(k or 0, 0), len(tracks) - 1)
            self.tr_tree.selection_set(str(self._tr_i))
        else:
            self._tr_i = None
        self._load_track_vars()
        labels = [f"Scene {sg.idx + 1}" if not sg.outro else "End card" for sg in self.project.segments()[0]]
        self.t_at_cb.config(values=labels)
        self.t_until_cb.config(values=labels)
        self._draw_lanes()

    def _load_track_vars(self):
        v, k = self.tv, self._tr_i
        t = {**TRACK_DEFAULTS, **(self.project.tracks[k] if k is not None and k < len(self.project.tracks) else {})}
        self._tloading = True
        try:
            v["t_start"].set(t["start"])
            v["t_end"].set(t["end"])
            v["t_offset"].set(t["offset"])
            v["t_src_end"].set(t["src_end"])
            v["t_volume"].set(t["volume"])
            v["t_fade_in"].set(t["fade_in"])
            v["t_fade_out"].set(t["fade_out"])
            v["t_loop"].set(bool(t["loop"]))
        finally:
            self._tloading = False

    def _on_track_select(self, _=None):
        sel = self.tr_tree.selection()
        if not sel or int(sel[0]) >= len(self.project.tracks):
            return
        self._tr_i = int(sel[0])
        self._load_track_vars()
        self._draw_lanes()

    def _track_changed(self, *_):
        k = self._tr_i
        if self._tloading or k is None or k >= len(self.project.tracks):
            return
        t, v = self.project.tracks[k], self.tv
        try:
            a, b = max(0.0, round(float(v["t_start"].get()), 2)), max(0.0, round(float(v["t_end"].get()), 2))
            if b > 0:
                b = max(b, a + 0.2)
            off = max(0.0, round(float(v["t_offset"].get()), 2))
            cut = max(0.0, round(float(v["t_src_end"].get()), 2))
            if 0 < cut < off + 0.2:
                cut = round(off + 0.2, 2)
            t.update(start=a, end=b, offset=off, src_end=cut,
                     volume=int(round(float(v["t_volume"].get()))), fade_in=round(float(v["t_fade_in"].get()), 2),
                     fade_out=round(float(v["t_fade_out"].get()), 2), loop=bool(v["t_loop"].get()))
        except (tk.TclError, ValueError):
            return
        self.tr_tree.item(str(k), values=self._tr_row(k, t))
        self._draw_lanes()
        self._changed()

    def _set_tv(self, **kw):
        self._tloading = True
        try:
            for key, val in kw.items():
                self.tv[key].set(val)
        finally:
            self._tloading = False

    def _set_from_playhead(self, key):
        if self._tr_i is None:
            self.status.set("Select a track first.")
            return
        self.tv[key].set(round(float(self.t_var.get()), 2))

    def _to_scene(self, label, end):
        segs = self.project.segments()[0]
        names = [f"Scene {sg.idx + 1}" if not sg.outro else "End card" for sg in segs]
        if self._tr_i is None or label not in names:
            return
        sg = segs[names.index(label)]
        self.tv["t_end" if end else "t_start"].set(round(sg.start + (sg.dur if end else 0.0), 2))

    def add_tracks(self):
        files = filedialog.askopenfilenames(parent=self, title="Choose background music (pick several)",
                                            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"), ("All files", "*.*")])
        if not files:
            return
        try:
            ff = find_ffmpeg()
        except RuntimeError:
            ff = None
        P, total = self.project, self._total()
        playlist = not P.tracks and len(files) > 1
        for f in files:
            t = dict(TRACK_DEFAULTS, path=str(f), len=round(audio_len(ff, f), 2) if ff else 0.0)
            if P.tracks and total > 0:  # follow the last track if there is room, otherwise start at the beginning
                prev = P.tracks[-1]
                pe = track_end(prev, total)
                if pe < total - 0.5:
                    t["start"] = round(max(0.0, pe - P.crossfade), 2)
                    t["fade_in"] = prev["fade_out"] = round(P.crossfade, 2)
            P.tracks.append(t)
        self._refresh_tracks(select=len(P.tracks) - len(files))
        if playlist and total > 0:
            self.arrange(True)
        else:
            self._changed()
            self.status.set(f"{len(files)} track(s) added. Drag them on the timeline, or use Spread evenly / Back to back.")

    def remove_track(self):
        k = self._tr_i
        if k is not None and k < len(self.project.tracks):
            del self.project.tracks[k]
            self._refresh_tracks(select=k)
            self._changed()

    def move_track(self, d):
        k, tr = self._tr_i, self.project.tracks
        if k is not None and 0 <= k + d < len(tr):
            tr[k], tr[k + d] = tr[k + d], tr[k]
            self._refresh_tracks(select=k + d)
            self._changed()
            self.status.set("Order changed. Spread evenly / Back to back lay the tracks out in this order.")

    def arrange(self, even):
        """Lay the tracks out in list order with crossfades. even=True: share the video equally;
        False: each song at its natural length, one after another."""
        tr, total = self.project.tracks, self._total()
        n = len(tr)
        if not n:
            self.status.set("Add some tracks first.")
            return
        if total <= 0:
            self.status.set("Add scenes first, the tracks are laid out against the video length.")
            return
        if even:
            seg = (total + self.project.crossfade * (n - 1)) / n
            cf = min(self.project.crossfade, seg / 2) if n > 1 else 0.0
            seg = (total + cf * (n - 1)) / n
            for k, t in enumerate(tr):
                t["start"] = round(k * (seg - cf), 2)
                t["end"] = 0.0 if k == n - 1 else round(t["start"] + seg, 2)
                avail = usable_len(t)
                t["loop"] = avail <= 0 or avail < seg - cf  # a song clearly shorter than its slot repeats (a tiny gap hides in the fade)
        else:
            cf, cursor = min(self.project.crossfade, total / (2 * n)), 0.0
            for k, t in enumerate(tr):
                avail = usable_len(t)
                t["loop"] = False
                t["start"] = round(cursor, 2)
                if avail <= 0:  # unknown length: share what is left
                    t["end"], t["loop"] = 0.0, True
                    break
                t["end"] = round(cursor + avail, 2)
                cursor += avail - cf
            if tr[-1]["end"] > 0 and tr[-1]["end"] >= total - 0.01:
                tr[-1]["end"] = 0.0
        for k, t in enumerate(tr):
            t["fade_in"] = round(cf, 2) if k else 0.0
            t["fade_out"] = round(cf, 2) if k < n - 1 else 0.0
        self._refresh_tracks()
        self._changed()
        self.status.set(("Spread evenly" if even else "Back to back") + f": {n} track(s), {cf:g} s crossfades. Press play to listen.")

    # ------------------------------------------------------------- crop a song
    def crop_track(self):
        """Open the waveform cropper for the selected music track."""
        k = self._tr_i
        if k is None or k >= len(self.project.tracks):
            self.status.set("Select a music track first (or add one with Add tracks…).")
            return
        t = {**TRACK_DEFAULTS, **self.project.tracks[k]}
        if not Path(t["path"]).is_file():
            self.status.set("This track's file cannot be found.")
            return
        try:
            ff = find_ffmpeg()
        except Exception as exc:
            messagebox.showerror("FFmpeg", str(exc), parent=self)
            return
        self._playing = False
        self.player.stop()
        self.play_btn.config(text="Play")
        ln = float(t["len"]) or audio_len(ff, t["path"])
        AudioCropper(self, ff, t["path"], ln, float(t["offset"]), float(t["src_end"]),
                     lambda a, b, fit, k=k: self._cropped(k, a, b, fit))

    def _cropped(self, k, a, b, fit):
        if k >= len(self.project.tracks):
            return
        t = self.project.tracks[k]
        full = float(t.get("len", 0) or 0)
        t["offset"] = round(a, 2)
        t["src_end"] = 0.0 if (full and b >= full - 0.05) else round(b, 2)
        if fit:
            total, length = self._total(), b - a
            t["loop"] = False
            end = float(t.get("start", 0.0)) + length
            t["end"] = 0.0 if (total and end >= total - 0.01) else round(end, 2)
        self._refresh_tracks(select=k)
        self._changed()
        self.status.set(f"Using {fmt_time(a)} to {fmt_time(b)} of the song ({b - a:.1f} s). Press play to listen in the video.")

    # ------------------------------------------- move / resize text on the preview
    def _outro_selected(self):
        sel = self.tree.selection()
        return bool(sel) and sel[0] == "outro"

    def _leave_edit(self):
        self._edit_sel, self._edit_drag, self._edit_guide = None, None, None
        self.edit_txt.set(EDIT_LABEL)

    def toggle_edit(self):
        """Freeze the preview on a moment where the text is fully visible so it can be grabbed with the mouse."""
        if self._picking and self._pick_kind == "text":
            self._picking = False
            self._leave_edit()
            self.canvas.config(cursor="")
            self._show(self._pick_t)
            self.status.set("Text position saved.")
            return
        if not self.project.scenes:
            return
        outro, i = self._outro_selected(), self._selected()
        if not outro and i is None:
            self.status.set("Select a scene (or the End card row) first.")
            return
        self._playing = False
        self.player.stop()
        self.play_btn.config(text="Play")
        segs, _ = self.project.segments()
        seg = segs[-1] if outro else segs[i]
        if outro and not seg.outro:
            return
        r = self._renderer()
        if outro:
            lt = 2.4
        else:
            lt = 1.4
            try:
                plan = r._plan(seg) if r._effect(seg) == "typing" else None
                if plan:
                    lt = (plan["s1"] if plan["ns"] else plan["t1"]) + 0.4
            except Exception:
                pass
        lt = min(max(lt, 1.2), seg.dur - 0.05)
        self._pick_t, self._pick_kind, self._picking = seg.start + lt, "text", True
        self._edit_sel, self._edit_drag = None, None
        self.edit_txt.set("✔  Done")
        self.mode.set("scene")
        self.t_var.set(self._pick_t)
        self._show(self._pick_t)
        if not self._edit_items():
            self.status.set("Nothing to move here: this scene shows no text." if not outro else "The end card has nothing to show yet.")
        else:
            self.status.set(EDIT_HINT)

    def reset_layout(self):
        """Selected scene: back to the look's text position / size. End card selected: all elements back to default."""
        if self._outro_selected():
            self.project.outro_layout = {}
            self._leave_edit_keep()
        else:
            i = self._selected()
            if i is None:
                return
            sc = self.project.scenes[i]
            sc.text_style = {}
            if is_blank(sc.image):
                sc.phone_dy = 0.0
            self._on_select_reload(i)
        self._changed()
        self.status.set("Position reset.")

    def _leave_edit_keep(self):
        self._edit_sel, self._edit_drag = None, None

    def _on_select_reload(self, i):
        """Re-read the selected scene into the editor controls (used after a reset)."""
        keep = self._picking and self._pick_kind == "text"
        self._on_select()
        if keep:
            self.toggle_edit()

    def _edit_items(self):
        """{key: (x0, y0, x1, y1)} in preview-frame pixels of everything that can be grabbed right now."""
        r = self._renderer()
        if self._outro_selected():
            if not r.outro_boxes:
                r.frame(self._pick_t)
            return dict(r.outro_boxes)
        i = self._selected()
        if i is None or i >= len(r.segs):
            return {}
        box = r.text_box(r.segs[i])
        return {"text": box} if box else {}

    def _to_canvas(self, x, y):
        cx, cy, k, w0, h0 = self._shown
        return cx + (x - w0 / 2) * k, cy + (y - h0 / 2) * k

    def _to_frame(self, x, y):
        cx, cy, k, w0, h0 = self._shown
        return (x - cx) / k + w0 / 2, (y - cy) / k + h0 / 2

    def _draw_edit_overlay(self):
        self.canvas.delete("edit")
        if not (self._picking and self._pick_kind == "text" and self._shown) or self._exporting:
            return
        try:
            items = self._edit_items()
        except Exception:
            return
        for key, (x0, y0, x1, y1) in items.items():
            a, b = self._to_canvas(x0, y0), self._to_canvas(x1, y1)
            sel = key == self._edit_sel
            self.canvas.create_rectangle(a[0], a[1], b[0], b[1], outline=theme.ACCENT_SOFT if sel else "#FFFFFF",
                                         dash=None if sel else (4, 3), width=2 if sel else 1, tags="edit")
            if sel:
                for hx, hy in ((a[0], a[1]), (b[0], a[1]), (a[0], b[1]), (b[0], b[1])):
                    self.canvas.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill=theme.ACCENT, outline="#FFFFFF", tags="edit")
        if self._edit_guide:
            cx, cy, k, w0, h0 = self._shown
            self.canvas.create_line(cx, cy - h0 * k / 2, cx, cy + h0 * k / 2, fill="#FF5AA5", dash=(3, 3), tags="edit")

    def _scene_style(self, sc):
        """The scene's own text style, filled with whatever the editor currently shows for the missing keys."""
        st, e = dict(sc.text_style or {}), self.ev
        for k in TEXT_STYLE:
            if k not in st:
                v = e[k].get()
                st[k] = str(v) if k in STR_STYLE else round(float(v), 2)
        sc.text_style = st
        return st

    def _edit_base(self, key):
        if key == "text":
            sc = self.project.scenes[self._selected()]
            st = self._scene_style(sc)
            return dict(text_x=float(st["text_x"]), text_y=float(st["text_y"]), title_size=float(st["title_size"]),
                        subtitle_size=float(st["subtitle_size"]), phone_dy=float(sc.phone_dy))
        lay = (self.project.outro_layout or {}).get(key, {})
        return dict(dx=float(lay.get("dx", 0.0)), dy=float(lay.get("dy", 0.0)), scale=float(lay.get("scale", 1.0)))

    def _apply_edit(self, key, base, dx, dy, f):
        """dx, dy in preview pixels, f = size factor, all relative to `base` (the values when the drag began)."""
        W, H = self._shown[3], self._shown[4]
        if key == "text":
            sc = self.project.scenes[self._selected()]
            st = self._scene_style(sc)
            st["text_x"] = round(base["text_x"] + dx / W * 100, 2)
            if is_blank(sc.image):  # a blank scene always centres its text: the vertical move is the scene's own offset
                sc.phone_dy = round(base["phone_dy"] + dy / H * 100, 2)
            else:
                st["text_y"] = round(base["text_y"] + dy / H * 100, 2)
            st["title_size"] = round(min(30.0, max(1.5, base["title_size"] * f)), 2)
            st["subtitle_size"] = round(min(16.0, max(0.8, base["subtitle_size"] * f)), 2)
        else:
            lay = self.project.outro_layout.setdefault(key, {})
            lay["dx"] = round(base["dx"] + dx / W * 100, 2)
            lay["dy"] = round(base["dy"] + dy / H * 100, 2)
            lay["scale"] = round(min(4.0, max(0.25, base["scale"] * f)), 3)

    def _commit_edit(self, key):
        """Dragging is done: copy the values into the editor controls and save."""
        if not self.winfo_exists():
            return
        if key == "text" and self._selected() is not None:
            sc, e = self.project.scenes[self._selected()], self.ev
            st = self._scene_style(sc)
            was, self._loading = self._loading, True
            try:
                e["style_on"].set(True)
                for k in ("text_x", "text_y", "title_size", "subtitle_size"):
                    e[k].set(st[k])
                e["phone_dy"].set(sc.phone_dy)
            finally:
                self._loading = was
        self._changed()

    def _hit(self, items, fx, fy, pad):
        cands = [((b[2] - b[0]) * (b[3] - b[1]), key) for key, b in items.items()
                 if b[0] - pad <= fx <= b[2] + pad and b[1] - pad <= fy <= b[3] + pad]
        return min(cands)[1] if cands else None

    def _on_handle(self, items, fx, fy):
        b = items.get(self._edit_sel)
        if not b:
            return False
        r = 10 / self._shown[2]
        return any(abs(fx - x) <= r and abs(fy - y) <= r for x in (b[0], b[2]) for y in (b[1], b[3]))

    def _edit_press(self, ev):
        items = self._edit_items()
        fx, fy = self._to_frame(ev.x, ev.y)
        if self._on_handle(items, fx, fy):
            mode, key = "resize", self._edit_sel
        else:
            key = self._hit(items, fx, fy, 6 / self._shown[2])
            mode = "move"
            if key is None:
                self._edit_sel = None
                self._show(self._pick_t)
                return
        self._edit_sel = key
        self._edit_drag = dict(mode=mode, key=key, ex=ev.x, ey=ev.y, fx=fx, fy=fy, box=items[key], base=self._edit_base(key))
        self._show(self._pick_t)

    def _edit_move(self, ev):
        d = self._edit_drag
        if not d or not self._shown:
            return
        k, W = self._shown[2], self._shown[3]
        if d["mode"] == "move":
            dx, dy = (ev.x - d["ex"]) / k, (ev.y - d["ey"]) / k
            mid = (d["box"][0] + d["box"][2]) / 2 + dx
            self._edit_guide = abs(mid - W / 2) < 6
            if self._edit_guide:  # snap to the horizontal centre
                dx += W / 2 - mid
            self._apply_edit(d["key"], d["base"], dx, dy, 1.0)
        else:
            x0, y0, x1, y1 = d["box"]
            c = ((x0 + x1) / 2, (y0 + y1) / 2)
            px, py = self._to_frame(ev.x, ev.y)
            f = math.hypot(px - c[0], py - c[1]) / (math.hypot(d["fx"] - c[0], d["fy"] - c[1]) or 1.0)
            self._apply_edit(d["key"], d["base"], 0.0, 0.0, f)
        self._show(self._pick_t)

    def _edit_release(self, ev):
        d, self._edit_drag, self._edit_guide = self._edit_drag, None, None
        if d:
            self._commit_edit(d["key"])
            self._show(self._pick_t)

    def _edit_wheel(self, ev):
        if not (self._picking and self._pick_kind == "text" and self._shown and not self._exporting):
            return
        delta = ev.delta if getattr(ev, "delta", 0) else (120 if ev.num == 4 else -120)
        if abs(delta) < 10:  # macOS reports small steps
            delta *= 120
        items = self._edit_items()
        fx, fy = self._to_frame(ev.x, ev.y)
        key = self._hit(items, fx, fy, 6 / self._shown[2]) or (self._edit_sel if self._edit_sel in items else None)
        if key is None and len(items) == 1:
            key = next(iter(items))
        if key is None:
            return
        self._edit_sel = key
        self._apply_edit(key, self._edit_base(key), 0.0, 0.0, 1.06 ** (delta / 120.0))
        self._show(self._pick_t)
        self._later("editcommit", 300, lambda: self._commit_edit(key))

    def _canvas_motion(self, ev):
        if not (self._picking and self._pick_kind == "text" and self._shown) or self._edit_drag:
            return
        try:
            items = self._edit_items()
            fx, fy = self._to_frame(ev.x, ev.y)
            if self._on_handle(items, fx, fy):
                cur = "sizing"
            else:
                cur = "fleur" if self._hit(items, fx, fy, 6 / self._shown[2]) else "arrow"
            self.canvas.config(cursor=cur)
        except tk.TclError:
            self.canvas.config(cursor="crosshair")
        except Exception:
            pass

    def play_all(self):
        """Play the entire video from the start, with the music, so the crossfades can be heard in place."""
        if not self.project.scenes:
            return
        self.mode.set("all")
        self.sound.set(True)
        self._picking = False
        self._leave_edit()
        self.t_var.set(0.0)
        self._play_from(0.0)

    # -- the timeline strip
    def _lane_rows(self):
        n = len(self.project.tracks)
        return max(12, min(24, (int(self.lane_cv["height"]) - 20) // max(1, n)))

    def _draw_lanes(self):
        cv = getattr(self, "lane_cv", None)
        if cv is None or not self.winfo_exists():
            return
        cv.delete("all")
        W, H, pad, total = max(cv.winfo_width(), 200), int(cv["height"]), 8, self._total()
        self._lane_geo = None
        if total <= 0:
            cv.create_text(W / 2, H / 2, text="Add scenes first", fill=theme.MUTED)
            return
        self._lane_geo = (pad, W, total)
        X = lambda t: pad + max(0.0, min(t, total)) / total * (W - 2 * pad)
        for sg in self.project.segments()[0]:  # scene boundaries
            x = X(sg.start)
            cv.create_line(x, 14, x, H, fill=theme.BORDER)
            cv.create_text(x + 2, 7, text="end" if sg.outro else str(sg.idx + 1), anchor="w", fill=theme.MUTED, font=(theme.FAMILY, 7))
        rh = self._lane_rows()
        for k, t in enumerate(self.project.tracks):
            t = {**TRACK_DEFAULTS, **t}
            s, e = float(t["start"]), track_end(t, total)
            if e <= s or s >= total:
                continue
            y0, y1 = 18 + k * rh, 18 + k * rh + rh - 3
            fi, fo = min(float(t["fade_in"]), e - s), min(float(t["fade_out"]), e - s)
            col, sel = self.PALETTE[k % len(self.PALETTE)], k == self._tr_i
            cv.create_polygon(X(s), y1, X(s + fi), y0, X(e - fo), y0, X(e), y1, fill=col,
                              outline="#FFFFFF" if sel else col, width=2 if sel else 1)
            cv.create_text(X(s) + 5, (y0 + y1) / 2, text=("↻ " if t["loop"] else "") + Path(t["path"]).stem, anchor="w",
                           fill="#FFFFFF", font=(theme.FAMILY, 8, "bold"))
        if not self.project.tracks:
            cv.create_text(W / 2, H / 2, text="No tracks yet.  Add tracks…", fill=theme.MUTED)
        cv.create_line(X(self.t_var.get()), 0, X(self.t_var.get()), H, fill="#FF5A5A", width=2, tags="playhead")

    def _move_playhead(self):
        if self._lane_geo is None:
            return
        pad, W, total = self._lane_geo
        x = pad + max(0.0, min(float(self.t_var.get()), total)) / total * (W - 2 * pad)
        self.lane_cv.coords("playhead", x, 0, x, int(self.lane_cv["height"]))

    def _lane_press(self, ev):
        tracks = self.project.tracks
        if not self._lane_geo or not tracks:
            return
        pad, W, total = self._lane_geo
        k = int((ev.y - 18) // self._lane_rows())
        if not 0 <= k < len(tracks):
            return
        t = {**TRACK_DEFAULTS, **tracks[k]}
        X = lambda tt: pad + max(0.0, min(tt, total)) / total * (W - 2 * pad)
        s, e = float(t["start"]), track_end(t, total)
        mode = "right" if abs(ev.x - X(e)) <= 7 else "left" if abs(ev.x - X(s)) <= 7 else "move"
        self._ldrag = dict(k=k, mode=mode, x=ev.x, s=s, e=e, end_val=float(t["end"]))
        self.tr_tree.selection_set(str(k))
        self._tr_i = k

    def _lane_move(self, ev):
        d = self._ldrag
        if not d or not self._lane_geo:
            return
        pad, W, total = self._lane_geo
        px = (W - 2 * pad) / total  # pixels per second
        pts = [0.0, total] + [sg.start for sg in self.project.segments()[0]]

        def snap(v):
            for p in pts:
                if abs(v - p) * px < 6:
                    return p
            return round(v, 1)

        t, dt = self.project.tracks[d["k"]], (ev.x - d["x"]) / px
        if d["mode"] == "move":
            ns = max(0.0, min(snap(d["s"] + dt), total - 0.5))
            t["start"] = round(ns, 2)
            if d["end_val"] > 0:
                t["end"] = round(d["end_val"] + (ns - d["s"]), 2)
        elif d["mode"] == "left":
            t["start"] = round(max(0.0, min(snap(d["s"] + dt), d["e"] - 0.2)), 2)
        else:
            ne = max(d["s"] + 0.2, min(snap(d["e"] + dt), total))
            t["end"] = 0.0 if ne >= total - 0.01 else round(ne, 2)
        self._set_tv(t_start=t["start"], t_end=t["end"])
        self.tr_tree.item(str(d["k"]), values=self._tr_row(d["k"], t))
        self._draw_lanes()

    def _lane_release(self, _ev):
        if self._ldrag:
            self._ldrag = None
            self._changed()

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
        ca = self._renderer().clip_audio() if P.scenes else []
        if not P.scenes or not has_audio(P, ca):
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
        total, iv, wt, tt = self._total(), fr.typing_intervals(), fr.whoosh_times(), fr.tap_times()
        clip_key = [[c["path"], round(c["ss"], 2), round(c["span"], 2), c["speed"], c["vol"], round(c["start"], 2),
                     round(c["dur"], 2)] for c in ca]
        key = json.dumps([P.music, P.tracks, P.music_volume, P.music_fade_in, P.music_fade_out, P.typing_sound, P.typing_volume,
                          P.whoosh_sound, P.whoosh_volume, P.duck, round(total, 2),
                          [(round(a, 2), round(b, 2)) for a, b in iv], [round(x, 2) for x in wt], clip_key,
                          P.tap_sound, P.tap_volume, [round(x, 2) for x in tt]])
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
                self._q.put(("pcm", gen, render_pcm(ffmpeg, proj, total, iv, wt, ca, tt)))
            except Exception as exc:
                self._q.put(("pcm_err", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def refresh_design(self):
        self.design = self.app.collect()
        self._changed()
        self.status.set("Look refreshed from the main window.")

    # ------------------------------------------------------------------ preview
    def _img(self, name):
        if is_video(name) or is_blank(name):  # videos are decoded frame by frame by the renderer
            return None
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
                                     fx=self.fx, raw_dir=self.paths.raw, live=True)
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
        self._draw_edit_overlay()
        self.time_lbl.config(text=f"{t - lo:4.1f} / {hi - lo:.1f} s")
        self._move_playhead()
        if self._fr is not None and self._fr.errors:  # never leave a blank phone unexplained
            self.status.set("Video problem: " + self._fr.errors[-1])

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
        self._leave_edit()
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
        self._leave_edit()
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
        self.status.set("Preparing the video clips…" if any(is_video(s.image) for s in project.scenes) else "Starting the export…")

        def work():
            try:
                def prog(i, n, frame):
                    small = frame.reduce(max(1, frame.width // PREVIEW_W)) if i % 3 == 0 or i == n else None
                    self._q.put(("p", i, n, small))

                ok = render_video(design, project, images, out, project.width, assets_dir=self.paths.video_assets,
                                  progress=prog, cancel=self._cancel.is_set, fx=fx, raw_dir=self.paths.raw)
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
        C.clear_cache()
        self._playing = False
        self.player.stop()
        self._cancel.set()
        self._save()
        self.destroy()