"""Video clips that play inside the phone: probing, trim / crop / speed / colour, and fast frame access.

How it works
------------
FFmpeg does the heavy lifting once per unique set of edits: it trims, crops, recolours and re-times the
source, scales it to the size the phone screen needs and writes the frames as small JPEGs into a temp
folder. The renderer then simply asks `clip.frame_at(seconds)`.

* Live preview: extraction runs in the background. Frames that are not ready yet fall back to the newest
  one that is (or the poster), so the UI never blocks.
* Export: `clip.wait()` blocks until every frame is on disk, so the result is exact.
* Clips are cached by content (file, edits, size, fps), so dragging unrelated sliders never re-decodes.

A scene stores its edits in `scene.clip` (any missing key uses DEFAULTS):
    trim_in / trim_out  seconds in the source (trim_out 0 = to the end)
    speed               0.1 .. 16  (below 1 = slow motion, above 1 = faster)
    fit                 True: the scene length drives the speed (see ui/video_dialog.py), so a longer scene plays slower
    volume / mute       the clip's own sound
    loop                repeat the clip when the scene is longer than it
    crop                [x, y, w, h] as fractions of the source frame
    brightness (-50..50), contrast (50..150), saturation (0..200)   colour tweaks, 0 / 100 / 100 = neutral
"""
import atexit
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

DEFAULTS = dict(trim_in=0.0, trim_out=0.0, speed=1.0, fit=False, volume=100, mute=False, loop=False,
                crop=[0.0, 0.0, 1.0, 1.0], brightness=0, contrast=100, saturation=100)
# only these change the extracted pixels; volume / mute / loop are applied later, so they never re-decode
EXTRACT_KEYS = ("trim_in", "trim_out", "speed", "crop", "brightness", "contrast", "saturation")


@dataclass
class Info:
    duration: float
    width: int      # as displayed (rotation already applied)
    height: int
    fps: float
    has_audio: bool


# ------------------------------------------------------------------ probing
_probe_cache = {}


