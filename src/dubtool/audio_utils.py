"""Audio helpers: ffmpeg/ffprobe wrappers, time-stretch, mixing.

Nothing calls ffmpeg at import time, so unit tests work without it.

AGENT_UPDATE_6.md: every process launch goes through dubtool/proc.py
(hidden windows, DEVNULL stdin, optional below-normal priority and
cancellation). Media *reading* is in-process via PyAV (already bundled with
faster-whisper) with ffprobe as a fallback, so stages 4–7 no longer launch a
process per probe. A MediaCache collapses repeated probes of the same
unnoticed file into one read per content version.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from dubtool import proc

log = logging.getLogger("dubtool")

OUT_SAMPLE_RATE = 44100


class FFmpegMissing(RuntimeError):
    pass


def _bundled_bin(name: str) -> str | None:
    """The copy shipped in the bundle (vendor/, AGU4 6.2) — checked first so
    the packaged exe works with the user's PATH stripped to system folders."""
    try:
        from dubtool.app.paths import vendor_dir

        cand = vendor_dir() / f"{name}.exe"
        if cand.is_file():
            return str(cand)
    except Exception:
        pass
    return None


def ffmpeg_bin() -> str:
    path = _bundled_bin("ffmpeg") or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not path:
        raise FFmpegMissing("ffmpeg not found — expected in the bundle's vendor/ or on PATH")
    return path


def ffprobe_bin() -> str | None:
    """None when unavailable: probes are in-process now, so a missing
    ffprobe only disables the fallback (AGENT_UPDATE_6.md 3.1)."""
    path = _bundled_bin("ffprobe") or shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    return path


def require_ffmpeg() -> None:
    ffmpeg_bin()


# ------------------------------------------------------------------ PyAV
def _av_open(path: Path):
    import av

    return av.open(str(path))


def media_duration_seconds(container, stream=None) -> float | None:
    """Container/stream duration in seconds — the ONE conversion point
    (AGENT_UPDATE_7.md task 3).

    PyAV reports `container.duration` in microseconds (av.time_base ==
    1000000 = AV_TIME_BASE). The old `probe_video_info` returned that raw
    number, so a 2-minute video looked like 120,000,000 seconds and always
    tripped the 30-minute warning. Stream fallback: `duration * time_base`.
    Returns None when neither level stores a duration."""
    import av

    dur = getattr(container, "duration", None)
    if dur is not None and dur > 0:
        return float(dur) / float(av.time_base)
    if stream is not None:
        d = getattr(stream, "duration", None)
        tb = getattr(stream, "time_base", None)
        if d is not None and tb:
            seconds = float(d * tb)
            if seconds > 0:
                return seconds
    return None


def probe_duration(path: Path) -> float:
    """Media duration in seconds — in-process (PyAV), ffprobe fallback."""
    try:
        with _av_open(path) as container:
            seconds = media_duration_seconds(container)
            if seconds is not None:
                return seconds
    except Exception as exc:
        log.debug("av.open failed on %s (%s) — falling back to ffprobe", path, exc)
    return _ffprobe_duration(path)


def _ffprobe_duration(path: Path) -> float:
    args = [
        ffprobe_bin() or "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path),
    ]
    result = proc.run(args)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe could not read {path.name}: "
            f"{(result.stderr or '').strip().splitlines()[-1:]}"
        )
    try:
        return float(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"ffprobe returned no duration for {path.name}") from exc


def probe_audio_streams(path: Path) -> int:
    try:
        with _av_open(path) as container:
            return sum(1 for s in container.streams if s.type == "audio")
    except Exception:
        args = [
            ffprobe_bin() or "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "default=nw=1", str(path),
        ]
        result = proc.run(args, check=True)
        return len([line for line in result.stdout.strip().splitlines() if line.strip()])


def probe_video_info(path: Path) -> dict:
    """First video stream: codec, resolution, frame rate, duration."""
    try:
        with _av_open(path) as container:
            vstream = next((s for s in container.streams if s.type == "video"), None)
            if vstream is None:
                raise RuntimeError(f"no video stream in {path.name}")
            rate = vstream.average_rate or getattr(vstream, "guessed_rate", None)
            num = getattr(rate, "numerator", rate) if rate is not None else 0
            den = getattr(rate, "denominator", 1) if rate is not None else 1
            # AGENT_UPDATE_7.md task 3: seconds, from the one helper
            # (container microseconds -> av.time_base; stream fallback).
            seconds = media_duration_seconds(container, vstream)
            if seconds is None:
                raise RuntimeError(f"no duration in {path.name}")
            return {
                "codec_name": vstream.codec_context.name if vstream.codec_context else "",
                "width": int(vstream.width or 0),
                "height": int(vstream.height or 0),
                "r_frame_rate": f"{num}/{den or 1}",
                "duration": float(seconds),
            }
    except Exception:
        args = [
            ffprobe_bin() or "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,r_frame_rate:format=duration",
            "-of", "json", str(path),
        ]
        result = proc.run(args)
        if result.returncode != 0:
            raise RuntimeError(f"probe failed on {path.name}") from None
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or [{}]
        info = dict(streams[0])
        # ffprobe format duration is already seconds; float() so every code
        # path of probe_video_info returns a number (AGENT_UPDATE_7.md task 3).
        raw = (data.get("format") or {}).get("duration")
        if raw is None:
            raise RuntimeError(f"no duration in {path.name}")
        info["duration"] = float(raw)
        return info


