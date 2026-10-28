"""Audio mix, built like tracks in a video editor. The same FFmpeg graph is used for the live
preview (rendered to PCM and played) and for the final MP4, so what you hear is what you export.

Tracks: any number of background music tracks, each placed on the timeline (start, end, where in the song to
begin, volume, fades, repeat) and crossfaded with equal-power curves where they overlap; the sound of video clips (trimmed, speed-matched, volume),
typing sound (looped only while text is being typed), transition sound (one hit per scene change).
Music is ducked under the typing sound and under video speech."""
import os
import re
import subprocess
from pathlib import Path

FMT = "aformat=sample_rates=44100:channel_layouts=stereo"


def _ok(path):
    return str(path) if path and Path(path).is_file() else None


TRACK_DEFAULTS = dict(path="", start=0.0, end=0.0, offset=0.0, src_end=0.0, volume=100, fade_in=0.0, fade_out=0.0, loop=False, len=0.0)
_len_cache = {}


def audio_len(ffmpeg, path):
    """Length of an audio file in seconds (0.0 if it cannot be read). Read from `ffmpeg -i`, so no ffprobe is needed."""
    try:
        key = (str(path), Path(path).stat().st_mtime_ns)
    except OSError:
        return 0.0
    if key not in _len_cache:
        txt = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                             errors="replace").stderr
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
        _len_cache[key] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    return _len_cache[key]


def usable_len(t):
    """Seconds of the song that are used: from `offset` to `src_end` (0 = the end of the song). 0.0 if unknown."""
    t = {**TRACK_DEFAULTS, **t}
    top = float(t["src_end"]) if float(t["src_end"]) > 0 else float(t["len"])
    return max(0.0, top - max(0.0, float(t["offset"]))) if top > 0 else 0.0


def track_end(t, total):
    """Where a track stops on the timeline: its end (0 = the end of the video), or earlier when the song runs out
    and is not set to repeat."""
    t = {**TRACK_DEFAULTS, **t}
    start = max(0.0, float(t["start"]))
    end = total if float(t["end"]) <= 0 else min(float(t["end"]), total)
    if not t["loop"] and usable_len(t) > 0:
        end = min(end, start + max(0.1, usable_len(t)))
    return end


def music_tracks(project, total):
    """The background music tracks that will really play: [{path, start, span, off, loop, vol, fi, fo}].
    An old project with a single `music` file counts as one track that repeats over the whole video."""
    src = list(getattr(project, "tracks", None) or [])
    if not src and _ok(project.music):
        src = [dict(path=project.music, loop=True)]
    out = []
    for t in src:
        t = {**TRACK_DEFAULTS, **t}
        path = _ok(t["path"])
        if not path:
            continue
        start = max(0.0, float(t["start"]))
        span = track_end(t, total) - start
        if span < 0.1:
            continue
        off = max(0.0, float(t["offset"]))
        cut = float(t["src_end"]) if float(t["src_end"]) > off + 0.1 else 0.0   # 0 = no crop at the end of the song
        out.append(dict(path=path, start=start, span=span, off=off, cut=cut, loop=bool(t["loop"]),
                        vol=max(0.0, float(t["volume"])), fi=min(max(0.0, float(t["fade_in"])), span),
                        fo=min(max(0.0, float(t["fade_out"])), span)))
    return out


def has_audio(project, clips=None):
    return bool(clips) or bool(getattr(project, "tracks", None)) or any(
        _ok(p) for p in (project.music, project.typing_sound, project.whoosh_sound, getattr(project, "tap_sound", "")))


