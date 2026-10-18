"""Data model: scenes + project settings (plain dataclasses, easy to save as JSON)."""
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

MOTION_CYCLE = ["push_in+float", "camera_push", "rise+float", "tilt+float", "parallax", "pull_out+float"]
TRANSITION_CYCLE = ["slide_left", "swap_depth", "slide_up", "spring", "zoom_through", "drop", "slide_right", "fade"]


@dataclass
class Scene:
    image: str = ""                 # file name inside screenshots/raw, or an absolute path
    duration: float = 3.2           # seconds (the transition into it is part of this time)
    motion: str = "push_in+float"   # see video/animations.py (combine with '+')
    transition: str = "slide_left"  # how this scene's phone replaces the previous scene's phone
    title: Optional[str] = None     # None = use the title from the design
    subtitle: Optional[str] = None  # "" hides it
    # taps: [{"x": 0..1, "y": 0..1, "start": seconds, "end": seconds}]  (seconds from the start of the scene)
    taps: list = field(default_factory=list)
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
    fps: int = 30
    width: int = 1080
    transition_duration: float = 0.75
    # text animation
    text_effect: str = "typing"     # "typing" (letter by letter with a caret) or "fade"
    type_speed: float = 15.0        # characters per second
    # audio (like tracks in a video editor)
    music: str = ""
    music_volume: int = 60
    music_fade_in: float = 0.6
    music_fade_out: float = 1.2
    typing_sound: str = ""          # a short clip, looped while text is being typed
    typing_volume: int = 70
    whoosh_sound: str = ""          # played at every scene transition
    whoosh_volume: int = 70
    duck: bool = True               # lower the music while typing

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

    def segments(self):
        """(list of Seg, total seconds). The outro is an extra segment that reuses the last screenshot."""
        segs, t = [], 0.0
        for sc in self.scenes:
            segs.append(Seg(t, sc.duration, sc, False, len(segs)))
            t += sc.duration
        if self.outro and self.app_name.strip() and self.scenes:
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


def auto_scenes(images, duration=3.2):
    return [make_scene(i, name, duration) for i, name in enumerate(images)]