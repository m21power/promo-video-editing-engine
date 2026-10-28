"""
Central configuration for Shot Studio.

Everything you are likely to tweak lives here: canvas sizes, default values for
every control, device frame styles, frame finishes, colour themes and fonts.
Add a key to DEFAULTS and a matching control in ui/app.py to add a new setting.
"""
import json
from pathlib import Path
from types import SimpleNamespace

TOOL_DIR = Path(__file__).resolve().parent

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
MEDIA_EXT = IMG_EXT | VIDEO_EXT


def project_paths(root, raw=None):
    """Folders used at runtime.

    `raw` is the folder you picked with your screenshots and videos (default: <root>/screenshots/raw).
    Presets, the promo project and exports live next to it:
      * a folder called "raw"  -> its parent holds presets/ and output/ (the classic screenshots/ layout)
      * any other folder       -> <folder>/_studio/presets and <folder>/_studio/output
    so every folder you open keeps its own settings and you never have to delete anything to switch.
    """
    root = Path(root).resolve()
    shots = root / "screenshots"
    raw = Path(raw).resolve() if raw else shots / "raw"
    work = raw.parent if raw.name.lower() == "raw" else raw / "_studio"
    return SimpleNamespace(
        root=root,
        raw=raw,
        output=work / "output",
        presets=work / "presets",           # per-screenshot settings + promo project (auto-saved)
        frames=TOOL_DIR / "assets" / "frames",   # drop custom PNG frames here
        fonts=TOOL_DIR / "assets" / "fonts",     # drop .ttf/.otf fonts here
        backgrounds=TOOL_DIR / "assets" / "backgrounds",  # drop custom background images here
        video_assets=TOOL_DIR / "assets" / "video",       # optional logo.png and play_badge.png for the end card
    )


# --------------------------------------------------------------------------- #
# Canvas sizes (Google Play: PNG/JPEG, 320-3840 px per side, max 2:1 ratio)
# --------------------------------------------------------------------------- #
CANVAS_PRESETS = {
    "Phone 9:16 · 1080×1920": (1080, 1920),
    "Phone 18:9 · 1080×2160": (1080, 2160),
    "Phone QHD · 1440×2560": (1440, 2560),
    "Tablet 7″ · 1200×1920": (1200, 1920),
    "Tablet 10″ · 1600×2560": (1600, 2560),
    "Feature graphic · 1024×500": (1024, 500),
}

# --------------------------------------------------------------------------- #
# Built-in procedural phone frames. All sizes are fractions of the body width.
#   aspect  body height / width        bezel   screen inset
#   radius  outer corner radius        camera  "punch" | "pill" | None
#   cam     camera diameter            buttons side buttons on/off
#   body    False = frameless (just a rounded screen)
# --------------------------------------------------------------------------- #
FRAME_STYLES = {
    "Pixel":     dict(aspect=2.17, bezel=0.030, radius=0.135, camera="punch", cam=0.036, buttons=True),
    "Slim":      dict(aspect=2.22, bezel=0.020, radius=0.125, camera="punch", cam=0.030, buttons=True),
    "Flat edge": dict(aspect=2.10, bezel=0.034, radius=0.060, camera="punch", cam=0.034, buttons=True),
    "Classic":   dict(aspect=2.00, bezel=0.052, radius=0.095, camera="pill",  cam=0.0,   buttons=True),
    "Frameless": dict(aspect=2.17, bezel=0.0,   radius=0.075, camera=None,    cam=0.0,   buttons=False, body=False),
}

FRAME_FINISHES = {
    "Obsidian": "#15171C", "Graphite": "#2B2E36", "Titanium": "#8E9196",
    "Silver": "#D6D9DE", "Porcelain": "#EFEAE2", "Sky": "#8DB6E8",
    "Mint": "#A9D9C3", "Rose": "#E9B9BE", "Gold": "#D9B77E",
}

# --------------------------------------------------------------------------- #
# Colour themes: one click sets background, glow and text colours.
# --------------------------------------------------------------------------- #
THEMES = {
    "Midnight Indigo": dict(bg_mode="Linear gradient", bg_color1="#0B1020", bg_color2="#2B1C73", bg_angle=160,
                            glow_color="#6C63FF", glow_strength=55, title_color="#FFFFFF", subtitle_color="#C9CCF5"),
    "Aurora":          dict(bg_mode="Linear gradient", bg_color1="#04121F", bg_color2="#0B6E6E", bg_angle=150,
                            glow_color="#2DD4BF", glow_strength=50, title_color="#FFFFFF", subtitle_color="#BFF5EA"),
    "Sunset":          dict(bg_mode="Linear gradient", bg_color1="#FF5F6D", bg_color2="#FFC371", bg_angle=200,
                            glow_color="#FFE3B3", glow_strength=45, title_color="#FFFFFF", subtitle_color="#FFF1E6"),
    "Play Green":      dict(bg_mode="Radial gradient", bg_color1="#0FB78A", bg_color2="#014B35", bg_angle=0,
                            glow_color="#7CFFD1", glow_strength=35, title_color="#FFFFFF", subtitle_color="#D5FFF1"),
    "Ocean":           dict(bg_mode="Linear gradient", bg_color1="#1A73E8", bg_color2="#0A2A66", bg_angle=170,
                            glow_color="#7FB2FF", glow_strength=45, title_color="#FFFFFF", subtitle_color="#D3E4FF"),
    "Clean light":     dict(bg_mode="Linear gradient", bg_color1="#F7F8FC", bg_color2="#DDE3F3", bg_angle=170,
                            glow_color="#FFFFFF", glow_strength=70, title_color="#0F1226", subtitle_color="#555B7A"),
    "Mono dark":       dict(bg_mode="Linear gradient", bg_color1="#0A0A0B", bg_color2="#1B1B20", bg_angle=180,
                            glow_color="#FFFFFF", glow_strength=12, title_color="#FFFFFF", subtitle_color="#A8ABB8"),
}

