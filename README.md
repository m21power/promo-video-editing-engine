# Shot Studio

Shot Studio is a local desktop app for turning emulator screenshots into polished Google Play-style promo graphics. It also includes a promo-video generator that uses the same design system for still images and motion scenes.

This project is designed to run on your own computer without any cloud service.

## What this project does

- Loads screenshots from a project folder
- Lets you style the background, text, phone frame, shadows, and crop
- Exports high-resolution PNG promo images
- Can apply one visual style to many screenshots at once
- Builds a promo MP4 video from multiple scenes

## Requirements

Before you start, make sure you have:

- Python 3.9 or newer
- Git
- FFmpeg for the video export feature
- A local screenshot folder such as `screenshots/raw`

### Install Python

Download Python from: https://www.python.org/downloads/

Make sure the box “Add Python to PATH” is checked during install on Windows.

### Install FFmpeg

FFmpeg is required for video export.

- Windows: `winget install Gyan.Dev.FFmpeg` or install from https://www.ffmpeg.org/download.html
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

After installation, confirm it works:

```bash
ffmpeg -version
```

## Clone the project

Open a terminal and run:

```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd <your-repo-name>
```

If you already have the folder locally, just open it in your terminal.

## Set up a virtual environment

It is recommended to use a virtual environment so dependencies stay isolated.

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The project requirements are very small:

- Pillow
- pygame (used for promo-video preview/audio)

If you are on Linux and Tkinter is missing, install it separately:

```bash
sudo apt install python3-tk
```

## Run the app

From the project folder:

```bash
python main.py
```

This uses the default project folder in the repo:

- `screenshots/raw` for source screenshots
- `screenshots/output` for exported PNGs
- `screenshots/presets` for saved design presets

You can also point it to another project folder:

```bash
python main.py --project "C:/path/to/your/project"
```

## Project structure

```text
shot-studio/
├── main.py                 # app entry point
├── promo.py                # command-line promo video generator
├── config.py               # project settings and defaults
├── requirements.txt        # Python dependencies
├── README.md               # setup and usage guide
├── assets/
│   ├── backgrounds/
│   ├── fonts/
│   ├── frames/
│   └── video/
├── core/
│   ├── fonts.py
│   ├── frames.py
│   ├── renderer.py
│   └── settings.py
├── ui/
│   ├── app.py
│   ├── theme.py
│   ├── video_dialog.py
│   └── widgets.py
├── video/
│   ├── animations.py
│   ├── audio.py
│   ├── engine.py
│   ├── fx.py
│   ├── picker.py
│   ├── premium.py
│   ├── timeline.py
│   └── transitions.py
├── screenshots/
│   ├── raw/
│   ├── output/
│   └── presets/
└── ...
```

## How to use the app

1. Put your emulator screenshots into `screenshots/raw/`
2. Open the app with `python main.py`
3. Pick a screenshot from the left panel
4. Adjust the text, phone frame, colours, and layout
5. Export the PNG or apply the same design to multiple images
6. If needed, build a promo video with the video editor tools

The preview updates live while you edit, so you can see the final composition immediately.

## Exporting images

The app will export PNG files into:

```text
screenshots/output/
```

The exported file name usually follows this pattern:

```text
<image-name>_playstore.png
```

## Creating a promo video

You can generate a video either from the app UI or using the command line.

### From the app

Open the app and click the “Promo video” option from the top menu.

### From the command line

```bash
python promo.py --name "My App" --tagline "Everything in one place"
python promo.py --screens screenshots/raw --template screenshots/presets/01.json --music song.mp3 --fps 60
python promo.py --scenes scenes.example.json
```

This uses the same design style and can generate MP4 output from your screenshots.

## Tips for a new user

- Put only the screenshots you want to use in `screenshots/raw/`
- Start with one screenshot and adjust the layout before exporting everything
- Use a project folder containing only the screenshots for a single app
- If you want custom fonts or frames, place them in `assets/fonts/` and `assets/frames/`
- If you want custom backgrounds, place images in `assets/backgrounds/`

## Common issues

### `ModuleNotFoundError: No module named 'PIL'`

Run:

```bash
pip install -r requirements.txt
```

### `Tkinter` errors on Linux

Install the package:

```bash
sudo apt install python3-tk
```

### `ffmpeg: command not found`

Install FFmpeg from your OS package manager or official installer and make sure it is on your PATH.

### App opens but no screenshots appear

Check that your project folder contains `screenshots/raw/` and that the screenshot files are real image files like `.png`, `.jpg`, or `.webp`.

## License

This project is intended for local, offline use and can be customized to fit your own design workflow.

## Quick start summary

```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd <your-repo-name>
python3 -m venv .venv
source .venv/bin/activate    # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

If you want, this project can also be extended with your own device frames, fonts, backgrounds, or motion presets.
