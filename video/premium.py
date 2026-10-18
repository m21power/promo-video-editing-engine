"""Premium animation pack. Importing this module registers new motions, transitions and combos
into video.animations / video.transitions, and exposes `entries(kind)` for the searchable picker."""
import math

from video import animations as A
from video import transitions as T
from video.animations import Pose, clamp01, ease_in_out, ease_out, ease_out_back, ease_out_bounce, smooth
from video.transitions import PhoneTransition, _fade_out, _lateral


# ------------------------------------------------------------------ curves
def expo(x):  # fast start, very long soft landing: the "expensive" camera feel
    x = clamp01(x)
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def elastic(x):  # overshoots and wobbles into place
    x = clamp01(x)
    if x <= 0 or x >= 1:
        return x
    return 2 ** (-10 * x) * math.sin((x * 10 - 0.75) * (2 * math.pi / 3)) + 1


def sine(t, period, phase=0.0):
    return math.sin(2 * math.pi * t / period + phase)


def wander(t, seed):  # smooth organic pseudo-random value in about -1..1 (handheld camera feel)
    return (math.sin(t * 1.3 + seed) + 0.6 * math.sin(t * 2.7 + seed * 2.1) + 0.35 * math.sin(t * 5.1 + seed * 0.7)) / 1.95


def beat(t, period, sharp=6):  # 0..1 pulse once per period
    return max(0.0, math.sin(math.pi * (t % period) / period)) ** sharp


# ----------------------------------------------------------------- motions
# (u = progress 0..1, t = seconds into scene, d = scene duration) -> Pose
NEW_MOTIONS = {
    # entrances (play in the first ~1.5 s, then hold: combine with an ambient motion)
    "hero_reveal":  lambda u, t, d: Pose(scale=0.80 + 0.20 * expo(t / 1.5), y=8 * (1 - expo(t / 1.5)), rot=-6 * (1 - expo(t / 1.5))),
    "elastic_pop":  lambda u, t, d: Pose(scale=0.55 + 0.45 * elastic(t / 1.3)),
    "elastic_drop": lambda u, t, d: Pose(y=-16 * (1 - elastic(t / 1.4)), rot=3 * (1 - ease_out(t / 1.2))),
    "launch":       lambda u, t, d: Pose(y=40 * (1 - expo(t / 1.1)), scale=0.85 + 0.15 * expo(t / 1.1), rot=4 * (1 - expo(t / 1.1))),
    "swoop":        lambda u, t, d: Pose(x=-18 * (1 - expo(t / 1.3)), y=10 * (1 - expo(t / 1.3)) ** 2, rot=-12 * (1 - expo(t / 1.3))),
    "spiral_in":    lambda u, t, d: Pose(rot=-28 * (1 - expo(t / 1.5)), scale=0.55 + 0.45 * expo(t / 1.5),
                                         x=-10 * (1 - expo(t / 1.5)), y=6 * (1 - expo(t / 1.5))),
    "snap_zoom":    lambda u, t, d: Pose(scale=1 + 0.16 * (1 - expo(t / 0.7))),
    "wobble_in":    lambda u, t, d: Pose(rot=12 * math.exp(-3.5 * t) * math.cos(10 * t), scale=0.92 + 0.08 * ease_out(t / 0.6)),
    "zoom_out_reveal": lambda u, t, d: Pose(scale=1.18 - 0.18 * expo(t / 1.6)),
    # camera moves (follow the whole scene)
    "dolly_zoom":   lambda u, t, d: Pose(scale=1 + 0.14 * ease_in_out(u), y=-2.5 * ease_in_out(u), rot=-1.5 * ease_in_out(u)),
    "cinematic_drift": lambda u, t, d: Pose(x=-2.5 + 5 * smooth(u), y=1.5 - 3 * smooth(u), rot=1.8 - 3.6 * smooth(u),
                                            scale=1.02 + 0.05 * smooth(u)),
    "crane_up":     lambda u, t, d: Pose(y=6 - 12 * smooth(u), scale=1.0 + 0.04 * smooth(u)),
    # ambient (never stop moving)
    "handheld":     lambda u, t, d: Pose(x=0.5 * wander(t, 1), y=0.6 * wander(t, 2), rot=0.5 * wander(t, 3), scale=1 + 0.006 * wander(t, 4)),
    "breathe":      lambda u, t, d: Pose(scale=1 + 0.018 * sine(t, 3.2)),
    "levitate":     lambda u, t, d: Pose(y=1.6 * sine(t, 2.8), rot=0.8 * sine(t, 5.6), scale=1 + 0.012 * sine(t, 2.8, 1.57)),
    "pendulum":     lambda u, t, d: Pose(rot=4 * sine(t, 3.6), x=1.2 * sine(t, 3.6, -1.57)),
    "slow_orbit":   lambda u, t, d: Pose(x=2.2 * sine(t, 6), y=1.2 * sine(t, 3), rot=2 * sine(t, 6, 1.0), scale=1.03 + 0.01 * sine(t, 3)),
    "gyro":         lambda u, t, d: Pose(rot=3 * sine(t, 4.4), x=2 * sine(t, 4.4, 1.57), y=0.8 * sine(t, 2.2)),
    # music-friendly
    "heartbeat":    lambda u, t, d: Pose(scale=1 + 0.035 * (beat(t, 1.2, 10) + 0.5 * beat(t + 0.25, 1.2, 10))),
    "beat_bounce":  lambda u, t, d: Pose(scale=1 + 0.025 * beat(t, 0.5, 6), y=-0.8 * beat(t, 0.5, 6)),
}

