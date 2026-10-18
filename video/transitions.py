"""Phone transitions: the next scene's phone arrives while the previous phone moves away.
The screenshots themselves never move. Each transition gives two small Pose offsets:
`out(p)` for the leaving phone and `inn(p)` for the arriving one (arriving phone is drawn on top)."""
from collections import namedtuple

from video.animations import Pose, ease_in_out, ease_out_back, smooth

PhoneTransition = namedtuple("PhoneTransition", "out inn ease")


def _fade_out(p, start=0.5):  # leaving phone disappears during the second half
    return 1 - smooth((p - start) / (1 - start))


def _lateral(sign, ease):
    """Arrives from one side over the leaving phone, which recedes, tilts and fades out."""
    return PhoneTransition(
        out=lambda p: Pose(x=-sign * 38 * p, rot=sign * 3 * p, scale=1 - 0.08 * p, alpha=_fade_out(p, 0.55)),
        inn=lambda p: Pose(x=sign * 100 * (1 - p), rot=-sign * 4 * (1 - p)),
        ease=ease)


TRANSITIONS = {
    "slide_left":  _lateral(1, ease_in_out),
    "slide_right": _lateral(-1, ease_in_out),
    "spring":      _lateral(1, ease_out_back),
    "slide_up": PhoneTransition(
        out=lambda p: Pose(y=-12 * p, scale=1 - 0.07 * p, alpha=_fade_out(p, 0.5)),
        inn=lambda p: Pose(y=90 * (1 - p)), ease=ease_in_out),
    "drop": PhoneTransition(
        out=lambda p: Pose(y=10 * p, scale=1 - 0.07 * p, alpha=_fade_out(p, 0.5)),
        inn=lambda p: Pose(y=-110 * (1 - p)), ease=ease_out_back),
    "swap_depth": PhoneTransition(
        out=lambda p: Pose(x=-60 * p, rot=8 * p, scale=1 - 0.1 * p, alpha=_fade_out(p, 0.45)),
        inn=lambda p: Pose(x=95 * (1 - p), rot=-14 * (1 - p), scale=0.9 + 0.1 * p), ease=ease_in_out),
    "zoom_through": PhoneTransition(
        out=lambda p: Pose(scale=1 + 0.35 * p, alpha=1 - smooth(p * 1.25)),
        inn=lambda p: Pose(scale=0.82 + 0.18 * p, alpha=smooth((p - 0.1) / 0.7)), ease=smooth),
    "fade": PhoneTransition(out=lambda p: Pose(alpha=1 - p), inn=lambda p: Pose(alpha=p), ease=smooth),
    "cut": PhoneTransition(out=lambda p: Pose(alpha=0), inn=lambda p: Pose(), ease=smooth),
        # fast, energetic: the new phone whips in from the right while the old one is flung to the left
    "whip": PhoneTransition(
        out=lambda p: Pose(x=-70 * p, rot=12 * p, scale=1 - 0.1 * p, alpha=_fade_out(p, 0.35)),
        inn=lambda p: Pose(x=130 * (1 - p), rot=-18 * (1 - p)), ease=ease_in_out),
    # the old phone shrinks away, the new one pops up from small with a spring
    "pop": PhoneTransition(
        out=lambda p: Pose(scale=1 - 0.12 * p, alpha=_fade_out(p, 0.3)),
        inn=lambda p: Pose(scale=0.6 + 0.4 * p, alpha=smooth(p / 0.45)), ease=ease_out_back),
    # the new phone swings in with a big tilt
    "rotate_in": PhoneTransition(
        out=lambda p: Pose(x=-50 * p, rot=18 * p, scale=1 - 0.08 * p, alpha=_fade_out(p, 0.4)),
        inn=lambda p: Pose(x=60 * (1 - p), rot=-35 * (1 - p), scale=0.8 + 0.2 * p, alpha=smooth(p / 0.5)),
        ease=ease_in_out),
    # the new phone arrives diagonally from the bottom right
    "cascade": PhoneTransition(
        out=lambda p: Pose(x=-30 * p, y=-8 * p, rot=4 * p, scale=1 - 0.08 * p, alpha=_fade_out(p, 0.5)),
        inn=lambda p: Pose(x=70 * (1 - p), y=70 * (1 - p), rot=-8 * (1 - p)), ease=ease_in_out),
    # the old phone sinks down while the new one comes down from above (opposite of slide_up)
    "elevator_down": PhoneTransition(
        out=lambda p: Pose(y=40 * p, scale=1 - 0.07 * p, alpha=_fade_out(p, 0.45)),
        inn=lambda p: Pose(y=-100 * (1 - p)), ease=ease_in_out),
    # the old phone zooms toward the camera while turning, the new one grows in from behind
    "spin_zoom": PhoneTransition(
        out=lambda p: Pose(scale=1 + 0.3 * p, rot=10 * p, alpha=1 - smooth(p * 1.3)),
        inn=lambda p: Pose(scale=0.7 + 0.3 * p, rot=-10 * (1 - p), alpha=smooth((p - 0.1) / 0.6)),
        ease=smooth),
}
TRANSITION_NAMES = list(TRANSITIONS)

# names from the first version of the promo feature still work in saved projects
TRANSITIONS.update({"push": TRANSITIONS["swap_depth"], "blur": TRANSITIONS["fade"], "zoom": TRANSITIONS["zoom_through"]})
