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
        }
        if self.state_path.exists():
            try:
                state = read_json(self.state_path)
            except Exception:
                return default
            merged = {**default, **state}
            # Only known stage keys, so a stale key never leaks into new runs.
            merged["stages"] = {s: merged["stages"].get(s, "pending") for s in STAGE_ORDER}
            return merged
        return default

    def save(self) -> None:
        write_json(self.state_path, self.state)

    # ------------------------------------------------------------- stages
    def stage_status(self, stage: str) -> str:
        return self.state["stages"].get(stage, "pending")

    def is_done(self, stage: str) -> bool:
        return self.stage_status(stage) == "done"

    def start_stage(self, stage: str) -> None:
        self.state["stages"][stage] = "running"
        self.state["last_error"] = None
        self.save()

    def finish_stage(self, stage: str, status: str = "done", error: str | None = None) -> None:
        self.state["stages"][stage] = status
        if error:
            self.state["last_error"] = f"{stage}: {error}"
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


def load_job(job_id: str, jobs_root: Path | None = None) -> Job:
    root = jobs_root or Path("jobs")
    job_dir = root / job_id
    if not job_dir.exists() or not (job_dir / "state.json").exists():
        raise RuntimeError(f"Job not found: {job_id} (no {job_dir}/state.json)")
    return Job(job_dir)
