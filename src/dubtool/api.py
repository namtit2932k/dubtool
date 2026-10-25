"""`run_dub` — the library entry point the desktop app uses (AGU4 6.3).

The pipeline stages (s1–s7) are the exact code the CLI has always used; this
module adds mode resolution, cancellation between items/stages, progress
events parsed from the stage logs, a stable job id for resume, and atomic
output delivery. The CLI keeps its own path (same stage code, same behavior).

Cancellation is cooperative: stages s3/s4/s5 accept a `cancel` event and
raise `StageCancelled` between items; every other stage is checked between
stages. On cancel the job is left resumable (stage stays non-'done').
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from dubtool import config as config_mod
from dubtool.job import STAGE_ORDER, StageCancelled


class SecretsProvider(Protocol):
    def get_gemini_key(self) -> str | None: ...


@dataclass
class DubOptions:
    mode: str = "fast"                 # "fast" | "accurate"
    burn_subtitles: bool = True
    save_srt: bool = False
    original_gain_db: float = -14.0
    voice: str = "vi-VN-HoaiMyNeural"
    keep_intermediate: bool = False
    description: str | None = None     # video brief text (user-supplied)
    translate_concurrency: int | None = None
    output_name_base: str | None = None
    whisper_model: str | None = None
    whisper_device: str | None = None


@dataclass
class ProgressEvent:
    stage: str
    state: str                          # start|progress|done|error|skipped
    done: int | None = None
    total: int | None = None
    message: str | None = None
    elapsed_s: float | None = None


@dataclass
class DubResult:
    ok: bool = False
    code: str = "ok"    # ok|cancelled|output_exists|invalid|error
    cancelled: bool = False
    out_video: Path | None = None
    out_srt: Path | None = None
    job_id: str | None = None
    error: str | None = None
    detail: str | None = None


# Log lines the stage modules emit for progress counting:
#   s3: "... chunk 2 done (45/71 units)"  (also "... segments)")
#   s4/s5: "s4: progress 3/50"
_PROGRESS_RE = re.compile(
    r"(?P<stage>s[345]): (?:units )?chunk \d+ done \((?P<chunk_done>\d+)/(?P<n>\d+)"
    r"|progress (?P<done>\d+)/(?P<total>\d+)"
)


class _LogBridge(logging.Handler):
    """Mirrors dubtool log records into the job log file and progress events."""

    def __init__(self, job_log_path: Path, emit: Callable[[ProgressEvent], None], stage: str):
        super().__init__(level=logging.INFO)
        self._file = open(job_log_path, "a", encoding="utf-8")
        self._emit = emit
        self._stage = stage

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        line = f"{logging.Formatter.formatTime(record, '%Y-%m-%d %H:%M:%S')} {record.levelname} {record.getMessage()}\n"
        try:
            self._file.write(line)
            self._file.flush()
        except OSError:
            pass
        if record.name != "dubtool":
            return
        m = _PROGRESS_RE.match(record.getMessage())
        if m and m.group("stage") == self._stage:
            if m.group("done"):
                done, total = int(m.group("done")), int(m.group("total"))
            else:
                done, total = int(m.group("chunk_done")), int(m.group("n"))
            self._emit(ProgressEvent(stage=self._stage, state="progress", done=done, total=total))

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass
        super().close()


def _stable_job_id(video: Path, options: DubOptions) -> str:
    """Same video (path, size, mtime) + relevant options -> same job id,\n    so re-running the same inputs resumes instead of starting over."""
    import hashlib

    h = hashlib.sha256()
    for part in (
        str(video.resolve()),
        str(video.stat().st_size),
        str(int(video.stat().st_mtime)),
        options.mode,
        "burn" if options.burn_subtitles else "noburn",
        "srt" if options.save_srt else "nosrt",
        f"{options.original_gain_db}",
        options.voice,
        options.output_name_base or "",
    ):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def _classify_error(exc: Exception) -> tuple[str, str | None]:
    text = str(exc)
    detail = repr(exc)
    if isinstance(exc, StageCancelled):
        return "cancelled", detail
    lowered = text.lower()
    if "api key" in lowered or "placeholder" in lowered or "missingkey" in detail.lower():
        return "invalid_key", detail
    if "quota" in lowered or "resource_exhausted" in lowered or "429" in lowered:
        return "quota", detail
    if "not found" in lowered or "404" in lowered or "model" in lowered and "not" in lowered and "found" in lowered:
        return "model_not_found", detail
    if "network" in lowered or "timed out" in lowered or "timeout" in lowered or "connection" in lowered:
        return "network", detail
    if "no space" in lowered or "disk" in lowered and "full" in lowered:
        return "disk_full", detail
    if "ffmpeg" in lowered:
        return "ffmpeg_missing", detail
    return "error", detail


def _open_or_create_job(job_id: str, jobs_root: Path, video: Path) -> "Job":
    from dubtool.job import Job

    source_name = "input" + video.suffix
    job_dir = jobs_root / job_id
    if job_dir.exists():
        job = Job(job_dir, source_name)
    else:
        job_dir.mkdir(parents=True, exist_ok=True)
        job = Job(job_dir, source_name)
    job.ensure_dirs()
    if not job.input_video.exists() or job.input_video.stat().st_size != video.stat().st_size:
        job.copy_input(video)
    job.save()
    return job


def _apply_description(job, options: DubOptions) -> None:
    from dubtool.brief import save_brief

    brief = (options.description or "").strip()
    if brief:
        save_brief(job.dir, brief)


def run_dub(
    video: Path,
    output_dir: Path,
    *,
    options: DubOptions,
    secrets: SecretsProvider,
    progress: Callable[[ProgressEvent], None],
    cancel: threading.Event,
    jobs_dir: Path | None = None,
    force: bool = False,
    extra_overrides: dict | None = None,
) -> DubResult:
    """Run the full pipeline on one video. Never raises; returns a DubResult."""
    try:
        video, output_dir = Path(video), Path(output_dir)
        if not video.is_file():
            return DubResult(code="invalid", error=f"video not found: {video}")
        if not output_dir.is_dir():
            return DubResult(code="invalid", error=f"output folder not found: {output_dir}")

        # Keys: only from the passed SecretsProvider (never .env in the app;
        # the CLI path reads .env itself). GeminiProvider reads os.environ.
        key = secrets.get_gemini_key() if secrets else None
        if not key:
            return DubResult(code="invalid_key", error="no Gemini API key (set it in Settings)")
        os.environ["GEMINI_API_KEY"] = key

        # Mode -> provider/model/concurrency (from packaging/modes.yaml, AGU4 6.4).
        from dubtool.app import modes as modes_mod

        mode_cfg: dict[str, Any] = modes_mod.get(options.mode)
        pid = str(mode_cfg.get("provider", "gemini"))
        overrides: dict[str, Any] = {
            "llm_provider": pid,
            "llm": {pid: {"model": mode_cfg.get("model")}},
            "tts_voice": options.voice,
            "mix": {"original_gain_db": float(options.original_gain_db)},
            "subtitles": {"burn": options.burn_subtitles},
            "translate": {"concurrency": int(
                options.translate_concurrency
                if options.translate_concurrency is not None
                else mode_cfg.get("translate_concurrency", 1)
            )},
        }
        if options.whisper_model:
            overrides["whisper_model"] = options.whisper_model
        if options.whisper_device:
            overrides["device"] = options.whisper_device
        if extra_overrides:
            for k, v in extra_overrides.items():
                overrides.setdefault(k, {}).update(v) if isinstance(v, dict) else overrides.update({k: v})

        # Config lives next to the shipped files, so behavior is identical
        # from source and frozen; config.yaml must ship in the bundle.
        from dubtool.app.paths import bundle_root

        root = bundle_root()
        cfg = config_mod.load(root, overrides=overrides)

        jobs_root = Path(jobs_dir) if jobs_dir else _default_jobs_dir()
        jobs_root.mkdir(parents=True, exist_ok=True)
        job_id = _stable_job_id(video, options)
        job = _open_or_create_job(job_id, jobs_root, video)
        _apply_description(job, options)

        from dubtool.stages import s1_extract_audio, s2_transcribe
        from dubtool.stages import s3_translate, s4_tts, s5_fit_duration, s6_mix, s7_subtitles

        stage_map = {
            "s1": s1_extract_audio.run,
            "s2": s2_transcribe.run,
            "s3": lambda j, c, force: s3_translate.run(j, c, force=force, cancel=cancel),
            "s4": lambda j, c, force: s4_tts.run(j, c, force=force, cancel=cancel),
            "s5": lambda j, c, force: s5_fit_duration.run(j, c, force=force, cancel=cancel),
            "s6": s6_mix.run,
            "s7": s7_subtitles.run,
        }

        for stage in STAGE_ORDER:
            if cancel.is_set():
                return DubResult(code="cancelled", cancelled=True, job_id=job.dir.name)
            progress(ProgressEvent(stage=stage, state="start"))
            started = time.monotonic()
            bridge = _LogBridge(job.log_path, progress, stage)
            logging.getLogger("dubtool").addHandler(bridge)
            try:
                stage_map[stage](job, cfg, force=force)
            except StageCancelled:
                done_s = round(time.monotonic() - started, 1)
                progress(ProgressEvent(stage=stage, state="error", message="cancelled", elapsed_s=done_s))
                # Leave the job resumable: do not mark the stage failed/done.
                return DubResult(code="cancelled", cancelled=True, job_id=job.dir.name)
            except Exception as exc:
                done_s = round(time.monotonic() - started, 1)
                job.finish_stage(stage, status="failed", error=str(exc))
                code, detail = _classify_error(exc)
                progress(ProgressEvent(stage=stage, state="error", message=str(exc), elapsed_s=done_s))
                return DubResult(
                    code=code, cancelled=False, job_id=job.dir.name,
                    error=str(exc), detail=detail,
                )
            finally:
                logging.getLogger("dubtool").removeHandler(bridge)
                bridge.close()
            job.finish_stage(stage)
            progress(
                ProgressEvent(stage=stage, state="done", elapsed_s=round(time.monotonic() - started, 1))
            )

        # Deliver the outputs atomically; never overwrite silently (AGU4 6.3).
        base_name = (options.output_name_base or video.stem) + "_vi.mp4"
        target = output_dir / base_name
        if target.exists() and not force:
            return DubResult(
                code="output_exists", out_video=target, job_id=job.dir.name,
                error=str(target),
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        tmp = output_dir / (base_name + ".part")
        shutil.copyfile(job.output_path, tmp)
        os.replace(tmp, target)

        out_srt = None
        if options.save_srt and job.srt_path.exists():
            srt_target = output_dir / (video.stem + "_vi.srt")
            tmp_srt = output_dir / (video.stem + "_vi.srt.part")
            shutil.copyfile(job.srt_path, tmp_srt)
            os.replace(tmp_srt, srt_target)
            out_srt = srt_target

        if not options.keep_intermediate:
            # Keep only the log (AGU4 7.4): delete everything else.
            for child in list(job.dir.iterdir()):
                if child.is_dir() and child.name != "logs":
                    shutil.rmtree(child, ignore_errors=True)
                elif child.name != "pipeline.log":
                    try:
                        child.unlink()
                    except OSError:
                        pass
            try:
                job.state_path.unlink(missing_ok=True)
            except OSError:
                pass

        job.save()
        return DubResult(ok=True, code="ok", out_video=target, out_srt=out_srt, job_id=job_id)
    except StageCancelled:
        return DubResult(code="cancelled", cancelled=True)
    except Exception as exc:
        return DubResult(code="error", error=str(exc), detail=repr(exc))


def _default_jobs_dir() -> Path:
    from dubtool.app.paths import jobs_dir

    try:
        return jobs_dir()
    except Exception:
        from dubtool.job import jobs_root_default

        return jobs_root_default()
