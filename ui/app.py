"""Main window: file browser, live preview, controls, export."""
import contextlib
import json
import os
import re
import subprocess
import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

import config
from core import fonts, frames, renderer
from core.settings import Settings
from ui import theme
from ui.widgets import Controls, ScrollFrame

IMG_EXT = config.MEDIA_EXT    # screenshots and videos both show up in the list
# "Apply style to all" copies everything except per-screenshot content/cropping.
STYLE_EXCLUDE = {"title", "subtitle", "shot_zoom", "shot_pan_x", "shot_pan_y", "shot_fit"}


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def is_video(path):
    return Path(path).suffix.lower() in config.VIDEO_EXT


def load_image(path):
    """A screenshot, or for a video a representative frame (so it can be styled and exported like a screenshot)."""
    if is_video(path):
        from video import clips
        from video.engine import find_ffmpeg
        return clips.poster(find_ffmpeg(), path)
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


class App(tk.Tk):
    def __init__(self, project_root, raw=None):
        super().__init__()
        self.paths = config.project_paths(project_root, raw or config.last_folder())
        for p in (self.paths.raw, self.paths.output, self.paths.presets, self.paths.frames, self.paths.fonts, self.paths.backgrounds):
            p.mkdir(parents=True, exist_ok=True)
        fonts.set_custom_dir(self.paths.fonts)
        frames.set_custom_dir(self.paths.frames)
        renderer.set_backgrounds_dir(self.paths.backgrounds)

        self.title(f"Shot Studio · {self.paths.raw.name}")
        self.geometry("1540x940")
        self.minsize(1220, 740)
        theme.apply(self)

        self.vars = {k: self._make_var(v) for k, v in config.DEFAULTS.items()}
        self.finish_var = tk.StringVar(value="Obsidian")
        self.theme_var = tk.StringVar(value="Midnight Indigo")
        self.ctl = Controls(self, self.vars)

        self.shot = None
        self.current = None
        self._files = {}          # tree iid -> Path
        self._thumbs = []
        self._vthumbs = {}        # (path, mtime) -> PIL thumbnail of a video's poster frame
        self.folder_var = tk.StringVar(value=str(self.paths.raw))
        self._photo = None
        self._geom = (0, 0, 1, 1)
        self._img_id = None
        self._scene = None
        self._last_scene = None
        self._video = None
        self._d0 = None
        self._mode = "phone"
        self._suspend = False
        self._dirty = False
        self._render_job = self._save_job = None
        self.status_var = tk.StringVar(value="Choose the folder with your raw screenshots and videos on the left.")

        self._build_ui()
        self.refresh_files(select_first=True)
        for v in self.vars.values():
            v.trace_add("write", self._on_change)

        self.bind_all("<MouseWheel>", self._wheel)
        self.bind_all("<Button-4>", self._wheel)
        self.bind_all("<Button-5>", self._wheel)
        self.bind("<Control-e>", lambda e: self.export_current())
        self.preview.bind("<Configure>", lambda e: self._queue_render())
        self.preview.bind("<ButtonPress-1>", self._drag_start)
        self.preview.bind("<B1-Motion>", self._drag)
        self.preview.bind("<Motion>", self._hover)
        self.preview.bind("<ButtonRelease-1>", self._drag_end)
        self.protocol("WM_DELETE_WINDOW", self._close)

    @staticmethod
    def _make_var(v):
        if isinstance(v, bool):
            return tk.BooleanVar(value=v)
        if isinstance(v, (int, float)):
            return tk.DoubleVar(value=v)
        return tk.StringVar(value=v)

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        bar = ttk.Frame(self, style="Bar.TFrame")
        bar.pack(fill="x")
        ttk.Label(bar, text="Shot Studio", style="Brand.TLabel").pack(side="left", padx=(18, 10), pady=12)
        ttk.Label(bar, text="Google Play screenshot composer", style="BarMuted.TLabel").pack(side="left")
        ttk.Button(bar, text="Export PNG", style="Accent.TButton", command=self.export_current).pack(side="right", padx=(6, 16), pady=9)
        ttk.Button(bar, text="Folder…", command=self.choose_folder).pack(side="right", padx=3)
        ttk.Button(bar, text="Promo video", command=self.open_video).pack(side="right", padx=3)
        ttk.Button(bar, text="Export all", command=self.export_all).pack(side="right", padx=3)
        ttk.Button(bar, text="Apply style to all", command=self.apply_style_to_all).pack(side="right", padx=3)
        ttk.Button(bar, text="Reset", command=self.reset).pack(side="right", padx=3)
        ttk.Button(bar, text="Load template", command=self.load_template).pack(side="right", padx=3)
        ttk.Button(bar, text="Save template", command=self.save_template).pack(side="right", padx=3)

        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(side="bottom", fill="x")
        body = ttk.Frame(self, style="Bg.TFrame")
        body.pack(fill="both", expand=True)

        # screenshot list
        left = ttk.Frame(body, width=290)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ttk.Label(left, text="Raw folder", style="Section.TLabel").pack(anchor="w", padx=16, pady=(16, 4))
        self.recent_cb = ttk.Combobox(left, textvariable=self.folder_var, values=self._folder_choices(), state="readonly")
        self.recent_cb.pack(fill="x", padx=14)
        self.recent_cb.bind("<<ComboboxSelected>>", lambda e: self.set_folder(self.folder_var.get()))
        ttk.Button(left, text="Choose folder…", style="Accent.TButton", command=self.choose_folder).pack(fill="x", padx=14, pady=(6, 0))
        head = ttk.Frame(left)
        head.pack(fill="x", padx=16, pady=(14, 8))
        ttk.Label(head, text="Screenshots and videos", style="Section.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(head, style="Muted.TLabel")
        self.count_lbl.pack(side="right")
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True, padx=(10, 0))
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        row = ttk.Frame(left)
        row.pack(fill="x", padx=12, pady=12)
        for text, cmd in (("Browse…", self.browse), ("Refresh", self.refresh_files), ("Open folder", self.open_folder)):
            ttk.Button(row, text=text, command=cmd).pack(side="left", expand=True, fill="x", padx=2)

        # controls
        right = ttk.Frame(body, width=380)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)
        for name, builder in (("Text", self._tab_text), ("Phone", self._tab_phone), ("Style", self._tab_style)):
            sf = ScrollFrame(nb)
            nb.add(sf, text=name)
            pad = ttk.Frame(sf.inner)
            pad.pack(fill="both", expand=True, padx=18, pady=(0, 24))
            builder(pad)

        # preview
        center = ttk.Frame(body, style="Bg.TFrame")
        center.pack(side="left", fill="both", expand=True)
        self.info = ttk.Label(center, style="Info.TLabel")
        self.info.pack(pady=(12, 0))
        self.preview = tk.Canvas(center, bg=theme.BG, highlightthickness=0, cursor="fleur")
        self.preview.pack(fill="both", expand=True)
        ttk.Label(center, text="Drag the text or the phone to move them  ·  scroll to resize the phone", style="Info.TLabel").pack(pady=(0, 10))

    def _tab_text(self, p):
        c = self.ctl
        c.section(p, "Title")
        c.textbox(p, "Text (leave empty to hide)", "title", 3)
        c.font_picker(p, "Font", "title_font", "Bold")
        c.slider(p, "Size", "title_size", 2, 16, 0.1)
        c.color(p, "Color", "title_color")
        c.section(p, "Subtitle")
        c.textbox(p, "Text (leave empty to hide)", "subtitle", 3)
        c.font_picker(p, "Font", "subtitle_font", "Regular")
        c.slider(p, "Size", "subtitle_size", 1, 9, 0.1)
        c.slider(p, "Opacity", "subtitle_opacity", 10, 100, 1, "%")
        c.color(p, "Color", "subtitle_color")
        c.section(p, "Position")
        c.segmented(p, "Alignment", "text_align", ["left", "center", "right"])
        c.slider(p, "Vertical position", "text_y", 0, 70, 0.5, "%")
        c.slider(p, "Horizontal offset", "text_x", -40, 40, 0.5, "%")
        c.slider(p, "Side margin", "text_margin", 0, 30, 0.5, "%")
        c.slider(p, "Title line spacing", "line_spacing", 0.8, 1.6, 0.01)
        c.slider(p, "Gap between title and subtitle", "text_gap", 0, 8, 0.1, "%")

    def _tab_phone(self, p):
        c = self.ctl
        c.section(p, "Device")
        c.combo(p, "Frame", "frame", frames.list_frames())
        c.combo(p, "Finish", None, config.FRAME_FINISHES, var=self.finish_var, on_pick=self._pick_finish)
        c.color(p, "Frame color", "frame_color")
        c.slider(p, "Glass reflection", "glass_sheen", 0, 100, 1, "%")
        c.slider(p, "Shadow", "phone_shadow", 0, 100, 1, "%")
        c.section(p, "Size and position")
        c.slider(p, "Size", "phone_scale", 30, 130, 1, "%")
        c.slider(p, "Horizontal position", "phone_x", -60, 60, 0.5, "%")
        c.slider(p, "Rotation", "phone_rotation", -30, 30, 0.5, "°")
        c.check(p, "Place the phone below the text automatically", "auto_phone_y")
        c.slider(p, "Gap below text (auto)", "phone_gap", -15, 40, 0.5, "%")
        c.slider(p, "Top position (manual)", "phone_y", -40, 100, 0.5, "%")
        c.section(p, "Screenshot inside the phone")
        c.combo(p, "Fit", "shot_fit", ["Cover", "Contain", "Stretch"])
        c.slider(p, "Zoom", "shot_zoom", 50, 300, 1, "%")
        c.slider(p, "Pan horizontally", "shot_pan_x", -100, 100, 1)
        c.slider(p, "Pan vertically", "shot_pan_y", -100, 100, 1)

    def _tab_style(self, p):
        c = self.ctl
        c.section(p, "Canvas")
        c.combo(p, "Size", "canvas", config.CANVAS_PRESETS)
        c.section(p, "Theme")
        c.combo(p, "Quick theme", None, config.THEMES, var=self.theme_var, on_pick=self._pick_theme)
        c.section(p, "Background")
        c.combo(p, "Type", "bg_mode", ["Solid", "Linear gradient", "Radial gradient", "Image"])
        c.color(p, "Color 1", "bg_color1")
        c.color(p, "Color 2", "bg_color2")
        c.slider(p, "Gradient angle", "bg_angle", 0, 360, 1, "°")
        c.section(p, "Custom background image")
        self.bg_cb = c.combo(p, "Image from assets/backgrounds", "bg_image", self._bg_choices(),
                             on_pick=lambda v: self.vars["bg_mode"].set("Image" if v != "None" else "Linear gradient"))
        row = ttk.Frame(p)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Refresh list", command=self.refresh_backgrounds).pack(side="left", expand=True, fill="x", padx=(0, 3))
        ttk.Button(row, text="Open folder", command=lambda: self.open_folder(self.paths.backgrounds)).pack(side="left", expand=True, fill="x")
        c.slider(p, "Zoom", "bg_img_zoom", 100, 300, 1, "%")
        c.slider(p, "Pan horizontally", "bg_img_pan_x", -100, 100, 1)
        c.slider(p, "Pan vertically", "bg_img_pan_y", -100, 100, 1)
        c.slider(p, "Darken (helps text stand out)", "bg_img_dim", 0, 80, 1, "%")
        c.section(p, "Glow behind the phone")
        c.color(p, "Glow color", "glow_color")
        c.slider(p, "Glow strength", "glow_strength", 0, 100, 1, "%")

    # ------------------------------------------------------------ state helpers
    @contextlib.contextmanager
    def _quiet(self):
        self._suspend = True
        try:
            yield
        finally:
            self._suspend = False

    def collect(self):
        d = {}
        for k, v in self.vars.items():
            try:
                d[k] = v.get()
            except tk.TclError:
                d[k] = config.DEFAULTS[k]
        return Settings(**d)

    def apply_settings(self, d):
        with self._quiet():
            for k, v in d.items():
                if k in self.vars:
                    try:
                        self.vars[k].set(v)
                    except tk.TclError:
                        pass
            self.ctl.sync_texts()
        self._queue_render()

    def status(self, msg):
        self.status_var.set(msg)
        self.update_idletasks()

    @staticmethod
    def _bg_choices():
        return ["None"] + renderer.list_backgrounds()

    def refresh_backgrounds(self):
        self.bg_cb.config(values=self._bg_choices())
        self.status(f"{len(renderer.list_backgrounds())} background images found.")

    def _pick_finish(self, name):
        self.vars["frame_color"].set(config.FRAME_FINISHES[name])

    def _pick_theme(self, name):
        with self._quiet():
            for k, v in config.THEMES[name].items():
                self.vars[k].set(v)
        self._on_change()

    def reset(self):
        if messagebox.askyesno("Reset", "Reset all settings to the defaults?"):
            self.apply_settings(config.DEFAULTS)
            self._dirty = True
            self._queue_autosave()

    # --------------------------------------------------------------- rendering
    def _on_change(self, *_):
        if self._suspend:
            return
        self._dirty = True
        self._queue_render()
        self._queue_autosave()

    def _queue_render(self):
        if self._render_job is None:  # throttle: one render in flight, always uses the latest values
            self._render_job = self.after(10, self.render_preview)

    def render_preview(self):
        self._render_job = None
        cw_widget, ch_widget = self.preview.winfo_width(), self.preview.winfo_height()
        if cw_widget < 50 or ch_widget < 50:
            return
        try:
            s = self.collect()
            cw, ch = renderer.canvas_size(s)
            k = min((cw_widget - 56) / cw, (ch_widget - 40) / ch)
            scene = renderer.make_scene(s, self.shot, width=max(40, int(cw * k)))
            img = scene.compose()
        except Exception:
            traceback.print_exc()
            self.status("Render error, see the terminal for details.")
            return
        pw, ph = img.size
        x0, y0 = (cw_widget - pw) // 2, (ch_widget - ph) // 2
        if self._img_id and self._geom == (x0, y0, pw, ph):
            self._photo.paste(img)  # same size: update pixels in place, no flicker
        else:
            self._photo = ImageTk.PhotoImage(img)
            self._geom = (x0, y0, pw, ph)
            self.preview.delete("all")
            self.preview.create_rectangle(x0 - 1, y0 - 1, x0 + pw, y0 + ph, outline=theme.BORDER)
            self._img_id = self.preview.create_image(x0, y0, anchor="nw", image=self._photo)
        self._last_scene = scene
        self.preview.delete("hl")
        name = self.current.name if self.current else "no screenshot selected"
        self.info.config(text=f"{cw}×{ch} px  ·  {s.frame}  ·  {name}")

    def _hit_text(self, e):
        sc = self._last_scene
        if not sc or not sc.text_bbox:
            return False
        x, y = e.x - self._geom[0], e.y - self._geom[1]
        l, t, r, b = sc.text_bbox
        return l <= x <= r and t <= y <= b

    def _draw_hl(self, ox=0, oy=0):
        """Dashed outline around the text block."""
        l, t, r, b = self._last_scene.text_bbox
        x0, y0 = self._geom[:2]
        box = (x0 + l + ox, y0 + t + oy, x0 + r + ox, y0 + b + oy)
        if self.preview.find_withtag("hl"):
            self.preview.coords("hl", *box)
        else:
            self.preview.create_rectangle(*box, outline=theme.ACCENT_SOFT, dash=(5, 3), width=1, tags="hl")

    def _hover(self, e):
        if self._d0 or not self._last_scene:
            return
        hit = self._hit_text(e)
        self.preview.config(cursor="hand2" if hit else "fleur")
        if hit:
            self._draw_hl()
        else:
            self.preview.delete("hl")

    def _drag_start(self, e):
        if self._render_job is not None:  # make sure the cached scene matches the current settings
            self.after_cancel(self._render_job)
            self.render_preview()
        if not self._img_id or not self._last_scene:
            return
        self._d0 = (e.x, e.y)
        self._scene = self._last_scene
        self._mode = "text" if self._hit_text(e) else "phone"
        self._auto = self.vars["auto_phone_y"].get()
        if self._mode == "text":
            self._start = (self.vars["text_x"].get(), self.vars["text_y"].get(), self.vars["phone_gap"].get())
        else:
            self._ykey = "phone_gap" if self._auto else "phone_y"
            self._start = (self.vars["phone_x"].get(), self.vars[self._ykey].get())

    def _drag(self, e):
        if not self._d0 or self._scene is None:
            return
        dx, dy = e.x - self._d0[0], e.y - self._d0[1]
        W, H = self._geom[2:]
        clamp = lambda v, lo, hi: max(lo, min(hi, v))
        if self._mode == "text":
            x0, y0, gap0 = self._start
            nx, ny = clamp(x0 + dx / W * 100, -40, 40), clamp(y0 + dy / H * 100, 0, 70)
            ex, ey = round((nx - x0) / 100 * W), round((ny - y0) / 100 * H)
            self._photo.paste(self._scene.compose(0, 0, ex, ey))  # text moves, the phone never does
            self._draw_hl(ex, ey)
            with self._quiet():
                self.vars["text_x"].set(round(nx, 1))
                self.vars["text_y"].set(round(ny, 1))
                if self._auto:  # auto layout would drag the phone along, so cancel that out
                    self.vars["phone_gap"].set(round(clamp(gap0 - (ny - y0), -15, 40), 1))
        else:
            self._photo.paste(self._scene.compose(dx, dy))
            lo, hi = (-15, 40) if self._auto else (-40, 100)
            with self._quiet():  # keep the sliders in sync without triggering renders
                self.vars["phone_x"].set(round(clamp(self._start[0] + dx / W * 100, -60, 60), 1))
                self.vars[self._ykey].set(round(clamp(self._start[1] + dy / H * 100, lo, hi), 1))

    def _drag_end(self, _):
        if self._scene is not None:
            self._scene = None
            self._on_change()  # one full-quality render + autosave
        self._d0 = None

    def _wheel(self, e):
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except KeyError:
            return
        direction = -1 if (getattr(e, "num", 0) == 4 or e.delta > 0) else 1
        if w is self.preview:
            v = self.vars["phone_scale"]
            v.set(max(30, min(130, round(v.get() - direction * 2))))
            return
        while w is not None and not isinstance(w, ScrollFrame):
            w = w.master
        if w:
            w.canvas.yview_scroll(direction, "units")

    # ------------------------------------------------------------------ folders
    @staticmethod
    def _folder_choices():
        return config.recent_folders()

    def choose_folder(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.paths.raw, mustexist=True,
                                    title="Choose the folder with your raw screenshots and videos")
        if d:
            self.set_folder(d)

    def set_folder(self, folder):
        """Switch to another folder of raws. Nothing is deleted: every folder keeps its own settings and exports."""
        folder = Path(folder).resolve()
        if not folder.is_dir():
            messagebox.showerror("Folder not found", str(folder))
            return
        self.flush_autosave()
        new = config.project_paths(self.paths.root, folder)
        for p in (new.raw, new.output, new.presets):
            p.mkdir(parents=True, exist_ok=True)
        self.paths.__dict__.update(vars(new))       # the video studio shares this object, so it follows automatically
        config.remember_folder(folder)
        self.recent_cb.config(values=self._folder_choices())
        self.folder_var.set(str(folder))
        self.title(f"Shot Studio · {folder.name}")
        self.shot = self.current = None
        self._dirty = False
        self.refresh_files(select_first=True)
        if self._video is not None and self._video.winfo_exists():
            self._video.reload_workspace()
        if not self._files:
            self._queue_render()
        self.status(f"Folder: {folder}  ·  {len(self._files)} files")

    # ------------------------------------------------------------------- files
    def refresh_files(self, select_first=False):
        self.tree.delete(*self.tree.get_children())
        self._files.clear()
        self._thumbs.clear()
        files = sorted((p for p in self.paths.raw.iterdir() if p.suffix.lower() in IMG_EXT), key=natural_key)
        for i, p in enumerate(files):
            kw, vid = {}, is_video(p)
            try:
                if vid:
                    key = (str(p), p.stat().st_mtime_ns)
                    if key not in self._vthumbs:
                        from video import clips
                        from video.engine import find_ffmpeg
                        self._vthumbs[key] = clips.poster(find_ffmpeg(), p, max_w=160)
                    im = self._vthumbs[key].copy()
                else:
                    im = Image.open(p)
                    im.draft("RGB", (200, 400))
                    im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((44, 80), Image.Resampling.LANCZOS)
                self._thumbs.append(ImageTk.PhotoImage(im))
                kw["image"] = self._thumbs[-1]
            except Exception:
                pass
            iid = f"f{i}"
            self._files[iid] = p
            self.tree.insert("", "end", iid=iid, text=("  ▶ " if vid else "   ") + p.name, **kw)
        self.count_lbl.config(text=f"{len(files)} files")
        if self.current:
            for iid, p in self._files.items():
                if p == self.current:
                    self.tree.selection_set(iid)
        elif select_first and files:
            self.tree.selection_set("f0")
        elif not files:
            self.status(f"Nothing here yet. Add screenshots or videos to {self.paths.raw}, or choose another folder.")

    def _on_select(self, _):
        sel = self.tree.selection()
        if sel and self._files.get(sel[0]) != self.current:
            self.load_shot(self._files[sel[0]])

    def browse(self):
        exts = " ".join("*" + e for e in sorted(config.MEDIA_EXT))
        p = filedialog.askopenfilename(initialdir=self.paths.raw, title="Choose a screenshot or video",
                                       filetypes=[("Screenshots and videos", exts)])
        if not p:
            return
        p = Path(p)
        for iid, f in self._files.items():
            if f.resolve() == p.resolve():
                self.tree.selection_set(iid)
                self.tree.see(iid)
                return
        self.load_shot(p)

    def open_folder(self, folder=None):
        target = str(folder or self.paths.raw)
        if sys.platform.startswith("win"):
            os.startfile(target)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])

    def load_shot(self, path):
        path = Path(path)
        self.flush_autosave()
        try:
            img = load_image(path)
        except Exception as exc:
            messagebox.showerror("Cannot open image", f"{path.name}\n\n{exc}")
            return
        self.shot, self.current = img, path
        preset = self.paths.presets / f"{path.stem}.json"
        if preset.exists():
            try:
                self.apply_settings(json.loads(preset.read_text("utf-8")))
            except (OSError, ValueError):
                pass
        self._dirty = False
        self._queue_render()
        self.status(f"Loaded {path.name} ({img.width}×{img.height})" + (" · video: showing a poster frame; build the video in Promo video." if is_video(path) else "."))

    # ---------------------------------------------------- per-image auto-save
    def _queue_autosave(self):
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(800, self.flush_autosave)

    def flush_autosave(self):
        if self._save_job:
            self.after_cancel(self._save_job)
            self._save_job = None
        if self._dirty and self.current:
            (self.paths.presets / f"{self.current.stem}.json").write_text(
                json.dumps(self.collect().to_dict(), indent=2, ensure_ascii=False), "utf-8")
            self._dirty = False

    def _close(self):
        self.flush_autosave()
        self.destroy()

    # ------------------------------------------------------------------ export
    def _export(self, settings, path, img=None):
        out = renderer.render(settings, img if img is not None else load_image(path))
        dest = self.paths.output / f"{path.stem}_playstore.png"
        out.save(dest, optimize=True)
        return dest

    def open_video(self):
        self.flush_autosave()
        if self._video is not None and self._video.winfo_exists():
            self._video.lift()
            return
        from ui.video_dialog import VideoDialog
        self._video = VideoDialog(self)

    def export_current(self):
        if self.shot is None:
            messagebox.showinfo("Nothing to export", "Select a screenshot first.")
            return
        self.status("Exporting…")
        dest = self._export(self.collect(), self.current, self.shot)
        self.flush_autosave()
        self.status(f"Exported {dest}")

    def export_all(self):
        files = list(self._files.values())
        if not files:
            return
        self.flush_autosave()
        current = self.collect()
        for i, p in enumerate(files, 1):
            self.status(f"Exporting {i}/{len(files)}: {p.name}")
            preset = self.paths.presets / f"{p.stem}.json"
            try:
                s = Settings(**json.loads(preset.read_text("utf-8"))) if preset.exists() else current
                self._export(s, p)
            except Exception as exc:
                traceback.print_exc()
                self.status(f"Failed on {p.name}: {exc}")
                return
        self.status(f"Exported {len(files)} images to {self.paths.output}")

    def apply_style_to_all(self):
        files = [p for p in self._files.values() if p != self.current]
        if not files or not messagebox.askyesno(
                "Apply style to all",
                "Copy this look (frame, background, text style and layout) to every other screenshot?\n"
                "Each screenshot keeps its own title, subtitle and crop."):
            return
        style = {k: v for k, v in self.collect().to_dict().items() if k not in STYLE_EXCLUDE}
        for p in files:
            f = self.paths.presets / f"{p.stem}.json"
            try:
                data = json.loads(f.read_text("utf-8")) if f.exists() else self.collect().to_dict()
            except (OSError, ValueError):
                data = self.collect().to_dict()
            data.update(style)
            f.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        self.flush_autosave()
        self.status(f"Style applied to {len(files)} screenshots.")

    def save_template(self):
        p = filedialog.asksaveasfilename(initialdir=self.paths.presets, defaultextension=".json",
                                         initialfile="template.json", filetypes=[("Template", "*.json")])
        if p:
            Path(p).write_text(json.dumps(self.collect().to_dict(), indent=2, ensure_ascii=False), "utf-8")
            self.status(f"Template saved to {p}")

    def load_template(self):
        p = filedialog.askopenfilename(initialdir=self.paths.presets, filetypes=[("Template", "*.json")])
        if p:
            try:
                self.apply_settings(json.loads(Path(p).read_text("utf-8")))
                self._dirty = True
                self._queue_autosave()
            except (OSError, ValueError) as exc:
                messagebox.showerror("Cannot load template", str(exc))