COMBOS = ["hero_reveal+handheld", "hero_reveal+breathe", "elastic_pop+levitate", "elastic_drop+levitate",
          "launch+levitate", "launch+handheld", "swoop+handheld", "spiral_in+slow_orbit", "snap_zoom+breathe",
          "dolly_zoom+handheld", "cinematic_drift+handheld", "crane_up+levitate", "wobble_in+levitate",
          "zoom_out_reveal+handheld", "hero_reveal+slow_orbit", "swoop+gyro", "elastic_pop+heartbeat",
          "snap_zoom+beat_bounce", "spiral_in+handheld", "launch+cinematic_drift"]

# ------------------------------------------------------------- transitions
NEW_TRANSITIONS = {
    "launch": PhoneTransition(
        out=lambda p: Pose(y=-55 * p, scale=1 - 0.15 * p, alpha=_fade_out(p, 0.3)),
        inn=lambda p: Pose(y=85 * (1 - p), scale=0.9 + 0.1 * p), ease=ease_out_back),
    "whirl": PhoneTransition(
        out=lambda p: Pose(x=-30 * p, rot=40 * p, scale=1 - 0.3 * p, alpha=_fade_out(p, 0.3)),
        inn=lambda p: Pose(x=40 * (1 - p), rot=-40 * (1 - p), scale=0.7 + 0.3 * p, alpha=smooth(p / 0.5)), ease=ease_in_out),
    "stack_peel": PhoneTransition(
        out=lambda p: Pose(x=-110 * p, rot=6 * p, alpha=_fade_out(p, 0.7)),
        inn=lambda p: Pose(scale=0.88 + 0.12 * p, alpha=smooth(p / 0.5)), ease=ease_in_out),
    "bounce_in": PhoneTransition(
        out=lambda p: Pose(y=8 * p, scale=1 - 0.06 * p, alpha=_fade_out(p, 0.3)),
        inn=lambda p: Pose(y=-100 * (1 - p)), ease=ease_out_bounce),
    "elastic_slide": _lateral(1, elastic),
    "zoom_whip": PhoneTransition(
        out=lambda p: Pose(x=-40 * p, scale=1 + 0.5 * p, alpha=1 - smooth(p * 1.4)),
        inn=lambda p: Pose(x=60 * (1 - p), rot=-10 * (1 - p), scale=1.4 - 0.4 * p, alpha=smooth(p / 0.5)), ease=ease_in_out),
    "carousel": PhoneTransition(
        out=lambda p: Pose(x=-60 * p, scale=1 - 0.18 * p, rot=-6 * p, alpha=_fade_out(p, 0.5)),
        inn=lambda p: Pose(x=60 * (1 - p), scale=0.82 + 0.18 * p, rot=6 * (1 - p)), ease=ease_in_out),
    "tumble": PhoneTransition(
        out=lambda p: Pose(y=30 * p, rot=-35 * p, alpha=_fade_out(p, 0.3)),
        inn=lambda p: Pose(y=-70 * (1 - p), rot=30 * (1 - p)), ease=ease_out_back),
    "diag_swoop": PhoneTransition(
        out=lambda p: Pose(x=40 * p, y=-20 * p, rot=-6 * p, alpha=_fade_out(p, 0.4)),
        inn=lambda p: Pose(x=-90 * (1 - p), y=60 * (1 - p), rot=14 * (1 - p)), ease=ease_in_out),
    "dolly_through": PhoneTransition(
        out=lambda p: Pose(scale=1 + 0.8 * p, alpha=1 - smooth(p * 1.2)),
        inn=lambda p: Pose(scale=0.55 + 0.45 * p, alpha=smooth((p - 0.15) / 0.6)), ease=smooth),
    "pop_burst": PhoneTransition(
        out=lambda p: Pose(scale=1 + 0.15 * p, alpha=1 - smooth(p * 2)),
        inn=lambda p: Pose(scale=0.3 + 0.7 * p, rot=-12 * (1 - p), alpha=smooth(p / 0.3)), ease=ease_out_back),
}