# --------------------------------------------------------------------------- #
# Fonts. First match (case-insensitive file stem) wins; "Auto" uses these lists.
# Drop your own .ttf/.otf into assets/fonts/ and they appear in the font menus.
# --------------------------------------------------------------------------- #
DEFAULT_TITLE_FONTS = ["Inter-Bold", "Inter_28pt-Bold", "Poppins-Bold", "Roboto-Bold", "SegoeUI-Bold",
                       "segoeuib", "SF-Pro-Display-Bold", "HelveticaNeue-Bold", "Helvetica", "arialbd",
                       "Arial Bold", "DejaVuSans-Bold", "LiberationSans-Bold"]
DEFAULT_BODY_FONTS = ["Inter-Regular", "Inter_28pt-Regular", "Poppins-Regular", "Roboto-Regular", "segoeui",
                      "SegoeUI", "HelveticaNeue", "Helvetica", "arial", "Arial", "DejaVuSans", "LiberationSans-Regular"]

# --------------------------------------------------------------------------- #
# Defaults for every setting. Numbers are percentages of canvas width/height
# unless noted, so a design looks identical at any export resolution.
# Use floats for fine-grained sliders and ints for whole-number ones.
# --------------------------------------------------------------------------- #
DEFAULTS = dict(
    canvas=next(iter(CANVAS_PRESETS)),

    # background
    bg_mode="Linear gradient",       # Solid | Linear gradient | Radial gradient | Image | Image
    bg_color1="#0B1020", bg_color2="#2B1C73", bg_angle=160,
    glow_color="#6C63FF", glow_strength=55,
    bg_image="None",                 # file name inside assets/backgrounds (used when bg_mode = Image)
    bg_img_zoom=100, bg_img_pan_x=0, bg_img_pan_y=0, bg_img_dim=0,

    # text
    title="Everything you need,\nin one place", subtitle="Simple. Fast. Beautiful.",
    title_font="Auto", subtitle_font="Auto",
    title_size=7.6, subtitle_size=3.7,                 # % of canvas width
    title_color="#FFFFFF", subtitle_color="#C9CCF5", subtitle_opacity=90,
    text_align="center", text_x=0.0, text_y=7.0,       # text_y: % of height
    text_margin=8.0, line_spacing=1.1, text_gap=2.4,

    # phone
    frame="Pixel", frame_color="#15171C", glass_sheen=40, phone_shadow=70,
    phone_scale=78, phone_x=0.0,
    auto_phone_y=True, phone_gap=5.0,                  # place below the text
    phone_y=26.0,                                      # manual top position
    phone_rotation=0.0,

    # screenshot inside the phone
    shot_fit="Cover",                                  # Cover | Contain | Stretch
    shot_zoom=100, shot_pan_x=0, shot_pan_y=0,
)


# --------------------------------------------------------------------------- #
# Promo video defaults
# --------------------------------------------------------------------------- #
VIDEO = dict(fps=30, width=1440, scene_duration=3.2, outro_duration=3.2, transition=0.75,
             cta="Get it on Google Play")

# export quality: name -> (x264 CRF, x264 preset). Lower CRF = better quality and bigger file.
QUALITY = {"Draft": (26, "veryfast"), "Standard": (16, "medium"), "High": (12, "slow"),
           "Ultra (TikTok/Reels)": (10, "slow"), "Max": (8, "slower")}
SOCIAL_QUALITY = "Ultra (TikTok/Reels)"


# --------------------------------------------------------------------------- #
# Remembered folders (so the app reopens where you left off)
# --------------------------------------------------------------------------- #
STATE_FILE = Path.home() / ".shot_studio.json"


def _state():
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def recent_folders():
    return [f for f in _state().get("recent", []) if Path(f).is_dir()]


def last_folder():
    r = recent_folders()
    return Path(r[0]) if r else None


def remember_folder(folder):
    folder = str(Path(folder).resolve())
    recent = [folder] + [f for f in _state().get("recent", []) if f != folder]
    try:
        STATE_FILE.write_text(json.dumps({"recent": recent[:10]}, indent=2), "utf-8")
    except OSError:
        pass