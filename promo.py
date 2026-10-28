"""Build a promo MP4 from screenshots, using the look you designed in Shot Studio.

    python promo.py --name "My App" --tagline "Everything in one place"
    python promo.py --screens screenshots/raw --template screenshots/presets/01.json --music song.mp3
    python promo.py --scenes my_scenes.json          # full control (see README)
"""
import argparse
import json
import sys
from pathlib import Path

import config
from core import fonts, frames, renderer
from core.settings import Settings
from video.engine import find_ffmpeg, load_image, render_video, resolve_image
from video import clips
from video.timeline import Project, auto_scenes, is_blank, is_video

MEDIA_EXT = config.MEDIA_EXT   # screenshots and videos


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=config.TOOL_DIR, help="folder containing screenshots/")
    ap.add_argument("--raw", type=Path, help="folder with your screenshots and videos (default: screenshots/raw)")
    ap.add_argument("--screens", nargs="*", type=Path, help="folder or image/video files (default: the raw folder)")
    ap.add_argument("--template", type=Path, help="Shot Studio settings .json to use as the look")
    ap.add_argument("--scenes", type=Path, help="JSON with scenes/project settings (overrides --screens)")
    ap.add_argument("--name", default="", help="app name shown on the end card")
    ap.add_argument("--tagline", default="")
    ap.add_argument("--cta", default=config.VIDEO["cta"])
    ap.add_argument("--headline", help="end card big line (default: GO DOWNLOAD IT NOW; any language)")
    ap.add_argument("--subline", help="end card small line (default: from the Play Store)")
    ap.add_argument("--no-outro", action="store_true")
    ap.add_argument("--duration", type=float, default=config.VIDEO["scene_duration"], help="seconds per scene")
    ap.add_argument("--fps", type=int, default=config.VIDEO["fps"])
    ap.add_argument("--width", type=int, default=config.VIDEO["width"])
    ap.add_argument("--music", default="")
    ap.add_argument("--music-volume", type=int, default=60)
    ap.add_argument("--typing-sound", default="", help="short clip looped while text is typed")
    ap.add_argument("--whoosh", default="", help="sound played at each scene transition")
    ap.add_argument("--tap-sound", default="", help="click sound played when a tap indicator presses")
    ap.add_argument("--effect", choices=["typing", "fade"], default="typing", help="text animation")
    ap.add_argument("--quality", choices=list(config.QUALITY), default=config.SOCIAL_QUALITY, help="export quality")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    paths = config.project_paths(a.project, a.raw)
    fonts.set_custom_dir(paths.fonts)
    frames.set_custom_dir(paths.frames)
    renderer.set_backgrounds_dir(paths.backgrounds)

    tpl = a.template or next(iter(sorted(paths.presets.glob("*.json"))), None) if paths.presets.exists() else a.template
    design = Settings(**json.loads(Path(tpl).read_text("utf-8"))) if tpl else Settings()

    if a.scenes:
        project = Project.from_dict(json.loads(a.scenes.read_text("utf-8")))
    else:
        files = []
        for src in (a.screens or [paths.raw]):
            files += sorted(p for p in src.iterdir() if p.suffix.lower() in MEDIA_EXT) if src.is_dir() else [src]
        if not files:
            sys.exit(f"No screenshots or videos found. Add some to {paths.raw}")
        project = Project(scenes=auto_scenes([f.name if f.parent == paths.raw else str(f.resolve()) for f in files], a.duration))
        for sc in project.scenes:        # an auto-built video scene is as long as its clip (a --scenes file keeps its own trims)
            if is_video(sc.image):
                clips.init_scene(find_ffmpeg(), sc, resolve_image(paths.raw, sc.image))
                sc.motion = "float"
    for key, val in (("app_name", a.name), ("tagline", a.tagline), ("music", a.music)):
        if val:
            setattr(project, key, val)
    project.cta, project.fps, project.outro = a.cta, a.fps, not a.no_outro
    if a.headline is not None:
        project.outro_headline = a.headline
    if a.subline is not None:
        project.outro_subline = a.subline
    project.text_effect, project.music_volume = a.effect, a.music_volume
    project.quality = a.quality
    project.tap_sound = a.tap_sound or project.tap_sound
    project.typing_sound, project.whoosh_sound = a.typing_sound or project.typing_sound, a.whoosh or project.whoosh_sound
    images = {sc.image: load_image(resolve_image(paths.raw, sc.image)) for sc in project.scenes
              if not is_video(sc.image) and not is_blank(sc.image)}

    slug = "".join(c if c.isalnum() else "_" for c in (project.app_name or "promo")).strip("_") or "promo"
    out = a.out or paths.output / f"{slug}_promo.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    ok = render_video(design, project, images, out, a.width, assets_dir=paths.video_assets, raw_dir=paths.raw,
                      progress=lambda i, n, f=None: print(f"\rRendering {i * 100 // n}%", end="", flush=True) if i % 5 == 0 or i == n else None)
    print(f"\nSaved {out}" if ok else "\nCancelled")


if __name__ == "__main__":
    main()