"""Data model: scenes + project settings (plain dataclasses, easy to save as JSON)."""
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from config import VIDEO_EXT

MOTION_CYCLE = ["push_in+float", "camera_push", "rise+float", "tilt+float", "parallax", "pull_out+float"]
TRANSITION_CYCLE = ["slide_left", "swap_depth", "slide_up", "spring", "zoom_through", "drop", "slide_right", "fade"]


DEFAULT_HEADLINE, DEFAULT_SUBLINE = "GO DOWNLOAD IT NOW", "from the Play Store"
BLANK = "@blank"      # special media name: a scene with no phone, just the background and text


def is_video(name):
    return Path(str(name)).suffix.lower() in VIDEO_EXT


def is_blank(name):
    return str(name) == BLANK


@dataclass
class Scene:
    image: str = ""                 # screenshot OR video: file name inside the raw folder, or an absolute path
    duration: float = 3.2           # seconds (the transition into it is part of this time)
    motion: str = "push_in+float"   # see video/animations.py (combine with '+')
    transition: str = "slide_left"  # how this scene's phone replaces the previous scene's phone
    title: Optional[str] = None     # None = use the title from the design
    subtitle: Optional[str] = None  # "" hides it
    # taps: [{"x": 0..1, "y": 0..1, "start": seconds, "end": seconds}]  (seconds from the start of the scene)
    taps: list = field(default_factory=list)
    # highlights drawn on the screen for a limited time, with appear / disappear animations (see video/callouts.py)
    callouts: list = field(default_factory=list)
    transition_duration: float = 0.0   # 0 = project default
    # text (every scene can use its own animation)
    text_effect: str = "default"    # default | typing | fade | static | off
    type_speed: float = 0.0         # characters/second, 0 = project default
    text_delay: float = 0.0         # seconds before the text starts, 0 = automatic
    text_style: dict = field(default_factory=dict)   # overrides: title_size, text_y, text_align, colours...
    # phone (relative to the design)
    phone_scale: int = 100          # %
    phone_dx: float = 0.0           # % of canvas width
    phone_dy: float = 0.0           # % of canvas height
    phone_rot: float = 0.0          # degrees
    shot: dict = field(default_factory=dict)         # crop overrides: shot_fit, shot_zoom, shot_pan_x, shot_pan_y
    # video scenes only (see video/clips.py): trim_in, trim_out, speed, volume, mute, loop,
    # crop [x, y, w, h] (fractions of the frame), brightness, contrast, saturation
    clip: dict = field(default_factory=dict)
    # sound
    typing_sound: bool = True
    transition_sound: bool = True


