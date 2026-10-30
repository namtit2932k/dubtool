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
    # AGENT_UPDATE_7.md task 4: the customer-readable translation file.
    export_readable: bool = True
    readable_format: str = "docx"      # docx | txt


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
    out_readable: Path | None = None   # <name>_vi_translation.docx|.txt (AGU7 task 4)
    job_id: str | None = None
    readable_warning: str | None = None  # set when the video is ok but the export failed
    error: str | None = None
    detail: str | None = None
    llm_requests: int | None = None                     # AGU6 5.2
    llm_requests_per_stage: dict[str, int] | None = None  # AGU6 5.2


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
        self._fmt = logging.Formatter()
        self._emit = emit
        self._stage = stage

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        line = f"{self._fmt.formatTime(record, '%Y-%m-%d %H:%M:%S')} {record.levelname} {record.getMessage()}\n"
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
    """Classify by exception TYPE along the cause chain (AGENT_UPDATE_7.md
    4.3) — the old substring matching put the SDK's "timeout" validation
    error under `network`, telling a user with working internet that the
    network was down. LLM-level wrappers carry the transport verdict inside
    their type when the provider mapped it (TimeoutLLMError etc.)."""
    detail = repr(exc)
    if isinstance(exc, StageCancelled):
        return "cancelled", detail
    from dubtool.llm.base import (
        InternalLLMError,
        InvalidKeyError,
        MissingKeyError,
        NetworkLLMError,
        RateLimitError,
        RateLimitExhausted,
        TimeoutLLMError,
        _is_internal,
        _transport_kind,
        _walk,
    )
    # AGU8 2.4: the dedicated TTS-unavailable error (all retry rounds +
    # optional fallback exhausted, finished clips preserved) is never
    # matched by the generic network branch.
    from dubtool.tts.base import TTSUnavailableError
    for e in _walk(exc):
        if isinstance(e, TTSUnavailableError):
            return "tts_unavailable", detail
    for e in _walk(exc):
        if isinstance(e, (RateLimitError, RateLimitExhausted)):
            return "quota", detail
    kind = _transport_kind(exc)
    has_timeout = any(isinstance(e, TimeoutLLMError) for e in _walk(exc))
    has_network = any(isinstance(e, NetworkLLMError) for e in _walk(exc))
    if kind == "timeout" and not has_network:
        return "timeout", detail
    if kind == "network" or has_network:
        return "network", detail
    if has_timeout:
        return "timeout", detail
    for e in _walk(exc):
        if isinstance(e, (InvalidKeyError, MissingKeyError)):
            return "invalid_key", detail
    if _is_internal(exc) or isinstance(exc, InternalLLMError):
        return "internal", detail
    # Legacy provider-level codes carried on the exception object (a
    # status/reason check, not a message-text check).
    if _is_model_not_found(exc):
        return "model_not_found", detail
    lowered = str(exc).lower()
    if "resource_exhausted" in lowered:
        return "quota", detail
    if "no space" in lowered or "disk full" in lowered:
        return "disk_full", detail
    if "ffmpeg" in lowered:
        return "ffmpeg_missing", detail
    if "api key" in lowered or "placeholder" in lowered or "missingkey" in detail.lower():
        return "invalid_key", detail
    return "error", detail


