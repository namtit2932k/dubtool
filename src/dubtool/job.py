"""Job directory, state file, and atomic JSON helpers.

A job lives in `jobs/<job_id>/` with a fixed set of files (see AGENT.md
section 3). All JSON is written atomically (temp file + rename) with
ensure_ascii=False, UTF-8, indent=2.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGE_ORDER = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
STAGE_NAMES = {
    "s1": "extract audio",
    "s2": "transcribe",
    "s3": "translate",
    "s4": "text-to-speech",
    "s5": "fit duration",
    "s6": "mix and mux",
    "s7": "burn subtitles",
}


class StageCancelled(RuntimeError):
    """Raised by stages between items/steps when the user cancels (AGU4 6.3).

    The job state on disk is already resume-safe (incremental writes), so a
    StageCancelled leaves the job resumable — the handler must NOT mark the
    stage failed. Stage status stays 'running' in state.json on purpose:
    load-time logic treats a non-'done' stage as pending for the rerun.
    """


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def compute_hash(path: Path) -> str:
    sha = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()[:10]


def compute_hash_with_meta(path: Path) -> str:
    """Content-ish identity that is cheap for big files: size + first 128 KB."""
    sha = hashlib.sha1()
    with open(path, "rb") as fh:
        sha.update(str(path.stat().st_size).encode())
        sha.update(fh.read(128 * 1024))
    return sha.hexdigest()[:10]


class SourceChanged(RuntimeError):
    """The no-copy input video (AGU10 5.1) is missing or has changed —
    the run must stop instead of silently resuming with the wrong file.
    The message is the localized user-facing text."""


# AGU10 5.1: the exact localized message for a changed/missing original.
_CHANGED_MSG_VI = "Video gốc đã thay đổi hoặc bị di chuyển — hãy chọn lại video"


def video_fingerprint(path: Path) -> str:
    """SHA-256 of the first 1 MB + the last 1 MB + the size (AGU10 5.1):
    a cheap identity for a multi-GB file that still catches edits at
    either end."""
    size = path.stat().st_size
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        mib = 1024 * 1024
        sha.update(fh.read(mib))
        if size > mib:
            fh.seek(size - mib)
            sha.update(fh.read(mib))
    sha.update(str(size).encode("ascii"))
    return sha.hexdigest()


def next_job_id(video: Path) -> str:
    return f"{video.stem}-{compute_hash_with_meta(video)}"


def jobs_root_default() -> Path:
    """jobs/ relative to the current working directory (AGENT.md layout)."""
    return Path("jobs")


class Job:
    ALLOWED_FILENAMES = {"input.mp4"}  # accepted source names in the job dir

    def __init__(self, job_dir: Path, source: str = "input.mp4"):
        self.dir = Path(job_dir)
        self.source = source
        self.state_path = self.dir / "state.json"
        self.state = self._load_state()

    # ---------------------------------------------------------------- paths
    @property
    def input_video(self) -> Path:
        return self.dir / self.source

    @property
    def audio_path(self) -> Path:
        return self.dir / "audio.wav"

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.json"

    @property
    def translated_path(self) -> Path:
        return self.dir / "translated.json"

    @property
    def tts_dir(self) -> Path:
        return self.dir / "tts"

    @property
    def fitted_dir(self) -> Path:
        return self.dir / "fitted"

    @property
    def vi_audio_path(self) -> Path:
        return self.dir / "vi.wav"

    @property
    def output_path(self) -> Path:
        return self.dir / "output.mp4"

    @property
    def output_nosubs_path(self) -> Path:
        """Mixed video without burned subtitles (s6 output, s7 input)."""
        return self.dir / "output.nosubs.mp4"

    @property
    def srt_path(self) -> Path:
        return self.dir / "output.vi.srt"

    @property
    def fonts_dir(self) -> Path:
        return self.dir / "fonts"

    @property
    def check_dir(self) -> Path:
        return self.dir / "check"

    @property
    def log_path(self) -> Path:
        return self.dir / "pipeline.log"

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, video: Path, jobs_root: Path, source_name: str | None = None,
               copy_input: bool = False) -> "Job":
        """Create jobs/<id>/ without copying the input video (AGU10 5.1):
        the original's path, size, mtime and fingerprint are recorded in
        state.json and stage 1 reads the original in place. Historical
        behavior is restored with copy_input=True (config jobs.copy_input)
        and legacy job folders holding input.mp4 keep working.
        Idempotent across re-runs.
        """
        source_name = source_name or ("input" + video.suffix)
        job_dir = jobs_root / next_job_id(video)
        job_dir.mkdir(parents=True, exist_ok=True)
        job = cls(job_dir, source_name)
        if "original" not in job.state:
            if copy_input:
                job.copy_input(video)
            job.record_original(video)
            job.save()
        return job

    def record_original(self, video: Path) -> None:
        """Record the no-copy input identity (AGU10 5.1) so resolve_input
        can detect a changed/missing file instead of resuming silently."""
        st = video.stat()
        self.state["original"] = {
            "path": str(Path(os.path.abspath(str(video)))),
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "fingerprint": video_fingerprint(video),
        }
    def _load_state(self) -> dict:

        default = {
            "job_id": self.dir.name,
            "source": self.source,
            "stages": {s: "pending" for s in STAGE_ORDER},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
            "inputs": {},
            "stage_s": {},  # recorded elapsed seconds per finished stage (AGU8 7.2)
        }
        if self.state_path.exists():
            try:
                state = read_json(self.state_path)
            except Exception:
                return default
            merged = {**default, **state}
            # Only known stage keys, so a stale key never leaks into new runs.
            merged["stages"] = {s: merged["stages"].get(s, "pending") for s in STAGE_ORDER}
            recorded = state.get("inputs") or {}
            merged["inputs"] = {s: recorded[s] for s in STAGE_ORDER if isinstance(recorded.get(s), str)}
            return merged
        return default

    def save(self) -> None:
        write_json(self.state_path, self.state)

    # ------------------------------------------------------------- stages
    def stage_status(self, stage: str) -> str:
        return self.state["stages"].get(stage, "pending")

    def is_done(self, stage: str, inputs_hash: str | None = None) -> bool:
        """Dependency-aware done (AGENT_UPDATE_5.md task 5.1): with the
        current hash passed, the stage counts as done only when the hash
        recorded at its finish matches; otherwise it must rerun."""
        if self.stage_status(stage) != "done":
            return False
        if inputs_hash is None:
            return True
        recorded = (self.state.get("inputs") or {}).get(stage)
        return recorded == inputs_hash

    def start_stage(self, stage: str) -> None:
        self.state["stages"][stage] = "running"
        self.state["last_error"] = None
        self.save()

    def finish_stage(self, stage: str, status: str = "done", error: str | None = None,
                     inputs_hash: str | None = None, elapsed_s: float | None = None) -> None:
        self.state["stages"][stage] = status
        if elapsed_s is not None:
            self.state.setdefault("stage_s", {})[stage] = elapsed_s
        if error:
            self.state["last_error"] = f"{stage}: {error}"
        if status == "done" and inputs_hash:
            self.state.setdefault("inputs", {})[stage] = inputs_hash
        elif status != "done":
            # A failed/rerun stage loses its stamp, so resume recomputes it.
            self.state.get("inputs", {}).pop(stage, None)
        self.save()

    # ---------------------------------------------------------- outputs
    def ensure_dirs(self) -> None:
        self.tts_dir.mkdir(parents=True, exist_ok=True)
        self.fitted_dir.mkdir(parents=True, exist_ok=True)

    def copy_input(self, video_file: Path) -> None:
        """Copy user's video in as <source> atomically (crash-safe rename) —
        only when config jobs.copy_input is true (AGU10 5.1)."""
        self.ensure_dirs()
        tmp = self.dir / (self.source + ".part")
        shutil.copy2(str(video_file), str(tmp))
        os.replace(tmp, self.input_video)

    def resolve_input(self) -> Path:
        """The real input video for stage 1 (AGU10 5.1).

        With the no-copy default the original file is read in place and
        validated (path, size, mtime, fingerprint) — a moved, deleted,
        edited or replaced video raises SourceChanged with the localized
        message instead of resuming silently with the wrong file. A job
        folder that still carries input.mp4 (legacy or copy_input) wins
        and is used as today.
        """
        legacy = self.input_video
        if legacy.exists():
            return legacy
        orig = (self.state.get("original") or {}) if isinstance(self.state, dict) else {}
        path_s = str(orig.get("path") or "")
        if not path_s:
            raise SourceChanged(_CHANGED_MSG_VI + " (no recorded original path)")
        p = Path(path_s)
        if not p.is_file():
            raise SourceChanged(_CHANGED_MSG_VI + f" [{path_s}]")
        try:
            st = p.stat()
        except OSError as exc:
            raise SourceChanged(_CHANGED_MSG_VI + f" ({exc})") from exc
        if st.st_size != int(orig.get("size") or -1) or int(st.st_mtime) != int(orig.get("mtime") or -1):
            raise SourceChanged(_CHANGED_MSG_VI + f" [{path_s}]")
        fp = video_fingerprint(p)
        if fp != str(orig.get("fingerprint") or ""):
            raise SourceChanged(_CHANGED_MSG_VI + f" [{path_s}]")
        return p


_STAGE_UPSTREAM = {
    "s1": ["input.*"],
    "s2": ["audio.wav"],
    "s3": ["transcript.json", "brief.txt"],
    "s4": ["translated.json"],
    "s5": ["tts/*", "translated.json", "brief.txt"],
    "s6": ["fitted/*", "audio.wav"],
    "s7": ["output.nosubs.mp4", "output.vi.srt"],
}


def _cfg_part(cfg, stage: str):
    """The config slice that can change this stage's output (no keys)."""
    if stage == "s1":
        return {}
    if stage == "s2":
        # AGU8 4.x: ASR knobs (word_timestamps, condition_on_previous_text,
        # initial_prompt) change the transcript itself.
        return [cfg.whisper_model, cfg.device, cfg.compute_type, cfg.asr]
    if stage in ("s3", "s5"):
        pid = cfg.llm_provider
        return {
            "model": [pid, cfg.provider_config(pid).get("model")],
            "translate": cfg.translate,
            "units": cfg.units,
            # AGU8 tasks 3.3/4: the punctuation step and the filler policy
            # change what is translated (AGU8 5.5 "part of inputs_hash").
            "punctuate": cfg.punctuate,
            "asr_resegment": bool((cfg.asr or {}).get("sentence_resegment", True)),
            "fillers": cfg.fillers,
        }
    if stage == "s4":
        return [cfg.tts_voice, cfg.tts_rate, cfg.tts_trim_silence,
                cfg.chars_per_sec, cfg.max_stretch]
    if stage == "s6":
        return {"mix": cfg.mix, "schedule": cfg.schedule}
    if stage == "s7":
        return {"subtitles": cfg.subtitles}
    return {}