# ----------------------------------------------------- catalog (for search)
# name: (category, description, search tags)
INFO = {
    # motions
    "none": ("Ambient", "Phone stays perfectly still", "static still off"),
    "push_in": ("Camera", "Slow push toward the phone", "zoom in slow cinematic"),
    "pull_out": ("Camera", "Starts close and pulls back", "zoom out reveal"),
    "camera_push": ("Camera", "Push in with a slight upward drift", "zoom dolly cinematic"),
    "float": ("Ambient", "Gentle floating bob", "hover bob soft"),
    "rise": ("Entrance", "Rises up into place", "up slide soft"),
    "tilt": ("Entrance", "Starts tilted, straightens", "rotate lean"),
    "parallax": ("Camera", "Slow sideways pan with tilt", "pan 3d depth"),
    "drop": ("Entrance", "Drops from above and bounces", "fall bounce gravity"),
    "swing": ("Entrance", "Swings in with overshoot", "rotate spring"),
    "spring_pop": ("Entrance", "Pops in with a spring", "scale bounce"),
    "punch": ("Entrance", "Starts big, punches down", "impact hit zoom"),
    "slide_up": ("Entrance", "Slides up with a soft spring", "rise spring"),
    "orbit": ("Ambient", "Small circular drift", "circle float"),
    "showcase": ("Camera", "Slow rotation sweep for hero shots", "turn hero rotate"),
    "hero_reveal": ("Entrance", "Premium expo ease: scale, lift and untilt", "luxury apple smooth hero"),
    "elastic_pop": ("Entrance", "Elastic pop with a jelly wobble", "jelly bounce spring"),
    "elastic_drop": ("Entrance", "Drops in and wobbles into place", "fall jelly"),
    "launch": ("Entrance", "Rockets up from below, soft landing", "rise lift expo"),
    "swoop": ("Entrance", "Curved swoop in from the left", "arc fly curve"),
    "spiral_in": ("Entrance", "Spins and grows in from small", "rotate zoom twirl"),
    "snap_zoom": ("Entrance", "Starts zoomed, snaps into frame", "punch fast whip"),
    "wobble_in": ("Entrance", "Damped wobble settle", "shake jiggle settle"),
    "zoom_out_reveal": ("Entrance", "Big zoom out to reveal the phone", "pull back reveal"),
    "dolly_zoom": ("Camera", "Strong slow push with lift", "cinematic dolly push"),
    "cinematic_drift": ("Camera", "Diagonal drift with slow rotation", "film glide pan"),
    "crane_up": ("Camera", "Crane move: sinks then rises", "lift boom"),
    "handheld": ("Ambient", "Organic handheld camera sway", "shake realistic documentary"),
    "breathe": ("Ambient", "Subtle breathing scale", "pulse soft"),
    "levitate": ("Ambient", "Premium float with micro tilt", "hover float magic"),
    "pendulum": ("Ambient", "Smooth swinging sway", "swing rock"),
    "slow_orbit": ("Ambient", "Slow wide orbit", "circle drift"),
    "gyro": ("Ambient", "Gyroscope-like tilt wander", "tilt wander"),
    "heartbeat": ("Beat", "Double pulse like a heartbeat", "pulse music"),
    "beat_bounce": ("Beat", "Bounces on a 120 BPM beat", "music rhythm dance"),
    # transitions
    "slide_left": ("Slide", "New phone slides in from the right", "swipe side"),
    "slide_right": ("Slide", "New phone slides in from the left", "swipe side"),
    "spring": ("Slide", "Slide with a springy overshoot", "bounce side"),
    "slide_up": ("Slide", "New phone comes up from below", "rise"),
    "drop": ("Slide", "New phone drops from above", "fall bounce"),
    "swap_depth": ("3D", "Swap with depth and tilt", "depth rotate"),
    "zoom_through": ("Zoom", "Fly through the old phone", "dolly depth"),
    "fade": ("Soft", "Clean cross fade", "dissolve smooth"),
    "cut": ("Soft", "Hard cut, no motion", "instant"),
    "whip": ("Energetic", "Fast whip pan", "fast quick speed"),
    "pop": ("Energetic", "New phone springs up from small", "scale bounce"),
    "rotate_in": ("3D", "Big tilt swing in", "rotate swing"),
    "cascade": ("Slide", "Diagonal arrival from bottom right", "diagonal"),
    "elevator_down": ("Slide", "New phone comes down from above", "drop lower"),
    "spin_zoom": ("Zoom", "Old phone zooms and turns away", "rotate zoom"),
    "whirl": ("3D", "Old spins away, new spins in", "spin twirl rotate"),
    "stack_peel": ("Premium", "Top phone peels away, next grows behind", "card stack peel depth"),
    "bounce_in": ("Energetic", "New phone drops and bounces", "fall bounce"),
    "elastic_slide": ("Premium", "Slide with jelly overshoot", "wobble spring"),
    "zoom_whip": ("Premium", "Whip pan with a zoom burst", "fast speed zoom"),
    "carousel": ("Premium", "Carousel rotation, depth scaling", "rotate gallery 3d"),
    "tumble": ("Energetic", "Old tumbles away, new tumbles in", "fall rotate"),
    "diag_swoop": ("Premium", "Diagonal swoop with tilt", "arc fly"),
    "dolly_through": ("Zoom", "Cinematic dolly through the old phone", "film push depth"),
    "pop_burst": ("Energetic", "New phone bursts out with spin", "explode scale"),
    "launch_t": ("Premium", "", ""),
}
INFO.pop("launch_t")
INFO["launch@t"] = ("Premium", "Old launches up, new rises from below", "rocket lift")


