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


# Upstream artifacts per stage (AGENT_UPDATE_5.md task 5.1); globs allowed.
# The caller hashes the file list's (name, size, mtime) plus the config
# values that can change that stage's output (model, voice, mix, subtitles).

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
    def create(cls, video: Path, jobs_root: Path, source_name: str | None = None) -> "Job":
        """Create jobs/<id>/ with input.<ext>; idempotent across re-runs."""
        source_name = source_name or ("input" + video.suffix)
        job_dir = jobs_root / next_job_id(video)
        job_dir.mkdir(parents=True, exist_ok=True)
        return cls(job_dir, source_name)
    def _load_state(self) -> dict:

        default = {
            "job_id": self.dir.name,
            "source": self.source,
            "stages": {s: "pending" for s in STAGE_ORDER},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
            "inputs": {},
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
                     inputs_hash: str | None = None) -> None:
        self.state["stages"][stage] = status
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
        """Copy user's video in as <source> atomically (crash-safe rename)."""
        self.ensure_dirs()
        tmp = self.dir / (self.source + ".part")
        shutil.copy2(str(video_file), str(tmp))
        os.replace(tmp, self.input_video)


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
        return [cfg.whisper_model, cfg.device, cfg.compute_type]
    if stage in ("s3", "s5"):
        pid = cfg.llm_provider
        return {
            "model": [pid, cfg.provider_config(pid).get("model")],
            "translate": cfg.translate,
            "units": cfg.units,
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