def stage_inputs_hash(job: "Job", cfg, stage: str) -> str:
    """Fingerprint of everything that can change this stage's output:
    upstream artifact (name, size, mtime), the relevant config values, and
    the recorded stamps of ALL earlier stages — so rerunning any stage
    automatically invalidates every later stage (task 5.1). Recorded in
    state.json at finish; resume compares it."""
    sha = hashlib.sha1()

    def feed(part) -> None:
        sha.update(json.dumps(part, sort_keys=True, default=str,
                              ensure_ascii=False).encode("utf-8"))

    for pattern in _STAGE_UPSTREAM[stage]:
        for p in sorted(job.dir.glob(pattern)):
            if p.is_file():
                try:
                    st = p.stat()
                except OSError:
                    continue
                feed([p.name, st.st_size, int(st.st_mtime)])
    if stage == "s1":
        # AGU10 5.1: with jobs.copy_input=false the original is not in the
        # job dir; its recorded identity is the s1 upstream instead, so a
        # changed source invalidates s1 (and every later stage via stamps).
        orig = (job.state.get("original") or {}) if isinstance(job.state, dict) else {}
        if orig:
            feed(["original", orig.get("size"), orig.get("mtime"), orig.get("fingerprint")])
    feed(_cfg_part(cfg, stage))
    recorded = job.state.get("inputs") or {}
    idx = STAGE_ORDER.index(stage)
    feed({"stamps": {s: recorded.get(s) for s in STAGE_ORDER[:idx]}})
    return sha.hexdigest()[:10]


