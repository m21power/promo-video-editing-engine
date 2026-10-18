"""Audio mix, built like tracks in a video editor. The same FFmpeg graph is used for the live
preview (rendered to PCM and played) and for the final MP4, so what you hear is what you export.

Tracks: music (looped, volume, fades), typing sound (looped only while text is being typed),
transition sound (one hit per scene change). Music is ducked under the typing sound."""
import os
import subprocess
from pathlib import Path

FMT = "aformat=sample_rates=44100:channel_layouts=stereo"


def _ok(path):
    return str(path) if path and Path(path).is_file() else None


def has_audio(project):
    return any(_ok(p) for p in (project.music, project.typing_sound, project.whoosh_sound))


def build_graph(project, total, typing_iv, whoosh_t, first=0):
    """Returns (input args, filter_complex) or None. `first` = index of the first audio input."""
    args, parts, buses, n = [], [], [], first
    music = _ok(project.music)
    typing = _ok(project.typing_sound) if typing_iv else None
    whoosh = _ok(project.whoosh_sound) if whoosh_t else None

    if music:
        args += ["-stream_loop", "-1", "-i", music]
        chain = f"[{n}:a]{FMT},atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={project.music_volume / 100:.3f}"
        if project.music_fade_in > 0:
            chain += f",afade=t=in:st=0:d={project.music_fade_in:.2f}"
        if project.music_fade_out > 0:
            chain += f",afade=t=out:st={max(0.0, total - project.music_fade_out):.3f}:d={project.music_fade_out:.2f}"
        parts.append(chain + "[music]")
        n += 1

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

    labels = []
    if music:
        if project.duck and typing:  # music dips while the typing sound is active
            parts.append("[typing]asplit=2[typing_mix][typing_raw];[typing_raw]apad[typing_key]")  # pad so the music never gets cut short
            parts.append("[music][typing_key]sidechaincompress=threshold=0.01:ratio=8:attack=30:release=600[music_d]")
            labels += ["music_d", "typing_mix"]
        else:
            labels += ["music"] + (["typing"] if typing else [])
    elif typing:
        labels.append("typing")
    if whoosh:
        labels.append("whoosh")
    if not labels:
        return None
    ins = "".join(f"[{l}]" for l in labels)
    mix = f"{ins}amix=inputs={len(labels)}:normalize=0:duration=longest" if len(labels) > 1 else f"{ins}anull"
    parts.append(f"{mix},alimiter=limit=0.97,atrim=0:{total:.3f}[aout]")
    return args, ";".join(parts)


def render_pcm(ffmpeg, project, total, typing_iv, whoosh_t):
    """The whole mix as raw 16-bit stereo 44.1 kHz PCM (for the live preview), or None if there is no audio."""
    g = build_graph(project, total, typing_iv, whoosh_t, first=0)
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