def _is_model_not_found(exc: BaseException) -> bool:
    """404 / NOT_FOUND from the provider's error object (never from the
    message text)."""
    from dubtool.llm.base import _walk

    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    if code == 404 or status == "NOT_FOUND":
        return True
    return any(
        (getattr(e, "code", None) == 404
         or str(getattr(e, "status", "") or "").upper() == "NOT_FOUND")
        for e in _walk(exc)
    )


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
    interactive: bool = True,
    overload_dialog: Callable[[], str] | None = None,
    fillers_dialog: Callable[[list], dict | None] | None = None,
) -> DubResult:
    """Run the full pipeline on one video. Never raises; returns a DubResult.

    `interactive` marks an app-driven run (the overload policy may ask the
    user); `overload_dialog` is the AGU8 3.4 callback answering 'wait' or
    'fallback_fast' when Gemini is overloaded, called only when the policy
    is 'ask'."""
    budget: Any = None  # bound below, inside the try (exception paths read it)
    try:
        video, output_dir = Path(video), Path(output_dir)
        if not video.is_file():
            return DubResult(code="invalid", error=f"video not found: {video}")
        if not output_dir.is_dir():
            return DubResult(code="invalid", error=f"output folder not found: {output_dir}")

        # Keys only from the passed SecretsProvider (never .env in the app;
        # the CLI path builds its list from the env itself). They are handed
        # to the pipeline explicitly (AGENT_UPDATE_5.md 2.4): no os.environ,
        # so a deleted key cannot survive in the process environment and no
        # child process inherits a key.
        keys: list[str] = []
        if secrets is not None:
            getter = getattr(secrets, "get_gemini_keys", None)
            if callable(getter):
                keys = [k for k in (getter() or []) if k]
            else:
                single = secrets.get_gemini_key()
                if single:
                    keys = [single]
        if not keys:
            return DubResult(code="invalid_key", error="no Gemini API key (set it in Settings)")

        # Mode -> provider/model (packaging/modes.yaml, AGU4 6.4) resolved
        # WITH the user's settings so the Models tab takes effect
        # (AGENT_UPDATE_5.md task 1).
        from dubtool.app import modes as modes_mod

        user_settings = _user_settings()
        mode_cfg: dict[str, Any] = modes_mod.get(options.mode, user_settings)
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
        # AGU8 6.3: the Settings → General fidelity preset rides inside
        # cfg.translate (so it is hashed into the stage 3/5 inputs too).
        prio = str(user_settings.get("translate_priority", "balanced") or "balanced").lower()
        if prio in ("faithful", "timing"):
            overrides["translate"]["priority"] = prio
        if options.whisper_model:
            overrides["whisper_model"] = options.whisper_model
        if options.whisper_device:
            overrides["device"] = options.whisper_device
        if extra_overrides:
            for k, v in extra_overrides.items():
                overrides.setdefault(k, {}).update(v) if isinstance(v, dict) else overrides.update({k: v})

        llm_volatility: dict[str, Any] = {
            # AGU8 3.5: which mode is running (per-mode request timeout) —
            # read through getattr in the stages so the CLI keeps None.
            "llm_mode": options.mode,
            "llm_interactive": interactive,
            "llm_overload_dialog": overload_dialog,
            # AGU8 task 4: the filler suggestions dialog (called at most once
            # per run, only on fresh runs, only when the run is interactive)
            # and the video identity for per-video choice storage.
            "fillers_dialog": fillers_dialog,
            "fillers_video": video,
        }

        # Config lives next to the shipped files, so behavior is identical
        # from source and frozen; config.yaml must ship in the bundle.
        from dubtool.app.paths import bundle_root

        root = bundle_root()
        cfg = config_mod.load(root, overrides=overrides)

        # AGU6 4.2: CPU limits are speed settings, not output settings — the
        # thread budget rides on a dedicated attribute (never inside
        # cfg.subtitles, which is hashed into the stage-7 resume hash) and
        # the encode preset DOES ride inside subtitles because the preset
        # changes the file.
        from dubtool.app import cpu as cpu_mod

        cfg.encode_threads = cpu_mod.thread_budget()
        # AGU8 task-2 LLM state shared with the stages (per-mode timeout,
        # the interactive flag and the overload dialog callback). Plain
        # attributes: they are run-scoped, never hashed into resume state.
        cfg.llm_mode = llm_volatility["llm_mode"]
        cfg.llm_interactive = llm_volatility["llm_interactive"]
        cfg.llm_overload_dialog = llm_volatility["llm_overload_dialog"]
        cfg.fillers_dialog = llm_volatility["fillers_dialog"]
        cfg.fillers_video = llm_volatility["fillers_video"]
        _saved = _user_settings()
        _preset = str(_saved.get("video_encode_preset", "")) or None
        if _preset in ("veryfast", "faster", "medium"):
            cfg.subtitles["preset"] = _preset

        # One request budget for the whole run (AGU6 5.2), bound so every
        # stage's LLMClient shares the same counter.
        from dubtool.llm import budget as llm_budget

        budget = llm_budget.from_config(cfg)
        budget_token = llm_budget.bind(budget)
        requests_per_stage: dict[str, int] = {}
        jobs_root = Path(jobs_dir) if jobs_dir else _default_jobs_dir()
        jobs_root.mkdir(parents=True, exist_ok=True)
        job_id = _stable_job_id(video, options)
        job = _open_or_create_job(job_id, jobs_root, video)
        _apply_description(job, options)

        from dubtool.stages import s1_extract_audio, s2_transcribe
        from dubtool.stages import s3_translate, s4_tts, s5_fit_duration, s6_mix, s7_subtitles

        stage_map = {
            "s1": lambda j, c, force: s1_extract_audio.run(j, c, force=force, cancel=cancel),
            "s2": s2_transcribe.run,
            "s3": lambda j, c, force: s3_translate.run(j, c, force=force, cancel=cancel, api_keys=keys),
            "s4": lambda j, c, force: s4_tts.run(j, c, force=force, cancel=cancel),
            "s5": lambda j, c, force: s5_fit_duration.run(j, c, force=force, cancel=cancel, api_keys=keys),
            "s6": lambda j, c, force: s6_mix.run(j, c, force=force, cancel=cancel),
            "s7": lambda j, c, force: s7_subtitles.run(j, c, force=force, cancel=cancel),
        }

        for stage in STAGE_ORDER:
            if cancel.is_set():
                return DubResult(code="cancelled", cancelled=True, job_id=job.dir.name)
            # Dependency-aware resume (AGENT_UPDATE_5.md task 5.1): a stage
            # whose recorded inputs hash still matches is skipped, so a
            # rerun after a failed/changed stage only redoes what is stale.
            from dubtool.job import stage_inputs_hash

            current_hash = stage_inputs_hash(job, cfg, stage)
            if job.is_done(stage, current_hash) and not force:
                logging.getLogger("dubtool").info(
                    "%s: done with unchanged inputs, skipping", stage
                )
                progress(ProgressEvent(stage=stage, state="skipped"))
                continue
            progress(ProgressEvent(stage=stage, state="start"))
            started = time.monotonic()
            stage_requests_before = budget.used  # AGU6 5.2 per-stage accounting
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
                job.finish_stage(stage, status="failed", error=str(exc), elapsed_s=done_s)
                code, detail = _classify_error(exc)
                progress(ProgressEvent(stage=stage, state="error", message=str(exc), elapsed_s=done_s))
                return DubResult(
                    code=code, cancelled=False, job_id=job.dir.name,
                    error=str(exc), detail=detail,
                )
            finally:
                logging.getLogger("dubtool").removeHandler(bridge)
                bridge.close()
            stage_requests = budget.used - stage_requests_before
            if stage_requests:
                requests_per_stage[stage] = stage_requests
                logging.getLogger("dubtool").info(
                    "%s: %d LLM request(s) used (run total %d/%d)",
                    stage, stage_requests, budget.used, budget.limit,
                )
            stage_elapsed = round(time.monotonic() - started, 1)
            if stage == "s2":
                # AGU8 task 7: real stage-2 time for the whisper-model
                # tooltip in Settings (model, elapsed, video length).
                from dubtool.app import asr_stats

                try:
                    from dubtool import audio_utils as _au

                    _au.MediaCache(job.dir)
                    asr_stats.save({
                        "model": cfg.whisper_model,
                        "s2_elapsed_s": stage_elapsed,
                        "video_s": _au.probe_duration(job.dir / "input.mp4"),
                    })
                except Exception:
                    pass
            job.finish_stage(stage, inputs_hash=current_hash, elapsed_s=stage_elapsed)
            progress(
                ProgressEvent(stage=stage, state="done", elapsed_s=stage_elapsed)
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

        # AGENT_UPDATE_7.md task 4: the readable translation file. It runs
        # after stage 7 and BEFORE the job folder is cleaned up below (the
        # stage reads only translated.json / units.json / schedule.json),
        # and it is failure-isolated: an export problem never un-dones the
        # video result. If the Word export fails the .txt fallback runs
        # automatically; only failing twice leaves the run readable-less.
        out_readable: Path | None = None
        readable_warning: str | None = None
        if options.export_readable:
            from dubtool.stages import s8_export_readable as s8

            lang = str(_user_settings().get("language") or "vi")
            fmt = "docx" if str(options.readable_format or "docx").lower() == "docx" else "txt"
            duration_s: float | None = None
            try:
                from dubtool import audio_utils

                duration_s = audio_utils.probe_duration(target)
            except Exception:
                duration_s = None  # header row is simply left out
            try:
                out_readable = s8.deliver(
                    job.dir, target, fmt=fmt, lang=lang, duration_s=duration_s,
                    mark_uncertain=bool((cfg.readable or {}).get("mark_uncertain")),
                )
            except Exception as exc:
                logging.getLogger("dubtool").warning(
                    "s8: %s export failed (%s) — trying the .txt fallback", fmt, exc
                )
                try:
                    out_readable = s8.deliver(
                        job.dir, target, fmt="txt", lang=lang, duration_s=duration_s,
                        mark_uncertain=bool((cfg.readable or {}).get("mark_uncertain")),
                    )
                except Exception as exc2:
                    logging.getLogger("dubtool").warning(
                        "s8: readable translation could not be created: %s", exc2
                    )
                    readable_warning = str(exc2)

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
        result = DubResult(
            ok=True, code="ok", out_video=target, out_srt=out_srt, job_id=job_id,
            llm_requests=budget.used,
            llm_requests_per_stage=requests_per_stage,
        )
        result.out_readable = out_readable
        if readable_warning:
            result.readable_warning = readable_warning
        return result
    except StageCancelled:
        return DubResult(code="cancelled", cancelled=True,
                         llm_requests=getattr(budget, "used", 0))
    except Exception as exc:
        return DubResult(
            code="error", error=str(exc), detail=repr(exc),
            llm_requests=getattr(budget, "used", 0),
        )


def _user_settings() -> dict:
    """settings.json, readable even when running in odd environments (the
    empty dict then keeps the shipped defaults)."""
    try:
        from dubtool.app import settings as settings_mod

        return settings_mod.load()
    except Exception:
        return {}


def _default_jobs_dir() -> Path:
    from dubtool.app.paths import jobs_dir

    try:
        return jobs_dir()
    except Exception:
        from dubtool.job import jobs_root_default

        return jobs_root_default()
