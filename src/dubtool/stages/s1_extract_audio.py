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
    src = job.input_video
    if not src.exists():
        raise RuntimeError(f"input video missing: {src}")

    if audio_utils.probe_audio_streams(src) == 0:
        raise RuntimeError(
            "input video has no audio stream — cannot dub a video without audio"
        )
    audio_utils.extract_audio(src, out, cancel=cancel)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no audio output")
    log.info("s1: extracted %s", out.name)