def probe(ffmpeg, path):
    """Duration, size, fps and audio presence, read from `ffmpeg -i` (works even without ffprobe)."""
    p = Path(path)
    key = (str(p), p.stat().st_mtime_ns)
    if key in _probe_cache:
        return _probe_cache[key]
    txt = subprocess.run([ffmpeg, "-hide_banner", "-i", str(p)], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    v = re.search(r",\s*(\d{2,5})x(\d{2,5})(?=[\s,\[])", txt[txt.find("Video:"):] if "Video:" in txt else "")
    if not m or not v:
        raise RuntimeError(f"Cannot read this video: {p.name}")
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    w, h = int(v.group(1)), int(v.group(2))
    rot = re.search(r"rotat\w*(?:\s+of|\s*:)\s*(-?\d+)", txt)
    if rot and abs(int(rot.group(1))) % 180 == 90:  # phone videos are often stored sideways with a rotation flag
        w, h = h, w
    f = re.search(r"(\d+(?:\.\d+)?)\s*fps", txt) or re.search(r"(\d+(?:\.\d+)?)\s*tbr", txt)
    info = Info(dur, w, h, float(f.group(1)) if f else 30.0, bool(re.search(r"Stream #\S+.*Audio:", txt)))
    _probe_cache[key] = info
    return info


# ------------------------------------------------------------------ options
def norm_crop(c):
    try:
        x, y, w, h = (float(v) for v in c)
    except (TypeError, ValueError):
        return [0.0, 0.0, 1.0, 1.0]
    w, h = min(max(w, 0.05), 1.0), min(max(h, 0.05), 1.0)
    return [min(max(x, 0.0), 1.0 - w), min(max(y, 0.0), 1.0 - h), w, h]


def opts(d):
    """Scene clip settings merged with the defaults and clamped to sane ranges."""
    o = {**DEFAULTS, **(d or {})}
    o["crop"] = norm_crop(o["crop"])
    o["speed"] = min(16.0, max(0.1, float(o["speed"])))
    return o


def span(info, o):
    """(source start, source length) in seconds after trimming."""
    tin = max(0.0, min(float(o["trim_in"]), info.duration - 0.1))
    tout = float(o["trim_out"])
    tout = info.duration if tout <= 0 else min(tout, info.duration)
    return tin, max(0.1, tout - tin)


def play_len(info, o):
    """How long the trimmed clip plays on the timeline (after speed)."""
    return span(info, o)[1] / o["speed"]


def init_scene(ffmpeg, scene, path):
    """Make a fresh video scene as long as its clip."""
    info = probe(ffmpeg, path)
    scene.clip = {"fit": True}  # a new video scene stretches: change its duration and the video slows down / speeds up
    scene.duration = round(play_len(info, opts({})), 2)
    return info


# ------------------------------------------------------------- single frames
def grab(ffmpeg, path, t=0.0, max_w=None):
    """One frame at `t` seconds as an RGB image (fast seek)."""
    def run(at):
        cmd = [ffmpeg, "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(path), "-frames:v", "1"]
        if max_w:
            cmd += ["-vf", f"scale='min(iw,{int(max_w)})':-2"]
        return subprocess.run(cmd + ["-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "2", "-"], capture_output=True)

    r = run(t)
    if not r.stdout and t > 0:  # asked past the last frame
        r = run(max(0.0, t - 0.5))
    if not r.stdout:
        r = run(0.0)
    if not r.stdout:
        raise RuntimeError("Could not read a frame from " + Path(path).name)
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def poster(ffmpeg, path, max_w=None):
    """A representative frame, used wherever a video needs a still picture (lists, PNG export)."""
    info = probe(ffmpeg, path)
    return grab(ffmpeg, path, min(1.0, info.duration / 2), max_w)


# ------------------------------------------------------------ frame sequences
class Clip:
    """The trimmed / cropped / recoloured clip extracted to JPEG frames. Use `get_clip`, not this directly."""

    def __init__(self, ffmpeg, path, o, info, width, fps):
        self.fps, self.o, self.info = fps, o, info
        tin, sp = span(info, o)
        self.n = max(1, int(round(sp / o["speed"] * fps)))
        self.dir = Path(tempfile.mkdtemp(prefix="shotclip_"))
        self.last_used = time.time()
        self._lru, self._lock, self._err = OrderedDict(), threading.Lock(), tempfile.TemporaryFile()

        cx, cy, cw, ch = o["crop"]
        crop_w, crop_h = max(2, int(info.width * cw) // 2 * 2), max(2, int(info.height * ch) // 2 * 2)
        vf = []
        if (cx, cy, cw, ch) != (0.0, 0.0, 1.0, 1.0):
            vf.append(f"crop={crop_w}:{crop_h}:{int(info.width * cx) // 2 * 2}:{int(info.height * cy) // 2 * 2}")
        if (o["brightness"], o["contrast"], o["saturation"]) != (0, 100, 100):
            vf.append(f"eq=brightness={o['brightness'] / 100:.3f}:contrast={o['contrast'] / 100:.3f}:saturation={o['saturation'] / 100:.3f}")
        vf += [f"setpts=(PTS-STARTPTS)/{o['speed']:.4f}", f"fps={fps}"]
        if crop_w > width:
            vf.append(f"scale={int(width)}:-2:flags=lanczos")
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", f"{tin:.3f}", "-t", f"{sp:.3f}", "-i", str(path), "-an",
               "-vf", ",".join(vf), "-q:v", "3", "-start_number", "1", str(self.dir / "%05d.jpg")]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=self._err)

    # -- state
    @property
    def failed(self):
        return self.proc.poll() not in (None, 0)

    @property
    def done(self):
        return self.proc.poll() == 0

    def error_text(self):
        """What FFmpeg complained about (only meaningful once the process has failed)."""
        try:
            self._err.seek(0)
            return self._err.read().decode("utf-8", "replace")[-300:].strip()
        except Exception:
            return ""

    def wait(self, cancel=None):
        """Block until all frames are written. Returns False if `cancel()` became true."""
        while self.proc.poll() is None:
            if cancel and cancel():
                return False
            time.sleep(0.04)
        if self.proc.returncode != 0:
            self._err.seek(0)
            raise RuntimeError("FFmpeg could not decode the video:\n" + self._err.read().decode("utf-8", "replace")[-800:])
        return True

    # -- frames
    def _load(self, j):
        with self._lock:
            if j in self._lru:
                self._lru.move_to_end(j)
                return self._lru[j]
        try:
            with Image.open(self.dir / f"{j + 1:05d}.jpg") as im:
                img = im.convert("RGB")
        except Exception:  # not written yet, or half written
            return None
        with self._lock:
            self._lru[j] = img
            while len(self._lru) > 10:
                self._lru.popitem(last=False)
        return img

    def frame_at(self, lt, loop=False):
        """(index, image) of the frame to show `lt` seconds into the scene. (-1, None) if nothing is ready yet.
        When the clip is shorter than the scene it holds the last frame, or repeats if `loop`."""
        self.last_used = time.time()
        i = int(max(0.0, lt) * self.fps)
        i = i % self.n if loop else min(i, self.n - 1)
        for j in range(i, max(-1, i - 40), -1):  # extraction may still be catching up: use the newest ready frame
            img = self._load(j)
            if img is not None:
                return j, img
        return -1, None

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self._err.close()
        shutil.rmtree(self.dir, ignore_errors=True)


_clips = OrderedDict()
_lock = threading.Lock()
MAX_CLIPS = 14


def get_clip(ffmpeg, path, o, info, width, fps):
    """Shared, cached `Clip` for these exact edits. Re-uses running or finished extractions."""
    p = Path(path)
    key = hashlib.md5(json.dumps([str(p), p.stat().st_mtime_ns, {k: o[k] for k in EXTRACT_KEYS}, int(width), int(fps)],
                                 sort_keys=True).encode()).hexdigest()
    with _lock:
        c = _clips.get(key)
        if c is None or c.failed:
            if c is not None:
                c.close()
            c = _clips[key] = Clip(ffmpeg, p, o, info, width, fps)
        _clips.move_to_end(key)
        for k in list(_clips):  # evict the longest unused ones (never anything used in the last few seconds)
            if len(_clips) <= MAX_CLIPS:
                break
            if time.time() - _clips[k].last_used > 5 and k != key:
                _clips.pop(k).close()
    return c


def clear_cache():
    with _lock:
        for c in _clips.values():
            c.close()
        _clips.clear()


atexit.register(clear_cache)