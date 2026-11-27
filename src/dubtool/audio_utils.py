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
import threading
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
    ffprobe only disables the fallback (AGENT_UPDATE_6.md 3.1). Since
    Update 9 the bundled ffprobe.exe is no longer shipped (it duplicated
    ffmpeg's 122 MB build); the fallback only fires if the user's system
    has one on PATH."""
    path = _bundled_bin("ffprobe") or shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    return path


def _ffprobe_or_raise(path: Path, probe: str) -> str:
    """The ffprobe binary for the fallback, or a clear RuntimeError that
    names the in-process failure (Update 9: ffprobe is not bundled, so the
    old 'program not found' crash must never reach the user)."""
    bin_path = ffprobe_bin()
    if bin_path:
        return bin_path
    raise RuntimeError(
        f"could not read {path.name} in-process ({probe}); ffprobe is not "
        "bundled and was not found on PATH — the file may be damaged or "
        "use an unsupported container"
    )


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
        _ffprobe_or_raise(path, "PyAV"), "-v", "error", "-select_streams", "a",
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
            _ffprobe_or_raise(path, "PyAV"), "-v", "error", "-select_streams", "a",
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
            _ffprobe_or_raise(path, "PyAV"), "-v", "error", "-select_streams", "v:0",
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
    if ffprobe_bin() is None:
        # No audio stream reachable in-process and no ffprobe on PATH
        # (Update 9: ffprobe is not bundled): use the standard output
        # format rather than failing the whole job over metadata.
        return OUT_SAMPLE_RATE, 2
    args = [
        ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
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
        self._lock = threading.Lock()
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
        if root is None:
            rel = path.name
        else:
            try:
                rel = str(path.resolve().relative_to(root.resolve()))
            except ValueError:
                rel = path.name
        return f"{rel}|{st.st_size}|{st.st_mtime_ns}"

    def duration(self, path: Path, root: Path | None = None) -> float:
        """Duration of `path`, cached. Raises on failure (as probe_duration)."""
        path = Path(path)
        key = self._key(path, root)
        with self._lock:
            if key in self._mem:
                return self._mem[key]
        duration = probe_duration(path)
        with self._lock:
            self._mem[key] = duration
            self._save_locked()
        return duration

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        if self.path is None:
            return
        try:
            tmp = self.path.with_name(f"~{self.path.name}.{threading.get_ident()}.tmp")
            tmp.write_text(
                json.dumps(self._mem, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)
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
def _parse_progress_seconds(line: str) -> float | None:
    """Seconds encoded in one ffmpeg `-progress pipe:1` value line
    (Update 9 task 3.3): `out_time_us=1234567`, `out_time_ms=1234.567` and
    `out_time=HH:MM:SS.micro` are all accepted; other lines return None."""
    line = line.strip()
    try:
        if line.startswith("out_time_us="):
            return int(line.split("=", 1)[1]) / 1_000_000.0
        if line.startswith("out_time_ms="):
            # ffmpeg prints milliseconds as microseconds*1000 despite the name.
            return int(line.split("=", 1)[1]) / 1_000_000.0
        if line.startswith("out_time="):
            t = line.split("=", 1)[1].strip()
            if t in ("", "N/A", "-"):
                return None
            parts = t.split(":")
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
            return seconds
    except (ValueError, IndexError):
        return None
    return None


def _emit_ffmpeg_ratio(seconds: float, total_s: float, pf: float, progress: Any, out_state: list) -> bool:
    """Call progress(ratio) once per whole percent change; True when the
    line should mark the event as throttled (not every half-second tick)."""
    ratio = 0.0 if total_s <= 0 else max(0.0, min(1.0, seconds / total_s))
    pct = int(ratio * 100)
    if pct != out_state[0]:
        out_state[0] = pct
        try:
            progress(ratio)
        except Exception:
            pass
    return pct != pf


def run_ffmpeg(args: list[str], *, cancel=None, low_priority: bool = False,
               progress=None, total_s: float | None = None, cwd=None) -> None:
    """Run ffmpeg through the hidden-window helper.

    With `progress` (and a known `total_s`) the run adds
    `-progress pipe:1 -nostats`, parses `out_time_*` values off the pipe
    without blocking the UI thread (a reader thread; the caller is the
    worker thread anyway) and calls progress([0..1]) hill every whole
    percent. The cancel path keeps the same process-tree kill as proc.run.
    """
    cmd = [ffmpeg_bin(), "-y", *args]
    if progress is None or not total_s or total_s <= 0:
        result = proc.run(cmd, cancel=cancel, low_priority=low_priority,
                          cwd=str(cwd) if cwd is not None else None)
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-5:]
            raise RuntimeError("ffmpeg failed: " + " | ".join(tail))
        return

    from queue import Empty, Queue
    from threading import Thread

    from dubtool import proc as _proc
    from dubtool.job import StageCancelled

    cmd = cmd + ["-progress", "pipe:1", "-nostats"]
    p = _proc.popen(cmd, cwd=cwd, low_priority=low_priority)
    assert p.stdout is not None and p.stderr is not None
    q: "Queue[str | None]" = Queue()
    state = [-1]           # last emitted percent

    def _reader() -> None:
        assert p.stdout is not None
        try:
            for line in p.stdout:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)

    class _StderrTail:
        tail: list[str] = []

    def _stderr_reader() -> None:
        assert p.stderr is not None
        for line in p.stderr:
            _StderrTail.tail.append(line)
            if len(_StderrTail.tail) > 10:
                del _StderrTail.tail[: len(_StderrTail.tail) - 10]

    Thread(target=_reader, daemon=True, name="ffmpeg-out").start()
    Thread(target=_stderr_reader, daemon=True, name="ffmpeg-err").start()

    def _fail() -> RuntimeError:
        tail = (_StderrTail().tail or [""])[-5:]
        text = "".join(tail).strip()
        reason = " | ".join(text.splitlines()[-5:]) if text else ""
        msg = "ffmpeg failed: " + (reason or f"exit code {p.returncode}")
        return RuntimeError(msg)

    StageCancelled_placeholder = StageCancelled
    try:
        while True:
            try:
                item = q.get(timeout=0.2)
            except Empty:
                if p.poll() is not None:
                    break
                if cancel is not None and cancel.is_set():
                    _proc._kill_tree(p.pid)
                    try:
                        p.wait(timeout=5)
                    except Exception:
                        pass
                    raise StageCancelled_placeholder("ffmpeg: cancelled") from None
                continue
            if item is None:
                break
            seconds = _parse_progress_seconds(str(item))
            if seconds is None:
                continue
            _emit_ffmpeg_ratio(seconds, float(total_s or 1.0), state[0], progress, state)
        if p.poll() is None:
            try:
                p.wait(timeout=300)
            except Exception:
                _proc._kill_tree(p.pid)
                p.wait(timeout=5)
        if cancel is not None and cancel.is_set():
            raise StageCancelled_placeholder("ffmpeg: cancelled") from None
        if p.returncode != 0:
            raise _fail()
    finally:
        for stream in (p.stdout, p.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


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


_STREAM_BLOCK_SECONDS = 30
_RF64_THRESHOLD_BYTES = 3_800_000_000  # switch WAV -> RF64 safely below 4 GiB RIFF cap


def _read_clip_pcm(clip_path: Path, target_rate: int, target_channels: int):
    """Read a WAV clip as float32 ndarray ``(frames, target_channels)`` at ``target_rate``.

    Uses ``soundfile`` + ``numpy`` directly (O(clip_len), zero full-track copies).
    Resampling runs only when ``sr != target_rate`` via linear interpolation.
    """
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(clip_path), dtype="float32", always_2d=True)
    if data.size == 0:
        return np.zeros((0, target_channels), dtype=np.float32)

    # Channel adaptation (mono <-> stereo / multichannel).
    ch = data.shape[1]
    if ch != target_channels:
        if ch == 1 and target_channels > 1:
            data = np.repeat(data, target_channels, axis=1)
        elif target_channels == 1:
            data = data.mean(axis=1, keepdims=True)
        elif ch < target_channels:
            pad = np.zeros((data.shape[0], target_channels - ch), dtype=np.float32)
            data = np.concatenate([data, pad], axis=1)
        else:
            data = data[:, :target_channels]

    # Sample-rate adaptation when clip rate differs from output track rate.
    if sr != target_rate and data.shape[0] > 0:
        out_len = max(1, int(round(data.shape[0] * (target_rate / float(sr)))))
        x_old = np.linspace(0.0, 1.0, num=data.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
        resampled = np.empty((out_len, target_channels), dtype=np.float32)
        for c in range(target_channels):
            resampled[:, c] = np.interp(x_new, x_old, data[:, c])
        data = resampled

    return data


def _stream_mix_to_wav(
    total_frames: int,
    clips: list[tuple[Path, float]],
    out_wav: Path,
    *,
    sample_rate: int,
    channels: int,
    base_reader=None,
    cancel=None,
) -> None:
    """Stream-mix ``clips`` [(path, offset_seconds)] onto silence (or ``base_reader``)
    in fixed 30-second blocks using O(N) memory (~10-25 MB RAM regardless of video
    length) and O(N) time instead of pydub's O(N * total_len) buffer copying."""
    import numpy as np
    import soundfile as sf
    from dubtool.job import StageCancelled

    total_frames = max(1, int(total_frames))
    block_frames = max(1, int(sample_rate * _STREAM_BLOCK_SECONDS))
    ordered = sorted(
        [(Path(p), max(0.0, float(off))) for p, off in clips],
        key=lambda item: item[1],
    )
    clip_idx = 0
    n_clips = len(ordered)
    active: list[tuple[int, Any]] = []  # (start_frame, float32 ndarray)

    expected_bytes = total_frames * channels * 2 + 128
    wav_format = "RF64" if expected_bytes >= _RF64_THRESHOLD_BYTES else "WAV"

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(
        str(out_wav),
        mode="w",
        samplerate=sample_rate,
        channels=channels,
        subtype="PCM_16",
        format=wav_format,
    ) as sf_out:
        for block_start in range(0, total_frames, block_frames):
            if cancel is not None and cancel.is_set():
                raise StageCancelled("s6: cancelled by user")
            n = min(block_frames, total_frames - block_start)
            block_end = block_start + n

            if base_reader is not None:
                raw = base_reader.read(n, dtype="float32", always_2d=True)
                if raw.shape[0] < n:
                    pad = np.zeros((n - raw.shape[0], channels), dtype=np.float32)
                    buf = np.concatenate([raw, pad], axis=0)
                else:
                    buf = raw.copy()
            else:
                buf = np.zeros((n, channels), dtype=np.float32)

            while clip_idx < n_clips:
                c_path, c_offset = ordered[clip_idx]
                c_start = int(round(c_offset * sample_rate))
                if c_start >= block_end:
                    break
                clip_idx += 1
                data = _read_clip_pcm(c_path, sample_rate, channels)
                if data.shape[0] > 0:
                    active.append((c_start, data))

            next_active: list[tuple[int, Any]] = []
            for c_start, data in active:
                c_end = c_start + data.shape[0]
                ov_start = max(block_start, c_start)
                ov_end = min(block_end, c_end)
                if ov_end > ov_start:
                    buf[ov_start - block_start : ov_end - block_start] += (
                        data[ov_start - c_start : ov_end - c_start]
                    )
                if c_end > block_end:
                    next_active.append((c_start, data))
            active = next_active

            np.clip(buf, -1.0, 1.0, out=buf)
            sf_out.write(buf)


def render_dub_track(
    duration_s: float,
    clips: list[tuple[Path, float]],
    out_wav: Path,
    *,
    sample_rate: int = OUT_SAMPLE_RATE,
    channels: int = 1,
    cancel=None,
) -> None:
    """Build the full-length Vietnamese dub track directly onto silence in
    streaming 30-second blocks — no intermediate ``_silence.wav`` file on disk."""
    total_frames = max(1, int(round(max(float(duration_s), 0.0) * sample_rate)))
    _stream_mix_to_wav(
        total_frames,
        clips,
        Path(out_wav),
        sample_rate=int(sample_rate),
        channels=max(1, int(channels)),
        base_reader=None,
        cancel=cancel,
    )


def mix_overlays(
    base_wav: Path,
    clips: list[tuple[Path, float]],
    out_wav: Path,
    *,
    cancel=None,
) -> None:
    """Overlay `clips` [(path, offset_seconds)] onto `base_wav` -> `out_wav`.

    Streams `base_wav` in 30-second blocks via ``soundfile`` + ``numpy`` so
    mixing thousands of clips over multi-hour audio runs in O(N) time and
    constant RAM (~10-25 MB).
    """
    import soundfile as sf

    with sf.SoundFile(str(base_wav), mode="r") as base_sf:
        _stream_mix_to_wav(
            len(base_sf),
            clips,
            Path(out_wav),
            sample_rate=base_sf.samplerate,
            channels=base_sf.channels,
            base_reader=base_sf,
            cancel=cancel,
        )


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
    # AGU10 5.1: with jobs.copy_input=false the background lives OUTSIDE the
    # job dir (jobs/ c20… found this in the packaged verification run); a
    # bare .name only resolved when the input was copied into working_dir.
    # Absolute paths are safe in a proc.run arg list (no shell splitting).
    background_ref = (
        background.name if background.parent.resolve() == Path(working_dir).resolve()
        else str(background.resolve())
    )
    cmd = [
        ffmpeg_bin(), "-y",
        "-i", background_ref,
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
    try:
        result = proc.run(
            cmd, cwd=working_dir, cancel=cancel, low_priority=True
        )
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-8:]
            raise RuntimeError("mix failed: " + " | ".join(tail))
    finally:
        script_path.unlink(missing_ok=True)
