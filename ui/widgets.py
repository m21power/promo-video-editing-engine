"""Reusable control builders. Each control is bound to a tk variable by settings key."""
import math
import tkinter as tk
from tkinter import colorchooser, ttk

from PIL import ImageColor

from core import fonts
from ui import theme


class ScrollFrame(ttk.Frame):
    """Vertically scrollable container; put children in `.inner`."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=theme.PANEL, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")


class Controls:
    def __init__(self, root, variables):
        self.root, self.vars, self.texts = root, variables, {}

    def section(self, parent, title):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=(18, 4))
        ttk.Label(f, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Separator(f).pack(fill="x", pady=(5, 0))

    def slider(self, parent, label, key, lo, hi, step=1, unit=""):
        var = self.vars[key]
        decimals = max(0, -math.floor(math.log10(step)))
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=(5, 0))
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text=label).pack(side="left")
        value = ttk.Label(top, style="Value.TLabel")
        value.pack(side="right")

        def show(*_):
            try:
                value.config(text=f"{var.get():.{decimals}f}{unit}")
            except tk.TclError:
                pass

        def snap(v):
            x = round(round(float(v) / step) * step, 6)
            if abs(x - var.get()) > 1e-9:
                var.set(x)
            show()

        ttk.Scale(f, from_=lo, to=hi, variable=var, command=snap).pack(fill="x", pady=(2, 0))
        var.trace_add("write", show)
        show()

    def color(self, parent, label, key):
        var = self.vars[key]
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=5)
        ttk.Label(f, text=label).pack(side="left")
        sw = tk.Label(f, width=3, cursor="hand2", bd=0)
        sw.pack(side="right", padx=(8, 0))
        ttk.Entry(f, textvariable=var, width=9).pack(side="right")

        def paint(*_):
            try:
                ImageColor.getrgb(var.get())
                sw.config(bg=var.get())
            except ValueError:
                pass

        def pick(_=None):
            c = colorchooser.askcolor(color=var.get(), parent=self.root, title=label)
            if c and c[1]:
                var.set(c[1].upper())

        sw.bind("<Button-1>", pick)
        var.trace_add("write", paint)
        paint()

    def combo(self, parent, label, key, values, var=None, on_pick=None):
        var = var or self.vars[key]
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=5)
        ttk.Label(f, text=label).pack(anchor="w")
        cb = ttk.Combobox(f, textvariable=var, values=list(values), state="readonly")
        cb.pack(fill="x", pady=(3, 0))
        if on_pick:
            cb.bind("<<ComboboxSelected>>", lambda e: on_pick(var.get()))
        return cb

    def font_picker(self, parent, label, key, prefer="Regular"):
        """Two menus (family + weight) that write the chosen font file name to `key`."""
        var, fams = self.vars[key], fonts.families()
        fam_var, wt_var = tk.StringVar(value="Auto"), tk.StringVar()
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=5)
        ttk.Label(f, text=label).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(3, 0))
        fam_cb = ttk.Combobox(row, textvariable=fam_var, values=["Auto"] + list(fams), state="readonly")
        fam_cb.pack(side="left", fill="x", expand=True)
        wt_cb = ttk.Combobox(row, textvariable=wt_var, state="disabled", width=12)
        wt_cb.pack(side="left", padx=(6, 0))

        def show_weights(fam, chosen=None):
            ws = list(fams.get(fam, {}))
            wt_cb.config(values=ws, state="readonly" if ws else "disabled")
            wt_var.set(chosen if chosen in ws else (prefer if prefer in ws else (ws[0] if ws else "")))

        def on_family(_=None):
            fam = fam_var.get()
            show_weights(fam)
            var.set(fams[fam][wt_var.get()] if fam in fams else "Auto")

        def on_weight(_=None):
            var.set(fams[fam_var.get()][wt_var.get()])

        def sync(*_):
            for fam, ws in fams.items():
                for weight, name in ws.items():
                    if name == var.get():
                        fam_var.set(fam)
                        show_weights(fam, weight)
                        return
            fam_var.set("Auto")
            show_weights("Auto")

        fam_cb.bind("<<ComboboxSelected>>", on_family)
        wt_cb.bind("<<ComboboxSelected>>", on_weight)
        var.trace_add("write", sync)
        sync()

    def entry(self, parent, label, key):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=5)
        ttk.Label(f, text=label).pack(anchor="w")
        ttk.Entry(f, textvariable=self.vars[key]).pack(fill="x", pady=(3, 0))

    def segmented(self, parent, label, key, options):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=5)
        ttk.Label(f, text=label).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(3, 0))
        for opt in options:
            ttk.Radiobutton(row, text=opt.capitalize(), value=opt, variable=self.vars[key],
                            style="Toolbutton").pack(side="left", expand=True, fill="x", padx=(0, 2))

    def check(self, parent, label, key):
        ttk.Checkbutton(parent, text=label, variable=self.vars[key]).pack(anchor="w", pady=(8, 2))

    def textbox(self, parent, label, key, height=3):
        var = self.vars[key]
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=5)
        ttk.Label(f, text=label).pack(anchor="w")
        t = tk.Text(f, height=height, wrap="word", bg=theme.CARD, fg=theme.TEXT, insertbackground=theme.TEXT,
                    relief="flat", bd=0, padx=9, pady=7, font=theme.FONT, highlightthickness=1,
                    highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        t.pack(fill="x", pady=(3, 0))
        t.insert("1.0", var.get())
        t.bind("<KeyRelease>", lambda e: var.set(t.get("1.0", "end-1c")))
        self.texts[key] = t

    def sync_texts(self):
        for key, t in self.texts.items():
            if t.get("1.0", "end-1c") != self.vars[key].get():
                t.delete("1.0", "end")
                t.insert("1.0", self.vars[key].get())