def probe_audio_format(path: Path) -> tuple[int, int]:
    """(sample_rate, channels) of the first audio stream."""
    try:
        with _av_open(path) as container:
            astream = next((s for s in container.streams if s.type == "audio"), None)
            if astream is not None:
                return int(astream.rate), int(astream.channels or 1)
    except Exception:
        pass
    args = [
        ffprobe_bin() or "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels", "-of", "default=nw=1:nk=1",
        str(path),
    ]
    result = proc.run(args)
    values = [line for line in result.stdout.strip().splitlines() if line]
    try:
        rate = int(values[0])
        channels = int(values[1]) if len(values) > 1 else 1
    except (IndexError, ValueError):
        rate, channels = OUT_SAMPLE_RATE, 2
    return rate, channels


class MediaCache:
    """One read per content version across stages (AGU6 3.2).

    Keyed by relative path, size and mtime (ns) — never an absolute path, so
    the durations.json stays stable when the job dir moves. Memory cache
    mirrors `durations.json` in the job directory. A cached value is never
    trusted when size or mtime changed.
    """

    def __init__(self, job_dir: Path | None = None):
        self.path = (job_dir / "durations.json") if job_dir else None
        self._mem: dict[str, float] = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._mem = {
                        str(k): float(v) for k, v in data.items()
                        if isinstance(v, (int, float))
                    }
            except Exception:
                self._mem = {}

    @staticmethod
    def _key(path: Path, root: Path | None) -> str:
        st = path.stat()
        rel = path.name if root is None else (
            str(path.relative_to(root)) if str(path).startswith(str(root)) else path.name
        )
        return f"{rel}|{st.st_size}|{st.st_mtime_ns}"

    def duration(self, path: Path, root: Path | None = None) -> float:
        """Duration of `path`, cached. Raises on failure (as probe_duration)."""
        path = Path(path)
        key = self._key(path, root)
        if key in self._mem:
            return self._mem[key]
        duration = probe_duration(path)
        self._mem[key] = duration
        self.save()
        return duration

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.write_text(
                json.dumps(self._mem, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass


def format_mmss(seconds: float) -> str:
    """mm:ss, or h:mm:ss from one hour (1:02:03) — shared by the video info
    line (AGENT_UPDATE_7.md task 3) and the readable translation doc."""
    total = max(0, int(round(float(seconds))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def is_valid_media(path: Path) -> bool:
    try:
        probe_duration(path)
        return True
    except RuntimeError:
        return False


# -------------------------------------------------------- ffmpeg process wrappers
def run_ffmpeg(args: list[str], *, cancel=None, low_priority: bool = False) -> None:
    cmd = [ffmpeg_bin(), "-y", *args]
    result = proc.run(cmd, cancel=cancel, low_priority=low_priority)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError("ffmpeg failed: " + " | ".join(tail))


def extract_audio(src: Path, dst: Path, *, cancel=None) -> None:
    """Extract mono 16 kHz WAV (AGENT.md stage 1 command)."""
    run_ffmpeg(
        ["-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)],
        cancel=cancel,
    )


def convert_to_wav(src: Path, dst: Path, sample_rate: int = OUT_SAMPLE_RATE,
                   *, cancel=None) -> None:
    run_ffmpeg(
        ["-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), str(dst)],
        cancel=cancel,
    )


def atempo_stretch(src: Path, dst: Path, ratio: float,
                   sample_rate: int = OUT_SAMPLE_RATE, *, cancel=None) -> None:
    """Speed up audio by `ratio` (duration shrinks), pitch preserved.

    ffmpeg atempo supports 0.5..100 via chaining; for ratio > 2 chain filters.
    """
    if ratio <= 0:
        raise ValueError(f"ratio must be positive, got {ratio}")
    filters: list[str] = []
    remaining = ratio
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    filters.append(f"atempo={min(remaining, 2.0):.6f}")
    run_ffmpeg(
        ["-i", str(src), "-filter:a", ",".join(filters), "-vn", "-ac", "1",
         "-ar", str(sample_rate), str(dst)],
        cancel=cancel, low_priority=True,
    )


def trim_silence(
    src: Path, dst: Path, threshold_db: int = -35, margin_s: float = 0.05,
    *, cancel=None,
) -> None:
    """Trim leading/trailing silence, keeping up to `margin_s` at each end.

    Uses ffmpeg silenceremove twice (reverse trick) so word onsets and
    endings are never cut (AGENT_UPDATE_3.md task 2).
    """
    keep = f"{max(float(margin_s), 0.0):.3f}"
    lead = (
        f"silenceremove=start_periods=1:start_threshold={int(threshold_db)}dB"
        f":start_silence={keep}"
    )
    run_ffmpeg(
        ["-i", str(src), "-af", ",".join([lead, "areverse", lead, "areverse"]), str(dst)],
        cancel=cancel, low_priority=True,
    )


def make_silence(
    duration_s: float, dst: Path, sample_rate: int = OUT_SAMPLE_RATE,
    channels: int = 1,
) -> None:
    channel_layout = "mono" if channels == 1 else f"{channels}c"
    run_ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={channel_layout}",
        "-t", f"{max(duration_s, 0.0):.3f}",
        str(dst),
    ])


def mix_overlays(base_wav: Path, clips: list[tuple[Path, float]], out_wav: Path) -> None:
    """Overlay `clips` [(path, offset_seconds)] onto `base_wav` -> `out_wav`.

    Uses pydub (bundled with ffmpeg for other formats). All inputs are mono
    WAV and get normalized to the shared sample rate before mixing.
    """
    from pydub import AudioSegment

    base = AudioSegment.from_wav(str(base_wav))
    for clip_path, offset in clips:
        seg = AudioSegment.from_wav(str(clip_path))
        seg = seg.set_frame_rate(base.frame_rate).set_channels(base.channels)
        base = base.overlay(seg, position=int(offset * 1000))
    base.export(str(out_wav), format="wav")


def mux(video_in: Path, audio_in: Path, out: Path, *, cancel=None) -> None:
    """Replace video audio, never re-encoding video (AGENT.md stage 6)."""
    run_ffmpeg([
        "-i", str(video_in), "-i", str(audio_in),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(out),
    ], cancel=cancel, low_priority=True)


# ------------------------------------------------------------------ mixing
# AGENT_UPDATE_2.md: the Vietnamese voice plays on top of the lowered
# original audio. The mixer takes a "background audio path" so a later
# phase can swap the original full mix for a Demucs background track with
# a one-line change in s6_mix.py.


def has_audio_stream(path: Path) -> bool:
    try:
        return probe_audio_streams(path) > 0
    except Exception:
        return False


def alimiter_limit() -> str:
    """Limiter ceiling as a filter param (leave ~0.3 dB headroom)."""
    return "0.97"


def build_mix_graph(
    mode: str,
    original_gain_db: float,
    dub_gain_db: float,
    duck: dict | None = None,
) -> str:
    """Pure: build the filter_complex graph text for the mix.

    amix normalize=0 keeps the input levels (no automatic halving).
    alimiter at the end prevents clipping in the final mix.
    """
    if mode not in ("static", "duck"):
        raise ValueError(f"unknown mix mode: {mode}")
    duck = duck or {}
    lines = [
        f"[0:a]volume={original_gain_db}dB[o0]",
        f"[1:a]volume={dub_gain_db}dB[d0]",
    ]
    if mode == "duck":
        lines.append(
            "[d0]asplit=2[d1][d2]"
        )
        lines.append(
            f"[o0][d2]sidechaincompress="
            f"threshold={duck.get('threshold', 0.03)}:"
            f"ratio={duck.get('ratio', 8)}:"
            f"attack={duck.get('attack_ms', 20) / 1000:.3f}:"
            f"release={duck.get('release_ms', 400)}"
            f"[oc]"
        )
        lines.append("[oc][d1]amix=inputs=2:normalize=0[m]")
    else:
        lines.append("[o0][d0]amix=inputs=2:normalize=0[m]")
    lines.append(f"[m]alimiter=limit={alimiter_limit()}[out]")
    return ";".join(lines)


def mix_original_with_dub(
    working_dir: Path,
    background: Path,
    dub: Path,
    out: Path,
    graph: str,
    *, cancel=None, threads: int = 0,
) -> None:
    """Run the final mix: video copied, original audio lowered, dub on top.

    The filter graph is written to a script file and FFmpeg runs with
    `working_dir` as the working directory, so all inputs are relative
    names (Windows command-line/escaping friendly).
    """
    script_path = working_dir / "mix_script.txt"
    script_path.write_text(graph, encoding="utf-8")
    cmd = [
        ffmpeg_bin(), "-y",
        "-i", background.name,
        "-i", dub.name,
        "-filter_complex_script", "mix_script.txt",
        "-map", "0:v", "-c:v", "copy",
        "-map", "[out]", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        out.name,
    ]
    if threads > 0:
        cmd[-1:-1] = ["-threads", str(threads)]
    result = proc.run(
        cmd, cwd=working_dir, cancel=cancel, low_priority=True
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError("mix failed: " + " | ".join(tail))
    script_path.unlink(missing_ok=True)
