"""Run evaluation on demo video (test2.mp4) for AGU13 Task 4.1 and 4.2.
"""
import os
import sys
import json
import time
import threading
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dubtool import api
from dubtool.api import DubOptions
from dubtool.app import secrets
from dubtool.config import load_env

load_env(Path("."))

video_path = Path("sample/input/test2.mp4")
out_dir = Path("D:/Projects/Vibe/Project_K_build/tmp/demo_eval_out")
out_dir.mkdir(parents=True, exist_ok=True)
jobs_dir = Path("D:/Projects/Vibe/Project_K_build/jobs")

def analyze_job(job_dir: Path, is_sentence: bool):
    print(f"\n--- Analyzing {job_dir.name} (sentence={is_sentence}) ---")
    
    # 1. Read transcript and units/translated
    trans_path = job_dir / "transcript.json"
    transcript = json.loads(trans_path.read_text(encoding="utf-8")) if trans_path.exists() else []
    
    units_file = (job_dir / "dub_units.json") if (job_dir / "dub_units.json").exists() else (job_dir / "units.json")
    units = json.loads(units_file.read_text(encoding="utf-8")) if units_file.exists() else []
    
    trans_file = job_dir / "translated.json"
    translated = json.loads(trans_file.read_text(encoding="utf-8")) if trans_file.exists() else []
    
    dur_file = job_dir / "durations.json"
    durations = json.loads(dur_file.read_text(encoding="utf-8")) if dur_file.exists() else []
    
    sched_file = job_dir / "schedule.json"
    schedule = json.loads(sched_file.read_text(encoding="utf-8")) if sched_file.exists() else []
    
    srt_file = job_dir / "output.vi.srt"
    cues = []
    if srt_file.exists():
        import re
        content = srt_file.read_text(encoding="utf-8")
        # simple srt parser
        blocks = content.strip().split("\n\n")
        for b in blocks:
            lines = b.splitlines()
            if len(lines) >= 3 and "-->" in lines[1]:
                m = re.match(r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)", lines[1])
                if m:
                    def to_s(ts):
                        h, mm, rest = ts.split(":")
                        s, ms = rest.split(",")
                        return int(h)*3600 + int(mm)*60 + int(s) + int(ms)/1000.0
                    start_s = to_s(m.group(1))
                    end_s = to_s(m.group(2))
                    text = " ".join(lines[2:])
                    cues.append({"start": start_s, "end": end_s, "dur": end_s - start_s, "text": text})

    # Metrics
    # Number of TTS requests
    tts_requests = len(durations) if durations else len(units if is_sentence else translated)
    
    # Non-sentence ending TTS texts
    non_sentence_texts = 0
    items = units if is_sentence else translated
    for item in items:
        txt = (item.get("vi") or item.get("text") or "").strip()
        if txt and not txt.endswith(('.', '!', '?', '…', ':')):
            non_sentence_texts += 1

    # fit_status counts
    fit_counts = {}
    for d in durations:
        st = d.get("fit_status", "unknown")
        fit_counts[st] = fit_counts.get(st, 0) + 1

    # Shifted and overlapping clips, max shift, lag at end
    shifted_count = 0
    overlaps_count = 0
    max_shift = 0.0
    for i, s in enumerate(schedule):
        shift = abs(s.get("start", 0) - s.get("target_start", s.get("start", 0)))
        if shift > max_shift:
            max_shift = shift
        if shift > 0.05:
            shifted_count += 1
        if i > 0 and schedule[i-1].get("end", 0) > s.get("start", 0) + 0.01:
            overlaps_count += 1
            
    video_end = transcript[-1]["end"] if transcript else 0.0
    last_clip_end = schedule[-1]["end"] if schedule else 0.0
    end_lag = max(0.0, last_clip_end - video_end)

    cue_count = len(cues)
    longest_cue = max((c["dur"] for c in cues), default=0.0)

    print(f"TTS requests: {tts_requests}")
    print(f"TTS non-sentence-ending texts: {non_sentence_texts}")
    print(f"fit_status counts: {fit_counts}")
    print(f"Shifted clips (>0.05s): {shifted_count}")
    print(f"Overlapping clips: {overlaps_count}")
    print(f"Maximum shift: {max_shift:.2f}s")
    print(f"End of video lag: {end_lag:.2f}s")
    print(f"Subtitle cues count: {cue_count}")
    print(f"Longest subtitle cue: {longest_cue:.2f}s")

    return {
        "job_id": job_dir.name,
        "is_sentence": is_sentence,
        "tts_requests": tts_requests,
        "non_sentence_texts": non_sentence_texts,
        "fit_counts": fit_counts,
        "shifted_count": shifted_count,
        "overlaps_count": overlaps_count,
        "max_shift": round(max_shift, 2),
        "end_lag": round(end_lag, 2),
        "cue_count": cue_count,
        "longest_cue": round(longest_cue, 2),
        "items": items,
    }

def run():
    print("Running Demo Video Evaluation...")
    cancel = threading.Event()
    def p(e):
        if e.state in ("start", "done", "error"):
            print(f"  {e.stage}: {e.state} ({e.done or 0}/{e.total or 0}) {e.message or ''}", flush=True)

    # 1. Sentence mode, balanced
    print("\n[Run 1: Sentence mode, balanced]")
    opt_sentence = DubOptions(model="gemini-3.5-flash-lite")
    res_s_b = api.run_dub(video_path, out_dir, options=opt_sentence, secrets=secrets, jobs_dir=jobs_dir,
                          progress=p, cancel=cancel,
                          extra_overrides={"dub": {"granularity": "sentence"}, "translate": {"priority": "balanced"}})
    print("Run 1 OK:", res_s_b.ok, "job:", res_s_b.job_id)
    data_s_b = analyze_job(jobs_dir / res_s_b.job_id, is_sentence=True)

    # 2. Segment mode, balanced
    print("\n[Run 2: Segment mode, balanced]")
    opt_segment = DubOptions(model="gemini-3.5-flash-lite")
    res_seg_b = api.run_dub(video_path, out_dir, options=opt_segment, secrets=secrets, jobs_dir=jobs_dir,
                           progress=p, cancel=cancel,
                           extra_overrides={"dub": {"granularity": "segment"}, "translate": {"priority": "balanced"}})
    print("Run 2 OK:", res_seg_b.ok, "job:", res_seg_b.job_id)
    data_seg_b = analyze_job(jobs_dir / res_seg_b.job_id, is_sentence=False)

    # 3. Sentence mode, faithful
    print("\n[Run 3: Sentence mode, faithful]")
    opt_faithful = DubOptions(model="gemini-3.5-flash-lite")
    res_s_f = api.run_dub(video_path, out_dir, options=opt_faithful, secrets=secrets, jobs_dir=jobs_dir,
                          progress=p, cancel=cancel,
                          extra_overrides={"dub": {"granularity": "sentence"}, "translate": {"priority": "faithful"}})
    print("Run 3 OK:", res_s_f.ok, "job:", res_s_f.job_id)
    data_s_f = analyze_job(jobs_dir / res_s_f.job_id, is_sentence=True)

    # Save summary json
    summary = {
        "sentence_balanced": data_s_b,
        "segment_balanced": data_seg_b,
        "sentence_faithful": data_s_f,
    }
    (out_dir / "demo_eval_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nSummary saved to", out_dir / "demo_eval_summary.json")

if __name__ == "__main__":
    run()