@dataclass
class Project:
    scenes: list = field(default_factory=list)
    app_name: str = ""
    tagline: str = ""
    outro: bool = True              # end card with the app name and a Google Play button
    outro_duration: float = 3.2
    cta: str = "Get it on Google Play"
    # end card texts (any language) and look. outro_layout holds the position / size you dragged on the preview:
    # {"logo"|"name"|"tagline"|"divider"|"headline"|"subline"|"badge": {"dx": % of width, "dy": % of height, "scale": 1.0}}
    outro_headline: str = DEFAULT_HEADLINE   # "" hides it
    outro_subline: str = DEFAULT_SUBLINE     # "" hides it
    outro_badge: bool = True                 # the store badge (a pill, or your assets/video/play_badge.png)
    outro_badge_small: str = "GET IT ON"
    outro_badge_big: str = "Google Play"
    outro_font: str = ""                     # "" = the look's fonts (Ethiopic text finds an Ethiopic font by itself)
    outro_layout: dict = field(default_factory=dict)
    fps: int = 30
    width: int = 1080
    transition_duration: float = 0.75
    # text animation
    text_effect: str = "typing"     # "typing" (letter by letter with a caret) or "fade"
    type_speed: float = 15.0        # characters per second
    # audio (like tracks in a video editor)
    music: str = ""                 # old single-track setting; still plays when `tracks` is empty
    # background music tracks, each a dict: path, start, end (0 = to the end), offset, volume, fade_in, fade_out,
    # loop, len (see video/audio.py). Tracks that overlap on the timeline crossfade.
    tracks: list = field(default_factory=list)
    crossfade: float = 1.5          # default overlap / fade length when tracks are added or arranged
    music_volume: int = 60          # master volume and fades for all the music together
    music_fade_in: float = 0.6
    music_fade_out: float = 1.2
    typing_sound: str = ""          # a short clip, looped while text is being typed
    typing_volume: int = 70
    whoosh_sound: str = ""          # played at every scene transition
    whoosh_volume: int = 70
    tap_sound: str = ""             # a short click, played when each tap indicator presses
    tap_volume: int = 80
    duck: bool = True               # lower the music while typing or while a video clip is talking
    quality: str = "Standard"       # Draft | Standard | High (see config.QUALITY)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        if isinstance(d, list):
            d = {"scenes": d}
        sf = {f.name for f in fields(Scene)}
        scenes = []
        for s in d.get("scenes", []):
            s = dict(s)
            parts = [m.strip() for m in str(s.get("motion", "")).split("+")]
            if "tap" in parts:  # old projects: the tap used to be part of the motion
                s["motion"] = "+".join(m for m in parts if m != "tap") or "none"
                if not s.get("taps"):
                    a = 1.05 if scenes else 1.2
                    s["taps"] = [{"x": s.get("tap_x", 0.5), "y": s.get("tap_y", 0.58),
                                  "start": a, "end": round(a + 1.25, 2)}]
            scenes.append(Scene(**{k: v for k, v in s.items() if k in sf}))
        pf = {f.name for f in fields(cls)} - {"scenes"}
        return cls(scenes=scenes, **{k: v for k, v in d.items() if k in pf})

    def has_outro(self):
        """The end card shows when it is on and has something to say (an app name, a tagline or your own lines)."""
        if not (self.outro and self.scenes):
            return False
        custom = any(t.strip() and t != d for t, d in ((self.outro_headline, DEFAULT_HEADLINE), (self.outro_subline, DEFAULT_SUBLINE)))
        return bool(self.app_name.strip() or self.tagline.strip() or custom)

    def segments(self):
        """(list of Seg, total seconds). The outro is an extra segment that reuses the last screenshot."""
        segs, t = [], 0.0
        for sc in self.scenes:
            segs.append(Seg(t, sc.duration, sc, False, len(segs)))
            t += sc.duration
        if self.has_outro():
            o = Scene(image=self.scenes[-1].image, duration=self.outro_duration, motion="outro",
                      transition="cut", title=self.app_name, subtitle=self.tagline)
            segs.append(Seg(t, o.duration, o, True, len(segs)))
            t += o.duration
        return segs, t


@dataclass
class Seg:
    start: float
    dur: float
    scene: Scene
    outro: bool
    idx: int


def make_scene(index, image, duration=3.2):
    """A scene with a varied default look so a quick auto-built video doesn't feel repetitive."""
    sc = Scene(image=image, duration=duration, motion=MOTION_CYCLE[index % len(MOTION_CYCLE)],
               transition=TRANSITION_CYCLE[max(0, index - 1) % len(TRANSITION_CYCLE)])
    if index == 1:
        sc.taps = [{"x": 0.5, "y": 0.58, "start": 1.05, "end": 2.3}]
    return sc


def make_blank(index, duration=3.2):
    """A blank canvas scene: no phone, the text is centred on the background. Everything else (text effects,
    transitions, FX, sounds) works as in any other scene."""
    sc = make_scene(index, BLANK, duration)
    sc.motion, sc.transition, sc.taps = "none", "fade", []
    sc.title, sc.subtitle = "Your headline", "Add a supporting line"
    sc.text_style = {"title_size": 10.0, "subtitle_size": 4.4}
    return sc


def auto_scenes(images, duration=3.2):
    return [make_scene(i, name, duration) for i, name in enumerate(images)]