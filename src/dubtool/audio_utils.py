"""Audio helpers: ffmpeg/ffprobe wrappers, time-stretch, mixing.

Nothing calls ffmpeg at import time, so unit tests work without it.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("dubtool")

OUT_SAMPLE_RATE = 44100


class FFmpegMissing(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not path:
        raise FFmpegMissing("ffmpeg not found on PATH — install FFmpeg and retry")
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if not path:
        raise FFmpegMissing("ffprobe not found on PATH — install FFmpeg and retry")
    return path


def require_ffmpeg() -> None:
    ffmpeg_bin()
    ffprobe_bin()


def probe_duration(path: Path) -> float:
    """Media duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "a",
            "-show_entries", "format=duration", "-of", "default=nk=1:nw=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
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
    result = subprocess.run(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "default=nw=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return len([line for line in result.stdout.strip().splitlines() if line.strip()])


def probe_video_info(path: Path) -> dict:
    """First video stream: codec, resolution, frame rate, duration."""
    result = subprocess.run(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate:format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}")
    import json as _json

    data = _json.loads(result.stdout or "{}")
    streams = data.get("streams") or [{}]
    fmt = data.get("format", {})
    info = dict(streams[0])
    info["duration"] = fmt.get("duration")
    return info


def probe_audio_format(path: Path) -> tuple[int, int]:
    """(sample_rate, channels) of the first audio stream."""
    result = subprocess.run(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    values = [line for line in result.stdout.strip().splitlines() if line]
    try:
        rate = int(values[0])
        channels = int(values[1]) if len(values) > 1 else 1
    except (IndexError, ValueError):
        rate, channels = OUT_SAMPLE_RATE, 2
    return rate, channels


def is_valid_media(path: Path) -> bool:
    try:
        probe_duration(path)
        return True
    except RuntimeError:
        return False


def run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_bin(), "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError("ffmpeg failed: " + " | ".join(tail))


def extract_audio(src: Path, dst: Path) -> None:
    """Extract mono 16 kHz WAV (AGENT.md stage 1 command)."""
    run_ffmpeg(["-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)])


def convert_to_wav(src: Path, dst: Path, sample_rate: int = OUT_SAMPLE_RATE) -> None:
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), str(dst)])


def atempo_stretch(src: Path, dst: Path, ratio: float, sample_rate: int = OUT_SAMPLE_RATE) -> None:
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
    run_ffmpeg([
        "-i", str(src),
        "-filter:a", ",".join(filters),
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        str(dst),
    ])


def trim_silence(
    src: Path, dst: Path, threshold_db: int = -35, margin_s: float = 0.05
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
    run_ffmpeg([
        "-i", str(src),
        "-af", ",".join([lead, "areverse", lead, "areverse"]),
        str(dst),
    ])


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


def mux(video_in: Path, audio_in: Path, out: Path) -> None:
    """Replace video audio, never re-encoding video (AGENT.md stage 6)."""
    run_ffmpeg([
        "-i", str(video_in), "-i", str(audio_in),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(out),
    ])


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
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(working_dir))
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError("mix failed: " + " | ".join(tail))
    script_path.unlink(missing_ok=True)
