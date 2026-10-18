"""Searchable animation browser with live previews. Type to search names, categories, descriptions and tags."""
import math
import random
import time
import tkinter as tk
from tkinter import ttk

from ui import theme
from video import animations as A
from video import premium
from video import transitions as T

CW, CH = 260, 320
PW, PH = 84, 168
PREVIEW_BG = "#161922"


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _mix(fg, bg, a):
    a = max(0.0, min(1.0, a))
    f, b = _rgb(fg), _rgb(bg)
    return "#%02x%02x%02x" % tuple(int(b[i] + (f[i] - b[i]) * a) for i in range(3))


class AnimationPicker(tk.Toplevel):
    """kind = "motion" or "transition". on_apply(spec, all_scenes) is called when the user confirms."""

    def __init__(self, parent, kind, current, on_apply):
        super().__init__(parent)
        self.kind, self.on_apply = kind, on_apply
        self.title("Pick a motion" if kind == "motion" else "Pick a transition")
        self.geometry("900x580")
        self.minsize(780, 480)
        self.configure(bg=theme.BG)
        self.transient(parent)
        self.entries = premium.entries(kind)
        self.stack = [p for p in str(current).split("+") if p and p != "none"] if kind == "motion" else []
        self.q, self.cat = tk.StringVar(), tk.StringVar(value="All")
        self.all_var = tk.BooleanVar(value=False)
        self.count, self.combo_txt = tk.StringVar(), tk.StringVar()
        self.name_txt, self.desc_txt = tk.StringVar(), tk.StringVar()
        self._cur, self._job, self._t0 = None, None, time.time()

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=14, pady=12)
        left = ttk.Frame(outer)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(outer, width=290)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        bar = ttk.Frame(left)
        bar.pack(fill="x")
        ttk.Label(bar, text="Search").pack(side="left")
        self.entry = ttk.Entry(bar, textvariable=self.q)
        self.entry.pack(side="left", fill="x", expand=True, padx=8)
        cats = ["All"] + sorted({e["category"] for e in self.entries})
        ttk.Combobox(bar, textvariable=self.cat, values=cats, state="readonly", width=12).pack(side="left")
        ttk.Label(left, textvariable=self.count).pack(anchor="w", pady=(6, 2))

        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("name", "cat", "desc"), show="headings", selectmode="browse", style="Scenes.Treeview")
        for col, text, w in (("name", "Name", 170), ("cat", "Category", 80), ("desc", "What it does", 260)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.cv = tk.Canvas(right, width=CW, height=CH, bg=PREVIEW_BG, highlightthickness=0)
        self.cv.pack(pady=(0, 8))
        ttk.Label(right, textvariable=self.name_txt, font=("TkDefaultFont", 11, "bold"), wraplength=280).pack(anchor="w")
        ttk.Label(right, textvariable=self.desc_txt, wraplength=280).pack(anchor="w", pady=(2, 8))
        ttk.Button(right, text="Use this", style="Accent.TButton", command=self._use_selected).pack(fill="x")
        if kind == "motion":
            ttk.Button(right, text="Add to combo (stack motions)", command=self._add_combo).pack(fill="x", pady=(6, 0))
            ttk.Label(right, textvariable=self.combo_txt, wraplength=280).pack(anchor="w", pady=(4, 0))
            row = ttk.Frame(right)
            row.pack(fill="x", pady=(2, 0))
            ttk.Button(row, text="Use combo", command=self._use_combo).pack(side="left", expand=True, fill="x", padx=(0, 3))
            ttk.Button(row, text="Clear", command=self._clear_combo).pack(side="left", expand=True, fill="x")
        ttk.Button(right, text="Surprise me", command=self._surprise).pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(right, text="Apply to all scenes", variable=self.all_var).pack(anchor="w", pady=(8, 0))
        self._update_combo_label()

        self.q.trace_add("write", self._refilter)
        self.cat.trace_add("write", self._refilter)
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)
        self.tree.bind("<Double-1>", lambda _e: self._use_selected())
        self.bind("<Return>", lambda _e: self._use_selected())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.entry.bind("<Down>", lambda _e: (self.tree.focus_set(), "break")[1])
        self._refilter()
        self.entry.focus_set()
        self._tick()

    # ---------------------------------------------------------- list / search
    def _refilter(self, *_):
        toks = self.q.get().lower().replace("_", " ").split()
        cat = self.cat.get()
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.entries):
            if cat != "All" and e["category"] != cat:
                continue
            hay = f'{e["name"]} {e["category"]} {e["desc"]} {e["tags"]}'.lower().replace("_", " ")
            if all(t in hay for t in toks):
                self.tree.insert("", "end", iid=str(i), values=(e["name"], e["category"], e["desc"]))
        kids = self.tree.get_children()
        self.count.set(f"{len(kids)} animations")
        if kids:
            self.tree.selection_set(kids[0])
        else:
            self._cur = None
            self.name_txt.set("")
            self.desc_txt.set("No match. Try another word.")

    def _on_pick(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        self._cur = self.entries[int(sel[0])]
        self.name_txt.set(self._cur["name"])
        self.desc_txt.set(self._cur["desc"])
        self._t0 = time.time()

    def _surprise(self):
        kids = self.tree.get_children()
        if kids:
            k = random.choice(kids)
            self.tree.selection_set(k)
            self.tree.see(k)

    # ----------------------------------------------------------------- apply
    def _apply(self, spec):
        self.on_apply(spec, self.all_var.get())
        self.destroy()

    def _use_selected(self):
        if self._cur:
            self._apply(self._cur["name"])

    def _add_combo(self):
        if self._cur:
            self.stack.extend(p for p in self._cur["name"].split("+") if p not in self.stack)
            self._update_combo_label()

    def _clear_combo(self):
        self.stack = []
        self._update_combo_label()

    def _use_combo(self):
        self._apply("+".join(self.stack) if self.stack else "none")

    def _update_combo_label(self):
        self.combo_txt.set("Combo: " + (" + ".join(self.stack) if self.stack else "(empty)"))

    # --------------------------------------------------------------- preview
    def _draw(self, pose, body, screen):
        if pose.alpha <= 0.02:
            return
        cx, cy = CW / 2 + pose.x / 100 * CW, CH / 2 + pose.y / 100 * CH
        th = math.radians(pose.rot)
        c, s = math.cos(th), math.sin(th)

        def poly(w, h, fill, outline=""):
            pts = []
            for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
                x, y = x * pose.scale, y * pose.scale
                pts += [cx + x * c + y * s, cy - x * s + y * c]
            self.cv.create_polygon(pts, fill=fill, outline=outline)

        poly(PW, PH, _mix(body, PREVIEW_BG, pose.alpha), _mix("#9aa3b8", PREVIEW_BG, pose.alpha))
        poly(PW - 10, PH - 14, _mix(screen, PREVIEW_BG, pose.alpha))

    def _tick(self):
        self._job = None
        if not self.winfo_exists():
            return
        self.cv.delete("all")
        e = self._cur
        if e:
            try:
                if self.kind == "motion":
                    t = (time.time() - self._t0) % 3.6
                    self._draw(A.motion_pose(e["name"], t / 3.6, t, 3.6), "#2a2f3a", "#4f7cff")
                else:
                    t = (time.time() - self._t0) % 3.0
                    tr = T.TRANSITIONS[e["name"]]
                    p = tr.ease(min(max((t - 0.5) / 1.3, 0.0), 1.0))
                    self._draw(tr.out(p), "#2a2f3a", "#4f7cff")
                    self._draw(tr.inn(p), "#2a2f3a", "#ff7a59")
            except Exception:
                pass
        self._job = self.after(33, self._tick)

    def destroy(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        super().destroy()