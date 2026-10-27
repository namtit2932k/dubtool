"""Stage 7: subtitle export and burned-in Vietnamese subtitles.

AGENT_UPDATE.md 3 + AGENT_UPDATE_2.md 3 + AGENT_UPDATE_3.md section 2:
- The soft output.vi.srt is always written.
- libass sizes for SRT are computed on a virtual 288-px frame and scaled by
  video_height/288, so we choose intended *pixel* sizes (font height/ratio,
  margin height/margin_ratio, outline max(1, height/540)) and convert them
  with subtitles.px_to_ass_units. `original_size` has no effect and is not
  used.
- Cues follow schedule.json when it exists (clip playback schedule);
- Burning re-encodes video; --no-burn keeps the video stream copied.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from dubtool import audio_utils, proc, subtitles as subs
from dubtool.job import read_json

log = logging.getLogger("dubtool")


def build_burn_filter(
    srt_name: str,
    fontsdir: str,
    font_name: str,
    font_size: float,
    margin_v: float,
    outline: float,
) -> str:
    """Pure: the libass subtitles filter string for burning.

    Sizes must already be converted to libass units (px_to_ass_units).
    """
    style = (
        f"FontName={font_name},FontSize={font_size:.2f},"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        f"BorderStyle=1,Outline={outline:.2f},Shadow=0,Alignment=2,"
        f"MarginV={margin_v:.2f}"
    )
    return f"subtitles={srt_name}:fontsdir={fontsdir}:force_style='{style}'"


def style_in_units(height: int, width: int, cfg: dict) -> dict:
    """Intended pixel sizes -> libass units, plus the chosen line width."""
    height = int(height or 720)
    font_px = max(height / float(cfg.get("size_ratio", 24)), 8 * subs.LIBASS_SRT_PLAYRES_Y / height)
    margin_px = max(height / float(cfg.get("margin_ratio", 18)), 2 * subs.LIBASS_SRT_PLAYRES_Y / height)
    outline_px = max(1.0, height / 540.0)
    max_line_chars = subs.default_line_chars(width or 1, height, cfg.get("max_line_chars"))
    return {
        "font_px": font_px,
        "font_size": subs.px_to_ass_units(font_px, height),
        "margin_px": margin_px,
        "margin_v": subs.px_to_ass_units(margin_px, height),
        "outline_px": outline_px,
        "outline": subs.px_to_ass_units(outline_px, height),
        "max_line_chars": max_line_chars,
    }


def _load_schedule(job) -> list[dict] | None:
    schedule_path = job.dir / "schedule.json"
    if not schedule_path.exists():
        return None
    try:
        schedule = read_json(schedule_path)
    except Exception:
        return None
    return schedule if isinstance(schedule, list) else None


def run(job, config, force: bool = False, cancel=None) -> None:
    audio_utils.require_ffmpeg()
    cfg = config.subtitles

    entries = read_json(job.translated_path)
    if not isinstance(entries, list):
        raise RuntimeError("translated.json is malformed — rerun stage s3")

    # 1. Soft subtitles (always).
    schedule = _load_schedule(job)
    info = audio_utils.probe_video_info(job.output_nosubs_path)
    style = style_in_units(int(info.get("height") or 720), int(info.get("width") or 0), cfg)
    srt_text = subs.build_srt(
        entries, job.fitted_dir, schedule=schedule, max_line_chars=style["max_line_chars"]
    )
    job.srt_path.write_text(srt_text, encoding="utf-8", newline="\r\n")
    log.info("s7: wrote %s (%d cues)", job.srt_path.name, srt_text.count("-->"))

    base = job.output_nosubs_path
    out = job.output_path
    if not cfg.get("burn", True):
        if not out.exists() or force:
            shutil.copyfile(str(base), str(out))
            log.info("s7: burning disabled — copied video stream unchanged")
        return
    if out.exists() and not force:
        log.info("s7: output.mp4 already burned, skipping")
        return

    # 2. Burn.
    found = subs.find_system_font(cfg.get("font", "Arial"))
    if found is None:
        raise RuntimeError(
            "no Vietnamese-capable font found (Arial/Segoe UI/Tahoma). "
            "Install one or set subtitles.font in config.yaml."
        )
    font_file, font_name = found
    job.fonts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(font_file), str(job.fonts_dir / font_file.name))

    log.info(
        "s7: burn style: FontName=%s, %d px font -> %.2f units, %d px margin -> %.2f units, "
        "%.1f px outline -> %.2f units, max_line_chars=%d",
        font_name,
        style["font_px"], style["font_size"], style["margin_px"], style["margin_v"],
        style["outline_px"], style["outline"], style["max_line_chars"],
    )
    graph = build_burn_filter(
        srt_name=job.srt_path.name,
        fontsdir=job.fonts_dir.name,
        font_name=font_name,
        font_size=style["font_size"],
        margin_v=style["margin_v"],
        outline=style["outline"],
    )

    tmp_out = job.dir / "output.tmp.mp4"
    if tmp_out.exists():
        tmp_out.unlink()
    preset = str(cfg.get("preset", "medium")) or "medium"
    cmd = [
        audio_utils.ffmpeg_bin(), "-y",
        "-i", base.name,
        "-vf", graph,
        "-c:v", "libx264",
        "-crf", str(int(cfg.get("crf", 18))),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        tmp_out.name,
    ]
    threads = int(getattr(config, "encode_threads", 0) or 0)
    if threads > 0:
        cmd[-1:-1] = ["-threads", str(threads)]
    started = time.monotonic()
    result = proc.run(cmd, cwd=str(job.dir), cancel=cancel, low_priority=True)
    seconds = time.monotonic() - started
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError("burn failed: " + " | ".join(tail))
    os.replace(str(tmp_out), str(out))
    log.info("s7: burned subtitles into %s in %.1fs", out.name, seconds)
