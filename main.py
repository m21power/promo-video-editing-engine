"""Shot Studio: compose Google Play screenshots from raw emulator captures.

    python main.py                       # uses ./screenshots/{raw,output}
    python main.py --project ~/my-app    # uses ~/my-app/screenshots/{raw,output}
    python main.py --raw ~/Videos/shots  # any folder of screenshots and videos (remembered next time)

You can also switch folders inside the app (Choose folder…), nothing has to be moved or deleted.
"""
import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path(__file__).resolve().parent,
                    help="folder that contains screenshots/raw and screenshots/output")
    ap.add_argument("--raw", type=Path, help="folder with your raw screenshots and videos (default: the last one you used)")
    args = ap.parse_args()
    try:  # crisp text on Windows high-DPI displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    from ui.app import App
    App(args.project, args.raw).mainloop()


if __name__ == "__main__":
    main()