"""Dark studio theme for ttk. Change the colour constants to re-skin the app."""
import sys
from tkinter import ttk

BG, BAR, PANEL, CARD = "#0B0D12", "#10131A", "#141823", "#1B2030"
HOVER, BORDER = "#262C42", "#2A3047"
TEXT, MUTED = "#E8EAF2", "#8189A3"
ACCENT, ACCENT_HOVER, ACCENT_SOFT = "#7B61FF", "#9279FF", "#B3A5FF"

FAMILY = "Segoe UI" if sys.platform.startswith("win") else ("Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans")
FONT = (FAMILY, 10)
FONT_BOLD = (FAMILY, 10, "bold")


def apply(root):
    st = ttk.Style(root)
    st.theme_use("clam")
    root.configure(bg=BG)
    st.configure(".", background=PANEL, foreground=TEXT, fieldbackground=CARD, bordercolor=BORDER,
                 lightcolor=PANEL, darkcolor=PANEL, troughcolor=CARD, focuscolor=PANEL, font=FONT)
    st.configure("TFrame", background=PANEL)
    st.configure("Bg.TFrame", background=BG)
    st.configure("Bar.TFrame", background=BAR)
    st.configure("TLabel", background=PANEL, foreground=TEXT)
    st.configure("Brand.TLabel", background=BAR, font=(FAMILY, 13, "bold"))
    st.configure("BarMuted.TLabel", background=BAR, foreground=MUTED)
    st.configure("Info.TLabel", background=BG, foreground=MUTED)
    st.configure("Status.TLabel", background=BAR, foreground=MUTED, padding=(14, 6))
    st.configure("Muted.TLabel", foreground=MUTED)
    st.configure("Value.TLabel", foreground=ACCENT_SOFT)
    st.configure("Section.TLabel", foreground=ACCENT_SOFT, font=FONT_BOLD)

    st.configure("TButton", background=CARD, foreground=TEXT, bordercolor=BORDER, padding=(12, 7), relief="flat")
    st.map("TButton", background=[("active", HOVER), ("pressed", BORDER)])
    st.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF", bordercolor=ACCENT, font=FONT_BOLD, padding=(16, 7))
    st.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("pressed", ACCENT)])
    st.configure("Toolbutton", background=CARD, foreground=TEXT, padding=(10, 6), borderwidth=0, relief="flat")
    st.map("Toolbutton", background=[("selected", ACCENT), ("active", HOVER)], foreground=[("selected", "#FFFFFF")])

    st.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(22, 10), borderwidth=0,
                 bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
    st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", TEXT)],
           bordercolor=[("selected", CARD)], lightcolor=[("selected", CARD)], darkcolor=[("selected", CARD)])

    for w in ("TEntry", "TCombobox"):
        st.configure(w, fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER, arrowcolor=MUTED, padding=5)
    st.map("TCombobox", fieldbackground=[("readonly", CARD)], selectbackground=[("readonly", CARD)],
           selectforeground=[("readonly", TEXT)], arrowcolor=[("active", TEXT)])
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")

    st.configure("Horizontal.TScale", background=ACCENT, troughcolor=BORDER, bordercolor=PANEL,
                 lightcolor=ACCENT, darkcolor=ACCENT, sliderlength=16)
    st.map("Horizontal.TScale", background=[("active", ACCENT_HOVER)])
    for name, bg in (("TCheckbutton", PANEL), ("Bg.TCheckbutton", BG)):
        st.configure(name, background=bg, foreground=TEXT, indicatorbackground=CARD, indicatorforeground="#FFFFFF",
                     indicatorcolor=CARD, upperbordercolor=MUTED, lowerbordercolor=MUTED)
        st.map(name, indicatorbackground=[("selected", ACCENT)], indicatorcolor=[("selected", ACCENT)],
               background=[("active", bg)], foreground=[("active", TEXT)])
    st.configure("TSeparator", background=BORDER)

    st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, borderwidth=0, rowheight=88)
    st.map("Treeview", background=[("selected", HOVER)], foreground=[("selected", "#FFFFFF")])
    st.configure("Scenes.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, borderwidth=0, rowheight=30)
    st.map("Scenes.Treeview", background=[("selected", HOVER)], foreground=[("selected", "#FFFFFF")])
    st.configure("Treeview.Heading", background=CARD, foreground=MUTED, borderwidth=0, padding=(8, 6), font=FONT)
    st.configure("Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT, bordercolor=PANEL,
                 lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
    st.configure("Vertical.TScrollbar", background=CARD, troughcolor=PANEL, bordercolor=PANEL, arrowcolor=MUTED, gripcount=0)
    st.map("Vertical.TScrollbar", background=[("active", HOVER)])
