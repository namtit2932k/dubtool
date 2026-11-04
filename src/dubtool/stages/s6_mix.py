"""Stage 6: build the Vietnamese track and mix it over the original audio.

AGENT_UPDATE_2.md: the original audio is NOT replaced. It is lowered
(static mode) or sidechain-ducked (duck mode) under the Vietnamese voice,
which plays at dub_gain_db. Video stream is copied, never re-encoded.
The later Demucs background track only needs a one-line swap of the
`background` argument passed to the mixer.
"""

from __future__ import annotations

import logging

from dubtool import audio_utils
from dubtool import schedule as sched
from dubtool.job import read_json

log = logging.getLogger("dubtool")


def effective_max_shift(config, sched_cfg: dict | None) -> float:
    """AGU8 6.3: the faithful preset raises the lag cap to 4 s (a config-set
    larger value still wins; `balanced`/`timing` keep the configured one)."""
    sched_cfg = sched_cfg or {}
    max_shift = float(sched_cfg.get("max_shift_s", sched.DEFAULT_MAX_SHIFT_S))
    translate = getattr(config, "translate", None) or {}
    if str(translate.get("priority", "balanced")).lower() == "faithful":
        max_shift = max(max_shift, 4.0)
    return max_shift


def run(job, config, force: bool = False, cancel=None) -> None:
    audio_utils.require_ffmpeg()
    from dubtool.audio_utils import MediaCache

    media_cache = MediaCache(job.dir)
    out = job.output_nosubs_path
    if out.exists() and not force:
        log.info("s6: output.nosubs.mp4 already exists, skipping")
        return

    # AGU10 5.1: the original video is read in place (no-copy default).
    src_video = job.resolve_input()

    if not audio_utils.has_audio_stream(src_video):
        return run_dub_only(job, config, cancel=cancel)

    # Update 12 task 3: sentence mode schedules the dub units (one clip per
    # whole sentence; schedule.json identifies them by unit_id); segment
    # mode keeps the old per-segment flow.
    from dubtool.dub_units import source_for_stage

    _mode, all_entries, _persist, base = source_for_stage(job, config)
    entries = [e for e in all_entries if e.get("vi")]
    # Only entries with a fitted clip take part in the schedule; empty-text
    # units never collide and take no part in next-start math downstream
    # (schedule.json also holds the playback times used by s7 for cues).
    clip_items = [
        (e["id"], float(e["start"]))
        for e in entries
        if (job.fitted_dir / f"{base(e)}.wav").exists()
    ]
    if not clip_items:
        raise RuntimeError("no fitted clips found — run stage s5 first")

    video_duration = media_cache.duration(src_video)
    fitted_by_id = {e["id"]: job.fitted_dir / f"{base(e)}.wav" for e in entries}
    durations = {
        seg_id: media_cache.duration(fitted_by_id[seg_id])
        for seg_id, _start in clip_items
    }
    sched_cfg = getattr(config, "schedule", None) or {}
    max_shift = effective_max_shift(config, sched_cfg)
    schedule = sched.schedule_clips(
        [(seg_id, start, durations[seg_id]) for seg_id, start in clip_items],
        gap_s=float(sched_cfg.get("gap_s", sched.DEFAULT_GAP_S)),
        max_shift_s=max_shift,
    )
    sched.write_schedule(job.dir, schedule)
    sched.log_report(schedule)
    clips = [
        (fitted_by_id[s["id"]], float(s["play_start"]))
        for s in schedule
    ]
    mix = config.mix

    # Build vi.wav to match the original audio's sample rate and channels.
    orig_rate, orig_channels = audio_utils.probe_audio_format(src_video)
    bed_dir = job.dir
    silence = bed_dir / "_silence.wav"
    audio_utils.make_silence(
        video_duration, silence, sample_rate=orig_rate, channels=orig_channels
    )
    audio_utils.mix_overlays(silence, clips, job.vi_audio_path)
    silence.unlink(missing_ok=True)

    graph = audio_utils.build_mix_graph(
        mix.get("mode", "static"),
        float(mix.get("original_gain_db", -14)),
        float(mix.get("dub_gain_db", 0)),
        mix.get("duck") or {},
    )
    audio_utils.mix_original_with_dub(
        working_dir=job.dir,
        background=src_video,
        dub=job.vi_audio_path,
        out=out,
        graph=graph,
        cancel=cancel,
    )
    log.info(
        "s6: wrote %s (%d clips over %.1fs, mode=%s, orig=%sdB, dub=%sdB)",
        out.name, len(clips), video_duration,
        mix.get("mode", "static"),
        mix.get("original_gain_db", -14), mix.get("dub_gain_db", 0),
    )


def run_dub_only(job, config, cancel=None) -> None:
    """Fallback for videos with no audio stream (AGENT_UPDATE_2.md 2.3)."""
    log.info("s6: input has no audio stream — dub-only mix written")
    src_video = job.resolve_input()
    video_duration = audio_utils.probe_duration(src_video)
    audio_utils.make_silence(video_duration, job.vi_audio_path, sample_rate=44100)
    audio_utils.mux(src_video, job.vi_audio_path, job.output_nosubs_path, cancel=cancel)
