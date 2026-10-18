# Shot Studio

Local, offline GUI for turning raw emulator screenshots into Google Play promo graphics.

## Run

```bash
pip install -r requirements.txt     # Pillow only; Tkinter ships with Python
python main.py                      # uses ./screenshots/raw and ./screenshots/output
python main.py --project ~/my-app   # or point at another project that has screenshots/raw
```

Linux may need `sudo apt install python3-tk`.

## Workflow

1. Put emulator screenshots in `screenshots/raw/` and pick one on the left.
2. Edit text, phone, and style. The preview is the real composition.
3. **Export PNG** (Ctrl+E) writes `screenshots/output/<name>_playstore.png` at full resolution.
4. **Apply style to all** copies the look to every other screenshot, keeping each one's own title, subtitle and crop. **Export all** renders the whole set.

Settings are auto-saved per screenshot in `screenshots/presets/`. **Save/Load template** stores a look anywhere.
In the preview, drag to move the phone and scroll to resize it.

## Customising

| What | Where |
| --- | --- |
| Canvas sizes, default values, themes, frame finishes, built-in frame shapes, default fonts | `config.py` |
| Custom background images (`.png`, `.jpg`, `.webp`) | drop into `assets/backgrounds/`, then Style tab, Background |
| Your own fonts (`.ttf`, `.otf`) | drop into `assets/fonts/`, they appear in the font menus |
| Your own device frames | PNG with a **transparent screen area** in `assets/frames/`; the screen shape is detected automatically |
| New setting | add a key to `DEFAULTS` in `config.py`, use it in `core/renderer.py`, add one control line in `ui/app.py` |

## Layout

```
main.py            entry point
config.py          all tunable values
core/renderer.py   composition (background, glow, text, phone, shadow)
core/frames.py     procedural + PNG phone frames
core/fonts.py      font discovery
core/settings.py   typed settings
ui/app.py          window, preview, export
ui/widgets.py      slider / colour / combo builders
ui/theme.py        dark theme
```

All sizes are percentages of the canvas, so a design looks identical at every export size.

## Promo video

Turns your screenshots into a 10-20 second MP4 using the **same look** as the stills (background, glow, phone frame, fonts, text).
Requires [FFmpeg](https://ffmpeg.org) (`sudo apt install ffmpeg`, `brew install ffmpeg` or `winget install ffmpeg`).

**In the app:** click **Promo video** in the top bar.
The preview plays live and restarts whenever you change something. Each scene is its own phone: the next phone arrives while the previous one moves away, and the screenshot inside never moves. Use the **Scene / Whole video** switch to loop only the selected scene (with its transition in) while you edit it, or play everything. The *End card* row is selectable too. While exporting you also watch every frame as it renders.
Add screenshots, set duration, motion and transition per scene, optionally give a scene its own title and subtitle,
fill in the end card (app name, tagline, button), then scrub or press Play to preview and **Export MP4**.
Click *Refresh look from the main window* after changing the design. The project is saved in `screenshots/presets/promo_project.json`.

**From the command line:**

```bash
python promo.py --name "My App" --tagline "Everything in one place"
python promo.py --screens screenshots/raw --template screenshots/presets/01.json --music song.mp3 --fps 60
python promo.py --scenes scenes.example.json      # full control per scene
```

| Motions (combine with `+`) | Transitions |
| --- | --- |
| `push_in` `pull_out` `camera_push` `float` `rise` `tilt` `parallax` `tap` | `slide_left` `slide_right` `slide_up` `drop` `spring` `swap_depth` `zoom_through` `fade` `cut` |

Optional end-card assets in `assets/video/`: `logo.png` (shown above the app name) and `play_badge.png`
(your official Google Play badge image, used instead of the built-in button).
Add your own motions in `video/animations.py` and transitions in `video/transitions.py`: each is one small function.

### Typing text and audio

- **Text animation**: *typing* writes the title and subtitle letter by letter with a blinking caret (letters never shift while typing); *fade* is the soft slide-in. Speed is in characters per second, and it automatically speeds up if a scene is too short to finish.
- **Audio tracks** (all optional), mixed like tracks in a video editor:
  - **Music**: loops to fit, volume, fade in/out.
  - **Typing sound**: a short clip that loops *only while text is being typed* and fades out the moment typing stops.
  - **Transition sound**: one hit at every scene change.
  - **Lower the music while typing**: automatic ducking.
- The preview plays the exact same mix as the export, in sync with the video, and follows Play, Pause, scrubbing and the Scene / Whole video loop. This needs `pip install pygame`; without it the preview is silent but the exported MP4 still contains the audio.
- From the command line: `python promo.py --music song.mp3 --typing-sound typing.wav --whoosh whoosh.wav --effect typing`

### Per-scene control

Select a scene in the list and use the inspector under it. Everything is saved per scene:

- **Timing and motion**: duration, motion, transition and its length.
- **Text**: effect (`default` follows the project, or `typing`, `fade`, `static`, `off`), its own title/subtitle, typing speed, start delay, and a custom style (alignment, sizes, position, colours). Choosing an effect on a scene always plays it, even when the text matches the previous scene, so you decide exactly which scenes type. *Use this effect for all scenes* applies it everywhere.
- **Phone**: size, position and rotation for this scene only.
- **Screenshot crop**: fit, zoom and pan per scene.
- **Tap indicator** position, and whether this scene plays the **typing sound** and the **transition sound**.

In `scenes.json` the same options are fields of each scene (`text_effect`, `type_speed`, `text_delay`, `text_style`, `phone_scale`, `phone_dx`, `phone_dy`, `phone_rot`, `shot`, `typing_sound`, `transition_sound`, `transition_duration`).