def _atempo(s):
    """atempo only accepts 0.5..2 per stage, so chain stages for other speeds."""
    parts = []
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def build_graph(project, total, typing_iv, whoosh_t, first=0, clips=None, tap_t=None):
    """Returns (input args, filter_complex) or None. `first` = index of the first audio input.
    `clips` = [{path, ss, span, speed, vol, start, dur}] from FrameRenderer.clip_audio()."""
    clips = clips or []
    args, parts, buses, n = [], [], [], first
    mtracks = music_tracks(project, total)
    music = bool(mtracks)
    typing = _ok(project.typing_sound) if typing_iv else None
    whoosh = _ok(project.whoosh_sound) if whoosh_t else None
    tap = _ok(getattr(project, "tap_sound", "")) if tap_t else None

    if music:
        for k, t in enumerate(mtracks):  # each track: pick the part of the song, shape it, place it on the timeline
            args += (["-stream_loop", "-1"] if (t["loop"] and not t["cut"]) else []) + ["-i", t["path"]]
            chain = f"[{n}:a]{FMT},"
            if t["cut"]:  # a cropped part of the song: use exactly that part (and repeat only that part)
                chain += f"atrim=start={t['off']:.3f}:end={t['cut']:.3f},asetpts=PTS-STARTPTS,"
                if t["loop"]:
                    chain += f"aloop=loop=-1:size={int((t['cut'] - t['off']) * 44100) + 1},asetpts=PTS-STARTPTS,"
            elif t["off"] > 0:
                chain += f"atrim=start={t['off']:.3f},asetpts=PTS-STARTPTS,"
            chain += f"atrim=0:{t['span']:.3f},asetpts=PTS-STARTPTS,volume={t['vol'] / 100:.3f}"
            if t["fi"] > 0:  # qsin = equal-power curve: two overlapping tracks keep a steady loudness through the crossfade
                chain += f",afade=t=in:st=0:d={t['fi']:.2f}:curve=qsin"
            if t["fo"] > 0:
                chain += f",afade=t=out:st={max(0.0, t['span'] - t['fo']):.3f}:d={t['fo']:.2f}:curve=qsin"
            ms = int(t["start"] * 1000)
            parts.append(chain + f",adelay={ms}|{ms}[mtrk{k}]")
            n += 1
        ins = "".join(f"[mtrk{k}]" for k in range(len(mtracks)))
        mix = f"{ins}amix=inputs={len(mtracks)}:normalize=0:duration=longest" if len(mtracks) > 1 else f"{ins}anull"
        chain = f"{mix},atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={project.music_volume / 100:.3f}"  # master
        if project.music_fade_in > 0:
            chain += f",afade=t=in:st=0:d={project.music_fade_in:.2f}"
        if project.music_fade_out > 0:
            chain += f",afade=t=out:st={max(0.0, total - project.music_fade_out):.3f}:d={project.music_fade_out:.2f}"
        parts.append(chain + "[music]")

    for k, c in enumerate(clips):  # the video's own sound: trimmed at the input, retimed, placed at its scene
        args += ["-ss", f"{c['ss']:.3f}", "-t", f"{c['span']:.3f}", "-i", c["path"]]
        ms, d = int(max(0, c["start"]) * 1000), c["dur"]
        parts.append(f"[{n}:a]{FMT},{_atempo(c['speed'])},volume={c['vol'] / 100:.3f},atrim=0:{d:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(0.0, d - 0.06):.3f}:d=0.06,adelay={ms}|{ms}[clip{k}]")
        n += 1
    if clips:
        ins = "".join(f"[clip{k}]" for k in range(len(clips)))
        parts.append(f"{ins}amix=inputs={len(clips)}:normalize=0:duration=longest[clips]" if len(clips) > 1 else "[clip0]anull[clips]")

    def hits(label, src, times, length, vol, fade_out, loop):
        """One shaped copy of the clip per time, delayed to its position, mixed into one bus."""
        nonlocal n
        args.extend((["-stream_loop", "-1"] if loop else []) + ["-i", src])
        k = len(times)
        outs = "".join(f"[{label}s{j}]" for j in range(k))
        head = f"[{n}:a]{FMT},volume={vol / 100:.3f}"
        parts.append(head + (f",asplit={k}{outs}" if k > 1 else outs))
        n += 1
        for j, (start, dur) in enumerate(zip(times, length)):
            ms = int(max(0, start) * 1000)
            parts.append(f"[{label}s{j}]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.04,"
                         f"afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.2f},adelay={ms}|{ms}[{label}h{j}]")
        ins = "".join(f"[{label}h{j}]" for j in range(k))
        parts.append(f"{ins}amix=inputs={k}:normalize=0:duration=longest[{label}]" if k > 1 else f"{ins}anull[{label}]")

    if typing:
        hits("typing", typing, [a for a, _ in typing_iv], [b - a for a, b in typing_iv], project.typing_volume, 0.15, True)
    if whoosh:
        hits("whoosh", whoosh, whoosh_t, [1.6] * len(whoosh_t), project.whoosh_volume, 0.35, False)

    if tap:  # one click per tap, placed where the tap indicator presses
        hits("tap", tap, tap_t, [1.2] * len(tap_t), getattr(project, "tap_volume", 80), 0.2, False)

    labels = []
    keys = (["typing"] if typing else []) + (["clips"] if clips else [])  # buses that push the music down
    if music:
        if project.duck and keys:  # music dips while typing / video sound is active
            for b in keys:  # pad the key so the music never gets cut short
                parts.append(f"[{b}]asplit=2[{b}_mix][{b}_raw];[{b}_raw]apad[{b}_key]")
            if len(keys) > 1:
                parts.append("".join(f"[{b}_key]" for b in keys) + f"amix=inputs={len(keys)}:normalize=0:duration=longest[key]")
            else:
                parts.append(f"[{keys[0]}_key]anull[key]")
            parts.append("[music][key]sidechaincompress=threshold=0.01:ratio=8:attack=30:release=600[music_d]")
            labels += ["music_d"] + [f"{b}_mix" for b in keys]
        else:
            labels += ["music"] + keys
    else:
        labels += keys
    if whoosh:
        labels.append("whoosh")
    if tap:
        labels.append("tap")
    if not labels:
        return None
    # A finite silent track is mixed in so the audio always spans the whole video. (Padding with `apad` after a mix of
    # several hits deadlocks FFmpeg, so it must not be used here.)
    parts.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[sil]")
    ins = "".join(f"[{l}]" for l in labels) + "[sil]"
    parts.append(f"{ins}amix=inputs={len(labels) + 1}:normalize=0:duration=longest,alimiter=limit=0.97,atrim=0:{total:.3f}[aout]")
    return args, ";".join(parts)


