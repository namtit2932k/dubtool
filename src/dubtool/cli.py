"""Entry point: dubtool run|stage|status|retts|review-zip|doctor.

AGENT_UPDATE.md additions: `--llm`, `--out`, `review-zip`; only the selected
provider's key is required at startup.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
import time
from pathlib import Path

from dubtool import audio_utils, config as config_mod
from dubtool.job import STAGE_ORDER, STAGE_NAMES, Job, load_job

log = logging.getLogger("dubtool")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _attach_job_log(job: Job) -> None:
    handler = logging.FileHandler(job.log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)


def _check_prerequisites() -> None:
    """Clear one-line errors, not stack traces (ground rule / acceptance check)."""
    try:
        audio_utils.require_ffmpeg()
    except audio_utils.FFmpegMissing as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


def _env_keys() -> list[str]:
    """The CLI's key pool from the environment/.env (GEMINI_API_KEY,
    GEMINI_API_KEY_2, …). Read-only; passed explicitly to stages
    (AGENT_UPDATE_5.md 2.4), never written back."""
    from dubtool.llm.base import looks_like_placeholder, read_env_key

    keys: list[str] = []
    index = 1
    while True:
        name = "GEMINI_API_KEY" if index == 1 else f"GEMINI_API_KEY_{index}"
        value = read_env_key(name)
        if not value:
            break
        if not looks_like_placeholder(value) and value not in keys:
            keys.append(value)
        index += 1
    return keys


def _require_provider(conf, provider_id: str | None = None) -> None:
    from dubtool.llm import require_provider_ready

    config_mod.load_env(Path.cwd())
    try:
        message = require_provider_ready(conf, provider_id)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    log.debug("%s", message)


def _stage_module(name: str):
    module_map = {
        "s1": "s1_extract_audio",
        "s2": "s2_transcribe",
        "s3": "s3_translate",
        "s4": "s4_tts",
        "s5": "s5_fit_duration",
        "s6": "s6_mix",
        "s7": "s7_subtitles",
    }
    return importlib.import_module(f"dubtool.stages.{module_map[name]}")


def _run_stage(job: Job, conf, stage: str, force: bool = False,
               api_keys: list[str] | None = None) -> None:
    # Dependency-aware resume (AGENT_UPDATE_5.md task 5.1): the recorded
    # inputs hash must still match for a done stage to be skipped.
    from dubtool.job import stage_inputs_hash

    current_hash = stage_inputs_hash(job, conf, stage)
    if job.is_done(stage, current_hash) and not force:
        print(f"{stage} ({STAGE_NAMES[stage]}): already done with unchanged inputs, skipping")
        return
    if stage in ("s3", "s5"):
        _require_provider(conf)
    job.start_stage(stage)
    started = time.monotonic()
    try:
        module = _stage_module(stage)
        if stage in ("s3", "s5"):
            module.run(job, conf, force=force, api_keys=api_keys or _env_keys())
        else:
            module.run(job, conf, force=force)
    except Exception as exc:
        job.finish_stage(stage, status="failed", error=str(exc))
        raise
    job.finish_stage(stage, inputs_hash=current_hash)
    log.info("%s (%s): finished in %.1fs", stage, STAGE_NAMES[stage], time.monotonic() - started)


def _resolve_out_target(args) -> Path | None:
    if not args.out:
        return None
    out = Path(args.out).resolve()
    if out.exists() and not args.force:
        print(
            f"ERROR: {out} already exists — pass --force to overwrite it",
            file=sys.stderr,
        )
        sys.exit(2)
    return out


def _doc_lang() -> str:
    """Language of the readable file's labels: the app's language setting
    (vi by default), read defensively so the CLI works everywhere."""
    try:
        from dubtool.app import settings as app_settings

        return str(app_settings.load().get("language") or "vi")
    except Exception:
        return "vi"


def _export_readable_cli(job: Job, video_out: Path, fmt: str = "docx") -> Path | None:
    """The readable file next to the CLI's final video, failure-isolated
    exactly like the app path (AGENT_UPDATE_7.md 5.5)."""
    from dubtool.stages import s8_export_readable as s8

    lang = _doc_lang()
    fmt = "docx" if str(fmt or "docx").lower() == "docx" else "txt"
    duration_s = None
    try:
        duration_s = audio_utils.probe_duration(video_out)
    except Exception:
        duration_s = None
    try:
        path = s8.deliver(job.dir, video_out, fmt=fmt, lang=lang, duration_s=duration_s)
    except Exception as exc:
        log.warning("s8: %s export failed (%s) — trying the .txt fallback", fmt, exc)
        try:
            path = s8.deliver(job.dir, video_out, fmt="txt", lang=lang, duration_s=duration_s)
        except Exception as exc2:
            print(
                "WARNING: the video is ready; the readable translation file "
                f"could not be created ({exc2})",
                file=sys.stderr,
            )
            return None
    print(f"Readable translation: {path}")
    return path


def cmd_export_readable(args) -> int:
    """`dubtool export-readable <job_id>` — regenerate the customer-readable
    translation file from an existing job folder."""
    _setup_logging(args.verbose)
    from dubtool.stages import s8_export_readable as s8

    job = load_job(args.job_id, Path("jobs"))
    if not job.translated_path.exists():
        print(
            f"ERROR: {job.translated_path} not found — this job's intermediate "
            "files were deleted; run the video again to regenerate it",
            file=sys.stderr,
        )
        return 2
    if args.out:
        target = Path(args.out)
        if target.suffix.lower() not in (".docx", ".txt"):
            target = target.with_suffix(".docx" if args.fmt == "docx" else ".txt")
    else:
        if not (job.dir / "output.mp4").exists():
            print(
                "ERROR: no output video in the job — pass --out to name the file",
                file=sys.stderr,
            )
            return 2
        target = s8.readable_target(job.output_path, args.fmt)
    lang = _doc_lang()
    fmt = "docx" if args.fmt == "docx" else "txt"
    duration_s = None
    try:
        duration_s = audio_utils.probe_duration(job.output_path)
    except Exception:
        duration_s = None
    meta = s8.make_meta(job.output_path if job.output_path.exists() else target, lang=lang, duration_s=duration_s)
    rows = s8.build_rows(job.dir)
    if fmt == "docx":
        s8.render_docx(target, meta, rows)
    else:
        s8.render_txt(target, meta, rows)
    print(f"Readable translation written to {target} ({len(rows)} rows)")
    return 0


def _copy_to_out(job: Job, out: Path | None) -> None:
    if out is None:
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copyfile(str(job.output_path), str(out))
    print(f"Copied final dub to {out}")


def _resolve_and_save_brief(args, job, input_video=None) -> None:
    """Resolve the video brief (CLI text > CLI file > job brief.txt >
    <video>.brief.txt next to the input video); remember it in the job dir.
    The agent never invents a brief (AGU3 rule 7 / task 7.1)."""
    from dubtool.brief import resolve_brief, save_brief

    brief = resolve_brief(
        cli_brief=getattr(args, "brief", None),
        cli_brief_file=getattr(args, "brief_file", None),
        input_video=input_video,
        job_dir=job.dir,
    )
    if brief:
        save_brief(job.dir, brief)
        log.info("brief: using video brief (%d chars, saved to %s)", len(brief), job.dir / "brief.txt")
    elif getattr(args, "brief", None) or getattr(args, "brief_file", None):
        log.warning("brief: provided brief resolved to empty text")


def cmd_run(args) -> int:
    _setup_logging(args.verbose)
    _check_prerequisites()

    video = Path(args.video).resolve()
    if not video.exists():
        print(f"ERROR: video not found: {video}", file=sys.stderr)
        return 2

    out = _resolve_out_target(args)
    from dubtool.job import jobs_root_default

    jobs_root = jobs_root_default()
    jobs_root.mkdir(exist_ok=True)
    conf = config_mod.load(
        Path.cwd(),
        overrides={
            "llm_provider": args.llm,
            "tts_voice": args.voice,
            "whisper_model": args.whisper_model,
            "mix": _mix_overrides(args),
            "subtitles": _subtitle_overrides(args),
            **_model_overrides(args),
        },
    )
    # Only the selected provider's key is required at startup.
    _require_provider(conf)

    job = Job.create(video=video, jobs_root=jobs_root, source_name="input" + video.suffix)
    _attach_job_log(job)
    job.ensure_dirs()
    job.copy_input(video)
    _resolve_and_save_brief(args, job, input_video=video)
    job.save()

    for stage in STAGE_ORDER:
        print(f"==> {stage} ({STAGE_NAMES[stage]})")
        _run_stage(job, conf, stage, force=args.force, api_keys=_env_keys())

    _copy_to_out(job, out)
    # AGENT_UPDATE_7.md task 4: the CLI produces the readable file too.
    if not args.no_readable:
        _export_readable_cli(job, out if out is not None else job.output_path, fmt=args.readable_format or "docx")
    print(f"Done. Output: {job.output_path} and {job.vi_audio_path}")
    return 0


def _model_overrides(args) -> dict:
    """AGU4 4.3: --model overrides the provider's configured model;
    --translate-concurrency overrides translate.concurrency."""
    out: dict = {}
    model = getattr(args, "model", None)
    if model:
        pid = getattr(args, "llm", None) or getattr(config_mod, "DEFAULTS", {}).get("llm_provider", "gemini")
        out["llm"] = {pid.lower(): {"model": model}}
    concurrency = getattr(args, "translate_concurrency", None)
    if concurrency is not None:
        out["translate"] = {"concurrency": max(1, int(concurrency))}
    return out


def _mix_overrides(args) -> dict | None:
    """CLI flags -> partial mix dict (None = no change)."""
    values: dict = {}
    if getattr(args, "mix_mode", None):
        values["mode"] = args.mix_mode
    if getattr(args, "original_gain_db", None) is not None:
        values["original_gain_db"] = args.original_gain_db
    if getattr(args, "dub_gain_db", None) is not None:
        values["dub_gain_db"] = args.dub_gain_db
    return values or None


def _subtitle_overrides(args) -> dict | None:
    if getattr(args, "no_burn", False):
        return {"burn": False}
    return None


def cmd_stage(args) -> int:
    _setup_logging(args.verbose)
    _check_prerequisites()
    job = load_job(args.job_id, Path("jobs"))
    _attach_job_log(job)
    _resolve_and_save_brief(args, job, input_video=None)
    conf = config_mod.load(
        Path.cwd(),
        overrides={
            "llm_provider": args.llm,
            "mix": _mix_overrides(args),
            "subtitles": _subtitle_overrides(args),
            **_model_overrides(args),
        },
    )
    if args.stage in ("s3", "s5"):
        _require_provider(conf)
    if args.stage == "s2" and getattr(args, "postprocess_only", False):
        from dubtool.stages.s2_transcribe import _postprocess_only

        _postprocess_only(job, conf)
        return 0
    _run_stage(job, conf, args.stage, force=args.force, api_keys=_env_keys())
    return 0


def cmd_status(args) -> int:
    _setup_logging(args.verbose)
    job = load_job(args.job_id, Path("jobs"))
    print(f"job {job.dir.name}: dir={job.dir}")
    for stage in STAGE_ORDER:
        print(f"  {stage} ({STAGE_NAMES[stage]}): {job.stage_status(stage)}")
    if job.state.get("last_error"):
        print(f"  last error: {job.state['last_error']}")
    return 0


def cmd_retts(args) -> int:
    _setup_logging(args.verbose)
    _check_prerequisites()
    job = load_job(args.job_id, Path("jobs"))
    _attach_job_log(job)
    conf = config_mod.load(Path.cwd())
    from dubtool.stages.s4_tts import regenerate_tts

    ids = parse_ids(args.ids)
    print(f"retts: regenerating segments {ids}")
    regenerate_tts(job, conf, ids)
    print("retts: done; now rebuilding stage s5/s6 as needed")
    _run_stage(job, conf, "s5", api_keys=_env_keys())
    _run_stage(job, conf, "s6")
    return 0


def cmd_review_zip(args) -> int:
    from dubtool.review_zip import cmd_review_zip as run_zip

    return run_zip(tag=args.tag)


def cmd_compare_translate(args) -> int:
    _setup_logging(args.verbose)
    config_mod.load_env(Path.cwd())  # keys live in .env
    job = load_job(args.job_id, Path("jobs"))
    _attach_job_log(job)
    conf = config_mod.load(Path.cwd())
    from dubtool import compare_translate as ct

    models = ct.parse_models(args.models)
    if not models:
        print("ERROR: --models must be `provider:model,...`", file=sys.stderr)
        return 2
    out_path = ct.run_compare(job, conf, models, start=args.from_unit, count=args.units)
    print(f"Comparison written to {out_path}")
    print("You judge the translations — the default model in config.yaml was not changed.")
    return 0


def cmd_clean(args) -> int:
    """`dubtool clean` delegates to the cleanup module (AGU6 6.2)."""
    from dubtool.app import cleanup as _cw

    root = Path(__file__).resolve().parents[2]
    argv = []
    if args.apply:
        argv.append("--apply")
        if args.all_regenerable:
            argv.append("--all-regenerable")
        if args.categories and args.categories != ",":
            argv += ["--categories", args.categories]
    if args.jobs_days != 14:
        argv += ["--jobs-days", str(args.jobs_days)]
    return _cw.main(argv)


def cmd_doctor(args) -> int:
    _setup_logging(args.verbose)
    ok = True
    try:
        audio_utils.require_ffmpeg()
        print("ffmpeg/ffprobe: OK")
    except audio_utils.FFmpegMissing as exc:
        ok = False
        print(f"ffmpeg/ffprobe: MISSING — {exc}")

    conf = config_mod.load(Path.cwd())
    config_mod.load_env(Path.cwd())
    print(f"llm_provider: {conf.llm_provider}")
    from dubtool.llm import require_provider_ready

    try:
        require_provider_ready(conf)
        print(f"{conf.llm_provider}: key/config OK")
    except Exception as exc:
        if conf.llm_provider == "ollama":
            print("ollama: no key needed (checked at run time)")
        else:
            ok = False
            print(f"{conf.llm_provider}: {exc}")
    for pkg, pip_name in (("edge_tts", "edge-tts"), ("faster_whisper", "faster-whisper")):
        try:
            importlib.import_module(pkg)
            print(f"{pip_name}: OK")
        except ImportError:
            ok = False
            print(f"{pip_name}: MISSING — pip install {pip_name}")
    try:
        importlib.import_module("dotenv")
        print("python-dotenv: OK")
    except ImportError:  # pragma: no cover
        ok = False
        print("python-dotenv: MISSING — pip install python-dotenv")
    return 0 if ok else 1


def parse_ids(spec: str) -> list[int]:
    ids: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            low, _, high = part.partition("-")
            ids.extend(range(int(low), int(high) + 1))
        else:
            ids.append(int(part))
    return ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dubtool", description="English -> Vietnamese video dubbing tool"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="process one video end-to-end")
    run_p.add_argument("video", help="path to the input video")
    run_p.add_argument("--llm", choices=("gemini", "ollama", "anthropic"), default=None)
    run_p.add_argument("--voice", default=None, help="edge-tts voice (Vietnamese)")
    run_p.add_argument("--whisper-model", default=None)
    run_p.add_argument("--out", default=None, help="copy the final dubbed video to this path")
    run_p.add_argument("--force", action="store_true", help="redo finished stages / overwrite --out")
    run_p.add_argument("--mix-mode", choices=("static", "duck"), default=None)
    run_p.add_argument("--original-gain-db", type=float, default=None)
    run_p.add_argument("--dub-gain-db", type=float, default=None)
    run_p.add_argument("--no-burn", action="store_true", help="do not burn subtitles")
    run_p.add_argument("--brief", default=None, help="video brief text (context for the translator; max 1000 chars)")
    run_p.add_argument("--brief-file", default=None, help="path to a video brief text file")
    run_p.add_argument("--model", default=None, help="override the provider's configured model id")
    run_p.add_argument("--translate-concurrency", type=int, default=None, help="parallel translation chunk calls (1-5)")
    run_p.add_argument(
        "--no-readable",
        action="store_true",
        help="do not write the customer-readable translation file next to the video",
    )
    run_p.add_argument(
        "--readable-format",
        choices=("docx", "txt"),
        default=None,
        help="format of the readable translation file (default docx)",
    )
    run_p.set_defaults(func=cmd_run)

    exp_p = sub.add_parser(
        "export-readable",
        help="(re)create the customer-readable translation file of an existing job",
    )
    exp_p.add_argument("job_id")
    exp_p.add_argument("--format", dest="fmt", choices=("docx", "txt"), default="docx")
    exp_p.add_argument("--out", default=None, help="write to this exact path instead of next to the job's video")
    exp_p.add_argument("--verbose", action="store_true")
    exp_p.set_defaults(func=cmd_export_readable, fmt="docx")

    stage_p = sub.add_parser("stage", help="run one stage on an existing job")
    stage_p.add_argument("job_id")
    stage_p.add_argument("stage", choices=STAGE_ORDER)
    stage_p.add_argument("--llm", choices=("gemini", "ollama", "anthropic"), default=None)
    stage_p.add_argument("--force", action="store_true")
    stage_p.add_argument("--mix-mode", choices=("static", "duck"), default=None)
    stage_p.add_argument("--original-gain-db", type=float, default=None)
    stage_p.add_argument("--dub-gain-db", type=float, default=None)
    stage_p.add_argument("--no-burn", action="store_true")
    stage_p.add_argument(
        "--postprocess-only",
        action="store_true",
        help="s2 only: apply dedupe/merge/split to the existing transcript without Whisper",
    )
    stage_p.add_argument("--brief", default=None, help="video brief text (context for the translator; max 1000 chars)")
    stage_p.add_argument("--brief-file", default=None, help="path to a video brief text file")
    stage_p.add_argument("--model", default=None, help="override the provider's configured model id")
    stage_p.add_argument("--translate-concurrency", type=int, default=None, help="parallel translation chunk calls")
    stage_p.set_defaults(func=cmd_stage)

    status_p = sub.add_parser("status", help="show per-stage state")
    status_p.add_argument("job_id")
    status_p.set_defaults(func=cmd_status)

    retts_p = sub.add_parser("retts", help="re-synthesize specific segments")
    retts_p.add_argument("job_id")
    retts_p.add_argument("--ids", required=True, help="comma-separated segment ids")
    retts_p.set_defaults(func=cmd_retts)

    zip_p = sub.add_parser("review-zip", help="build the assistant review package")
    zip_p.add_argument("--tag", default=None, help="optional suffix, e.g. phase2")
    zip_p.set_defaults(func=cmd_review_zip)

    doctor_p = sub.add_parser("doctor", help="check prerequisites")
    doctor_p.set_defaults(func=cmd_doctor)

    clean_p = sub.add_parser(
        "clean",
        help="dry-run/apply the workspace cleanup plan (see also scripts/clean_workspace.py)",
    )
    clean_p.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    clean_p.add_argument("--categories", default=",", help="categories to delete with --apply")
    clean_p.add_argument("--all-regenerable", action="store_true")
    clean_p.add_argument("--jobs-days", type=int, default=14)
    clean_p.set_defaults(func=cmd_clean)

    cmp_p = sub.add_parser(
        "compare-translate",
        help="translate the same units with several models for human comparison",
    )
    cmp_p.add_argument("job_id")
    cmp_p.add_argument(
        "--units", type=int, default=20, help="how many consecutive units to compare"
    )
    cmp_p.add_argument(
        "--from", dest="from_unit", type=int, default=0, help="unit index to start at"
    )
    cmp_p.add_argument(
        "--models",
        required=True,
        help="comma-separated provider:model pairs, e.g. gemini:gemini-3.5-flash-lite",
    )
    cmp_p.set_defaults(func=cmd_compare_translate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
