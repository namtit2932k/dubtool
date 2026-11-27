"""Stage 1: extract mono 16 kHz WAV from the input video."""

from __future__ import annotations

import logging

from dubtool import audio_utils

log = logging.getLogger("dubtool")


def run(job, config, force: bool = False, cancel=None) -> None:
    out = job.audio_path
    if out.exists() and not force:
        log.info("s1: audio.wav already exists, skipping")
        return

    audio_utils.require_ffmpeg()
    # AGU10 5.1: with jobs.copy_input=false the original is read in place;
    # a missing/changed file raises SourceChanged (localized message).
    src = job.resolve_input()

    if audio_utils.probe_audio_streams(src) == 0:
        raise RuntimeError(
            "input video has no audio stream — cannot dub a video without audio"
        )
    part = out.with_name(out.stem + ".part" + out.suffix)
    part.unlink(missing_ok=True)
    try:
        audio_utils.extract_audio(src, part, cancel=cancel)
        if not part.exists() or part.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced no audio output")
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    log.info("s1: extracted %s", out.name)
