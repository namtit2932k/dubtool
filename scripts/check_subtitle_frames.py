"""AGENT_UPDATE_3.md task 1 real-job check (2.4).

Burns the NEW subtitle sizing onto the existing un-burned output.nosubs.mp4
of a job into a TEMP file under <job>/check/ (deliverables are not touched),
then measures >= 5 burned cues against the same frames without subtitles:
text-block height as % of frame height and bottom-edge distance from the
frame bottom. Expected: about 8% for two lines at 1080p; > 15% is a failure.

Usage: py scripts/check_subtitle_frames.py <job_id> [cues...default 6]
"""

from __future__ import annotations

import json
import shutil
from dubtool import audio_utils, proc
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dubtool import audio_utils, subtitles as subs  # noqa: E402
from dubtool.config import load as load_config  # noqa: E402
from dubtool.stages.s7_subtitles import build_burn_filter, style_in_units  # noqa: E402


def parse_srt(text: str):
    cues = []
    block = []
    for line in text.splitlines():
        if line.strip() == "":
            if block:
                cues.append(block)
                block = []
        else:
            block.append(line.strip())
    if block:
        cues.append(block)
    out = []
    for b in cues:
        if len(b) >= 3 and "-->" in b[1]:
            times = b[1].split("-->")
            start = _srt_time(times[0].strip())
            end = _srt_time(times[1].strip())
            out.append({"start": start, "end": end, "text": " ".join(b[2:])})
    return out


def _srt_time(stamp: str) -> float:
    hh, mm, rest = stamp.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def measure(burned_png: Path, ref_png: Path, threshold: int = 40) -> dict:
    import numpy as np
    import PIL.Image as PILImage

    ga = np.asarray(PILImage.open(burned_png).convert("L"), dtype=np.int16)
    gb = np.asarray(PILImage.open(ref_png).convert("L"), dtype=np.int16)
    diff = np.abs(ga - gb)
    strong = diff > threshold
    rows = np.where(strong.sum(axis=1) > 5)[0]  # ignore 1-2 px re-encode noise
    if len(rows) == 0:
        return {"height_pct": 0.0, "bottom_pct": 0.0, "changed": 0}
    # The subtitle band is the contiguous run of diff rows ending at the
    # last diff row (walking upward with a small gap allowance between
    # neighbouring rows). Motion or grain elsewhere is farther up and
    # separated by larger gaps.
    bottom = rows.max()
    top = bottom
    prev = bottom
    for r in rows[::-1]:
        if prev - r <= 6:
            top = r
            prev = r
        else:
            break
    height_px = bottom - top + 1
    frame_h = ga.shape[0]
    return {
        "height_pct": round(100.0 * height_px / frame_h, 2),
        "bottom_pct": round(100.0 * (frame_h - 1 - bottom) / frame_h, 2),
        "changed": int(strong[top : bottom + 1].sum()),
    }


def pick_cues(cues: list[dict], count: int) -> list[dict]:
    diacritics = ("ắ", "ữ", "ề", "ộ", "ỵ")
    chosen: dict[str, dict] = {}
    # 1. a cue with one of the target diacritics
    for c in cues:
        if any(ch in c["text"] for ch in diacritics):
            chosen["diacritics"] = c
            break
    # 2. the longest cue (by text length)
    longest = max(cues, key=lambda c: len(c["text"]))
    chosen["longest"] = longest
    # 3. spread over the timeline until we reach the count
    step = max(len(cues) // count, 1)
    for i in range(0, len(cues), step):
        if len(chosen) >= count:
            break
        key = f"t{cues[i]['start']:.1f}"
        if key not in chosen and cues[i]["text"] != longest["text"]:
            chosen[key] = cues[i]
    return list(chosen.values())[: max(count, 2)]


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else "test1-e5df32ffda"
    n_cues = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    job = Path("jobs") / job_id
    config = load_config(Path("."))
    cfg = config.subtitles

    entries = json.loads((job / "translated.json").read_text(encoding="utf-8"))
    info = audio_utils.probe_video_info(job / "output.nosubs.mp4")
    style = style_in_units(int(info.get("height") or 720), int(info.get("width") or 0), cfg)
    srt_text = subs.build_srt(entries, job / "fitted", schedule=None, max_line_chars=style["max_line_chars"])
    check = job / "check"
    check.mkdir(exist_ok=True)
    # The burn filter wants a relative FILE INSIDE the job dir (no subdir),
    # same as stage 7 does; subdir paths break filter parsing on Windows.
    srt_path = job / "tmp_check.vi.srt"
    srt_path.write_text(srt_text, encoding="utf-8", newline="\r\n")

    found = subs.find_system_font(cfg.get("font", "Arial"))
    if found is None:
        print("ERROR: no Vietnamese-capable font found")
        return 2
    font_file, font_name = found
    fonts_dir = job / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    shutil.copyfile(str(font_file), str(fonts_dir / font_file.name))

    graph = build_burn_filter(
        srt_name=srt_path.name,
        fontsdir=fonts_dir.name,
        font_name=font_name,
        font_size=style["font_size"],
        margin_v=style["margin_v"],
        outline=style["outline"],
    )
    burned_tmp = job / "tmp_check_burned.mp4"
    if burned_tmp.exists():
        burned_tmp.unlink()
    proc.run(
        [
            audio_utils.ffmpeg_bin(), "-y",
            "-i", (job / "output.nosubs.mp4").name,
            "-vf", graph,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-an", "-sn", "-movflags", "+faststart",
            burned_tmp.name,
        ],
        cwd=job,
        check=True,
    )

    cues = parse_srt(srt_text)
    selected = pick_cues(cues, n_cues)
    print(f"job={job_id} font={font_name} font_units={style['font_size']:.2f} "
          f"margin_units={style['margin_v']:.2f} outline_units={style['outline']:.2f} "
          f"(px: {style['font_px']:.1f}/{style['margin_px']:.1f}/{style['outline_px']:.1f})")
    print(f"{'t(s)':>8} | {'block %':>7} | {'bottom %':>8} | note")
    for cue in selected:
        t = max(cue["start"], (cue["start"] + min(cue["end"], cue["start"] + 5.0)) / 2)
        burn_png = check / f"task1_t{t:.1f}_burned.png"
        ref_png = check / f"task1_t{t:.1f}_ref.png"
        for out_png in (burn_png, ref_png):
            if out_png.exists():
                out_png.unlink()
        proc.run(
            [audio_utils.ffmpeg_bin(), "-y", "-ss", f"{t:.3f}", "-i", burned_tmp.name,
             "-frames:v", "1", str(burn_png.relative_to(job))],
            cwd=job, check=True,
        )
        proc.run(
            [audio_utils.ffmpeg_bin(), "-y", "-ss", f"{t:.3f}", "-i", "output.nosubs.mp4",
             "-frames:v", "1", str(ref_png.relative_to(job))],
            cwd=job, check=True,
        )
        m = measure(burn_png, ref_png)
        note = "diacritics cue" if any(k for k in ("ắ", "ữ", "ề", "ộ", "ỵ") if k in cue["text"]) else ""
        print(f"{t:>8.2f} | {m['height_pct']:>6.1f}% | {m['bottom_pct']:>7.1f}% | {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