def load_job(job_id: str, jobs_root: Path | None = None) -> Job:
    root = jobs_root or Path("jobs")
    job_dir = root / job_id
    if not job_dir.exists() or not (job_dir / "state.json").exists():
        raise RuntimeError(f"Job not found: {job_id} (no {job_dir}/state.json)")
    return Job(job_dir)


# AGU10 5.2, level "Giữ bản dịch và nhật ký": exactly the small files that
# survive a successful run. Everything else (tts/, fitted/, vi.wav,
# audio.wav, mixes/encode temporaries, copied input, final/nosubs mp4s) —
# plus any file not listed here as filed under "bulky" — is deleted.
_LIGHT_KEEP_FILES = {
    "state.json", "transcript.json", "translated.json", "units.json",
    "schedule.json", "words.json", "quality_report.json",
    "tts_failures.json", "durations.json", "brief.txt", "profile.csv",
    "pipeline.log", "pipeline.err.log",
}
_LIGHT_KEEP_SUFFIXES = (".srt",)
_LIGHT_KEEP_CONTAINS = ("_translation.",)


def prune_light(job_dir: Path) -> int:
    """Keep the small translation files and logs, delete the bulky ones
    (AGENT_UPDATE_10.md 5.2, level 'light'). Returns deleted bytes. Failed
    or cancelled runs never reach this — it is only called on success."""
    deleted = 0
    for child in list(job_dir.iterdir()):
        if child.is_symlink():
            continue
        if child.is_file():
            keep = (
                child.name in _LIGHT_KEEP_FILES
                or child.suffix.lower() in _LIGHT_KEEP_SUFFIXES
                or any(m in child.name for m in _LIGHT_KEEP_CONTAINS)
            )
            if not keep:
                try:
                    deleted += child.stat().st_size
                    child.unlink()
                except OSError:
                    pass
        elif child.is_dir():
            if child.name == "logs":
                continue  # AGU10 5.2: 'light' keeps the logs too
            size = tree_size_local(child)
            try:
                shutil.rmtree(child)
            except OSError:
                continue
            deleted += size
    return deleted


def prune_none(job_dir: Path) -> None:
    """AGU10 5.2 level 'Xóa hết' / AGU4 7.4: keep only the log (and a legacy
    logs/ subfolder), delete everything else including state.json so the
    folder counts as finished for housekeeping. Only called on success."""
    for child in list(job_dir.iterdir()):
        if child.is_symlink():
            continue
        if child.is_dir() and child.name != "logs":
            shutil.rmtree(child, ignore_errors=True)
        elif child.name != "pipeline.log":
            try:
                child.unlink()
            except OSError:
                pass
    try:
        (job_dir / "state.json").unlink(missing_ok=True)
    except OSError:
        pass


def tree_size_local(p: Path) -> int:
    """Size of a file/dir tree without following symlinks."""
    if p.is_symlink():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for entry in os.scandir(p):
        try:
            total += tree_size_local(Path(entry.path))
        except OSError:
            continue
    return total