def _info(kind, name):
    if kind == "transition" and name == "launch":
        return INFO["launch@t"]
    if kind == "transition" and name == "drop":
        return ("Slide", "New phone drops in from above", "fall bounce")
    if kind == "transition" and name == "slide_up":
        return ("Slide", "New phone comes up from below", "rise")
    return INFO.get(name, ("Other", "", ""))


def entries(kind):
    """Searchable rows for the picker: dicts with name, category, desc, tags."""
    out = []
    if kind == "motion":
        for n in A.MOTIONS:
            if n == "outro":
                continue
            c, d, tg = INFO.get(n, ("Other", "", ""))
            out.append(dict(name=n, category=c, desc=d, tags=tg))
        seen = set()
        for spec in A.MOTION_PRESETS:
            if "+" in spec and spec not in seen:
                seen.add(spec)
                parts = spec.split("+")
                out.append(dict(name=spec, category="Combo", desc=" + ".join(parts),
                                tags="combo mix " + " ".join(INFO.get(p, ("", "", ""))[2] for p in parts)))
    else:
        for n in T.TRANSITION_NAMES:
            c, d, tg = _info("transition", n)
            out.append(dict(name=n, category=c, desc=d, tags=tg))
    return out


# ------------------------------------------------------------ registration
A.MOTIONS.update(NEW_MOTIONS)
T.TRANSITIONS.update(NEW_TRANSITIONS)
for _n in NEW_TRANSITIONS:
    if _n not in T.TRANSITION_NAMES:
        T.TRANSITION_NAMES.append(_n)
for _spec in list(NEW_MOTIONS) + COMBOS:
    if _spec not in A.MOTION_PRESETS:
        A.MOTION_PRESETS.append(_spec)