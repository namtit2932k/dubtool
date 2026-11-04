"""AGENT_UPDATE_11 3.2: the TTS failure experiment (outside pacing logic).

Real edge-tts calls, ONE request at a time, 3 trials per variant, over:
  - the 13 refused texts of job 361f048f9ad8 (tests/fixtures/tts_failing_texts.json),
  - 13 synthesized texts of similar length from the same job's translation.

Variants:
  A: the text as is, voice vi-VN-HoaiMyNeural;
  B: split at sentence/clause boundaries (<= tts.max_chars_per_request),
     requested separately and joined with an 80 ms silence;
  C: the text as is, voice vi-VN-NamMinhNeural;
  D: the text normalized (whitespace collapsed, ellipsis/quotes replaced).

Output: a table of success rate and mean latency per variant, simple text
statistics of the failing vs the successful set, and explicit statements of
what the data shows and does not show (including the rate-limit hypothesis).
All audio goes to <build root>/tmp/tts_experiment — never the project root.
Refusal trials also record the text of "fails in one trial, passes in
another" runs (rate-based vs text-based).

Usage:
    python scripts/tts_experiment.py            # A/B/C/D over both sets
    python scripts/tts_experiment.py --online   # same (explicit, no env)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

VOICES = {"A": "vi-VN-HoaiMyNeural", "C": "vi-VN-NamMinhNeural"}
TRIALS = 3
MAX_CHARS_PER_REQUEST = 90  # tts.max_chars_per_request default


# ----------------------------------------------------------------- variants
def split_sentences(text: str, cap: int = MAX_CHARS_PER_REQUEST) -> list[str]:
    """Split at . ? ! (and clause commas as a fallback), never above cap."""
    import re

    parts = re.split(r"(?<=[.?!])\s+", text.strip()) or [text.strip()]
    out: list[str] = []
    for p in parts:
        if len(p) <= cap:
            out.append(p)
            continue
        piece = ""
        for token in re.split(r"(?<=[,;:–-])\s*", p):
            if piece and len(piece) + len(token) > cap:
                out.append(piece)
                piece = token
            elif not piece:
                piece = token
            else:
                piece += (" " if not piece.endswith(" ") else "") + token
        if piece:
            out.append(piece)
    return out or [text]


def normalize(text: str) -> str:
    """Variant D: collapse whitespace, replace the ellipsis and the curly
    quotes, drop the trailing space; never cut a leading lowercase word."""
    t = (text.replace("…", "...").replace("“", "\"").replace("”", "\"")
             .replace("‘", "'").replace("’", "'"))
    return " ".join(t.split())


# ---------------------------------------------------------------- synthesis
async def _edge_save(text: str, voice: str, out: Path) -> tuple[bool, float]:
    """One edge-tts request; returns (success, latency_seconds)."""
    import edge_tts

    started = asyncio.get_event_loop().time()
    try:
        comm = edge_tts.Communicate(text=text, voice=voice, rate="+0%")
        await comm.save(str(out))
        return Path(out).exists(), asyncio.get_event_loop().time() - started
    except Exception as exc:
        print(f"    (edge-tts: {type(exc).__name__}: {str(exc)[:100]})")
        return False, asyncio.get_event_loop().time() - started


def join_pieces(pieces: list[Path], out: Path) -> None:
    """Concatenate the piece MP3s with an 80 ms silence between them
    (through the proc helper; never a direct subprocess)."""
    import tempfile
    from dubtool import proc

    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        raise SystemExit("ffmpeg not found (vendor/); cannot run variant B")
    silence = out.parent / "_sil80ms.mp3"
    r = proc.run([str(ffmpeg), "-y", "-v", "error", "-f", "lavfi",
                  "-t", "0.08", "-i",
                  "anullsrc=channel_layout=mono:sample_rate=24000",
                  str(silence)], timeout=30)
    if r.returncode != 0:
        raise SystemExit("ffmpeg silence generation failed")
    lst = out.with_suffix(".txt")
    lines: list[str] = []
    for i, p in enumerate(pieces):
        if i:
            lines.append(f"file '{silence.as_posix()}'")
        lines.append(f"file '{p.as_posix()}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = proc.run([str(ffmpeg), "-y", "-v", "error", "-f", "concat",
                  "-safe", "0", "-i", str(lst), "-c", "copy",
                  str(out)], timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg join failed: {r.returncode}")


def _ffmpeg_binary() -> str | None:
    from dubtool.app.paths import ffmpeg_dir

    for name in ("ffmpeg.exe", "ffmpeg"):
        cand = Path(ffmpeg_dir()) / name
        if cand.exists():
            return str(cand)
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    return None


async def run_variant(label: str, voice: str, text: str, out_dir: Path,
                      split: bool = False) -> tuple[int, list[float]]:
    """TRIALS attempts on one text; returns (successes, latencies)."""
    ok_lat, ok = [], 0
    for t in range(TRIALS):
        out = out_dir / f"{label}_t{t}.mp3"
        if not split:
            good, lat = await _edge_save(text, voice, out)
        else:
            pieces = split_sentences(text)
            lat = 0.0
            good = True
            made: list[Path] = []
            for i, piece in enumerate(pieces):
                one = out_dir / f"{label}_t{t}_p{i}.mp3"
                g, l = await _edge_save(piece, voice, one)
                lat += l
                if g:
                    made.append(one)
                else:
                    good = False
                    break
            if good and made:
                join_pieces(made, out)
        if good:
            ok += 1
            ok_lat.append(lat)
    if ok < TRIALS and ok > 0:
        print(f"    split verdict for {label}: {ok}/{TRIALS} passed — rate"
              f"-based suspicion (a pass in one trial, a refusal in another)")
    return ok, ok_lat


# --------------------------------------------------------------- statistics
def text_stats(texts: list[dict]) -> dict:
    joined = [t["vi"] for t in texts]
    lens = [len(t) for t in joined]
    return {
        "n": len(texts),
        "mean_len": round(statistics.mean(lens), 1),
        "sentences/text": round(statistics.mean(t.count(".") + t.count("!")
                                                + t.count("?") for t in joined), 2),
        "digits/text": round(statistics.mean(sum(c.isdigit() for c in t)
                                             for t in joined), 2),
        "? marks": sum(t.count("?") for t in joined),
        "commas": sum(t.count(",") for t in joined),
        "leading lowercase": sum(t[:1].islower() for t in joined),
        "non-VN letters": _non_vn_count(texts),
    }


def _non_vn_count(texts) -> int:
    # Letters outside the ASCII + Vietnamese repertoire per unicodedata.
    allowed = set("áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợ"
                  "úùủũụưứừửữựýỳỷỹỵđ")
    extra = 0
    for t in (x["vi"] for x in texts):
        for ch in t.lower():
            if ch.isalpha() and ch not in allowed and ch not in set(
                    map(chr, range(97, 123))):
                extra += 1
    return extra


def load_sets() -> tuple[list[dict], list[dict]]:
    failing = json.loads((PROJECT_ROOT / "tests" / "fixtures" /
                          "tts_failing_texts.json").read_text("utf-8"))
    translated = _job_translated()
    successful = []
    if translated:
        # the first 13 synthesized entries by ascending length distance to
        # the failing mean (like-for-like comparison)
        target = statistics.mean(len(r["vi"]) for r in failing)
        others = [r for r in translated
                  if r.get("vi") and r["id"] not in {x["id"] for x in failing}]
        others.sort(key=lambda r: abs(len(r["vi"]) - target))
        successful = others[: len(failing)]
    return failing, successful


def _job_translated() -> list[dict]:
    import os
    import json as _json

    base = (os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" /
                                                  "Local"))
    jobs = [Path(base) / "DubTool" / "jobs" / "361f048f9ad8",
            Path("D:/Projects/Vibe/Project_K_build/jobs/361f048f9ad8")]
    for j in jobs:
        f = j / "translated.json"
        if f.exists():
            return _json.loads(f.read_text("utf-8"))
    print("  (no job translation found for the successful set; only the"
          " failing set is compared)")
    return []


def main() -> int:
    failing, successful = load_sets()
    from dubtool import workspace

    out_dir = workspace.tmp_dir() / "tts_experiment"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, int, int, float]] = []  # (group, variant, ok, n, mean lat)

    for group, texts in (("failing", failing), ("similar-ok", successful)):
        if not texts:
            continue
        for i, row in enumerate(texts):
            text = row["vi"]
            for var, voice in VOICES.items():
                ok, lat = asyncio.run(run_variant(
                    f"{group[0]}{i}{var.lower()}{row['id']}", voice,
                    text, out_dir, split=False))
                m = statistics.mean(lat) if lat else float("nan")
                rows.append((group, var, ok, TRIALS, m))
            # B and D on HoaiMy once per text
            ok, lat = asyncio.run(run_variant(
                f"{group[0]}{i}b{row['id']}", VOICES["A"],
                text, out_dir, split=True))
            m = statistics.mean(lat) if lat else float("nan")
            rows.append((group, "B", ok, TRIALS, m))
            ok, lat = asyncio.run(run_variant(
                f"{group[0]}{i}d{row['id']}", VOICES["A"],
                normalize(text), out_dir, split=False))
            m = statistics.mean(lat) if lat else float("nan")
            rows.append((group, "D", ok, TRIALS, m))

    # -------- report
    print("\n== variant table (successes / trials, mean latency s) ==")
    agg: dict[tuple[str, str], list[list]] = {}
    for g, v, ok, n, m in rows:
        agg.setdefault((g, v), [[ok, n, m]])
        agg[(g, v)].append([ok, n, m])
    print(f"  {'group':<12} {'variant':<8} {'success':>10}  {'mean lat':>9}")
    for key in sorted(agg):
        g, v = key
        trials = [r for r in agg[key]]
        ok = sum(r[0] for r in trials)
        n = sum(r[1] for r in trials)
        lat = [r[2] for r in trials if r[2] == r[2]]  # skip NaN means
        mean = statistics.mean(lat) if lat else float("nan")
        print(f"  {g:<12} {v:<8} {ok:>4}/{n:<4}   {mean:>8.1f}")

    print("\n== text statistics: failing vs similar-ok ==")
    s1, s2 = text_stats(failing), text_stats(successful)
    for k in s1:
        print(f"  {k:<18} failing={s1[k]:>8}  ok={s2[k]:>8}")

    print("\n== what this shows and does not show ==")
    print("  - it shows (per variant, over 3 clean trials): whether the"
          " service rejects these specific texts on any of A-D, and how"
          " long a pass takes;")
    print("  - it does NOT show the retry-round behavior under rate limits:"
          " a refusal in one trial and a pass in another (visible in the"
          " split-verdict lines) points at rate-based refusals, not text;"
          " this experiment runs outside the app's pacing so its absolute"
          " latencies are not the app's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