def render_pcm(ffmpeg, project, total, typing_iv, whoosh_t, clips=None, tap_t=None):
    """The whole mix as raw 16-bit stereo 44.1 kHz PCM (for the live preview), or None if there is no audio."""
    g = build_graph(project, total, typing_iv, whoosh_t, first=0, clips=clips, tap_t=tap_t)
    if g is None:
        return None
    cmd = [ffmpeg, "-y", "-loglevel", "error", *g[0], "-filter_complex", g[1], "-map", "[aout]",
           "-t", f"{total:.3f}", "-f", "s16le", "-ar", "44100", "-ac", "2", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-600:])
    return r.stdout


class Player:
    """Plays the preview mix from any position using pygame (optional dependency)."""
    RATE, FRAME = 44100, 4

    def __init__(self):
        self._ok, self._snd, self._pg = None, None, None

    @property
    def available(self):
        if self._ok is None:
            try:
                os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
                import pygame
                pygame.mixer.init(frequency=self.RATE, size=-16, channels=2, buffer=512)
                self._pg, self._ok = pygame, True
            except Exception:
                self._ok = False
        return self._ok

    def play(self, pcm, t):
        self.stop()
        offset = int(max(0.0, t) * self.RATE) * self.FRAME
        if self.available and offset < len(pcm):
            self._snd = self._pg.mixer.Sound(buffer=pcm[offset:])
            self._snd.play()

    def stop(self):
        if self._snd is not None:
            self._snd.stop()
            self._snd = None