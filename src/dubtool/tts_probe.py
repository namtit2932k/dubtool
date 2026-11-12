"""The honest, interleaved voice-request experiment (Update 12 task 5).

All three rate conditions (one request per second, per 3 s, per 10 s) run
INTERLEAVED per text in randomized order inside the same time window, so
they share the service's state. A short CONTROL sentence fires every 10
requests to measure that state regardless of any condition. Conclusions
come only from the data a run collected; the experiment cannot show a
service-side outage that would affect everything equally.
"""

from __future__ import annotations

import json
import random
import tempfile
import time
from pathlib import Path

CONTROL_TEXT = "Đây là câu điều khiển ngắn để đo trạng thái dịch vụ."
CONDITIONS: dict[str, float] = {"1/s": 1.0, "1/3s": 3.0, "1/10s": 10.0}
DEFAULT_SEED = 20261104


def build_texts(count: int) -> list[str]:
    """At least `count` real Vietnamese texts (the recorded refused texts
    first, then generated ones)."""
    fixture = Path("tests/fixtures/tts_failing_texts.json")
    texts: list[str] = []
    try:
        rows = json.loads(fixture.read_text(encoding="utf-8"))
        texts = [str(r.get("vi") or "").strip() for r in rows]
    except Exception:
        texts = []
    texts = [t for t in texts if t]
    filler = (
        "Đoạn văn bình thường số {n}: dùng để đo nhịp yêu cầu thích hợp "
        "cho dịch vụ giọng đọc trong chương trình."
    )
    while len(texts) < count:
        texts.append(filler.format(n=len(texts) + 1))
    return texts[:count]


def _classify(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "noaudioreceived" in low:
        return "no_audio"
    if "503" in text and ("handshake" in low or "invalid response" in low):
        return "handshake_503"
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "429" in text:
        return "rate_429"
    return "other"


def _blank_counts() -> dict:
    keys = ("requests", "ok", "no_audio", "handshake_503", "timeout", "other")
    return {k: 0 for k in keys}


def run_experiment(
    config, out_path=None, requests: int = 40, seed: int = DEFAULT_SEED,
    cancel=None, emit=print, provider=None, texts=None,
) -> dict:
    """Run the interleaved experiment and return the collected data.

    `provider` and `texts` exist for tests (a fake provider, fixed texts).
    With the real provider each synthesized file is deleted immediately —
    acceptance only, no storage.
    """
    owns_provider = provider is None
    if provider is None:
        from dubtool.tts.edge import EdgeTTSProvider

        provider = EdgeTTSProvider(
            request_timeout_s=float(
                (getattr(config, "tts", {}) or {}).get("request_timeout_s", 45.0)
            ),
            rate_limit_s=0.0,  # the experiment paces itself
        )
    voice = str(getattr(config, "tts_voice", "vi-VN-HoaiMyNeural"))
    rng = random.Random(seed)
    last: dict[str, float | None] = {k: None for k in CONDITIONS}
    counts = {k: _blank_counts() for k in CONDITIONS}
    controls = _blank_counts()
    tmp_dir = Path(tempfile.mkdtemp(prefix="dubtool_probe_"))
    out_file = tmp_dir / "probe.mp3"

    def fire(text: str, tally: dict) -> None:
        tally["requests"] += 1
        try:
            provider.synthesize(text, voice, out_file)
            tally["ok"] += 1
        except Exception as exc:  # any failure is data, never fatal
            tally[_classify(exc)] += 1
            emit(f"    failure: {type(exc).__name__}: {exc}"[:180])
        finally:
            out_file.unlink(missing_ok=True)

    if texts is None:
        texts = build_texts(requests)
    emit(
        f"voice request experiment: {len(texts)} texts x {len(CONDITIONS)} "
        f"rate conditions (1/s, 1/3s, 1/10s), seed {seed}, voice {voice}"
    )
    try:
        for index, text in enumerate(texts, start=1):
            if index % 5 == 0 or index == 1:
                emit(f"progress: text {index}/{len(texts)}")
            for key in rng.sample(list(CONDITIONS), k=len(CONDITIONS)):
                interval = CONDITIONS[key]
                prev = last[key]
                if prev is not None:
                    now = time.monotonic()
                    if now < prev + interval:
                        _sleep(prev + interval - now)
                last[key] = time.monotonic()
                fire(text, counts[key])
            if cancel is not None and cancel.is_set():
                break
            if index % 10 == 0:
                fire(CONTROL_TEXT, controls)  # every 10th request: the control
    finally:
        if owns_provider and hasattr(provider, "close"):
            try:
                provider.close()
            except Exception:
                pass

    emit("")
    emit("results (interleaved, same time window):")
    emit("rate     requests  ok  no_audio  handshake_503  timeout  other")
    for key in CONDITIONS:
        c = counts[key]
        emit(
            f"{key:<8} {c['requests']:>8} {c['ok']:>3} {c['no_audio']:>8} "
            f"{c['handshake_503']:>13} {c['timeout']:>7} {c['other']:>5}"
        )
    c = controls
    emit(
        f"{'control':<8} {c['requests']:>8} {c['ok']:>3} {c['no_audio']:>8} "
        f"{c['handshake_503']:>13} {c['timeout']:>7} {c['other']:>5}"
    )
    data = {
        "seed": seed,
        "texts": len(texts) if texts else 0,
        "conditions": counts,
        "controls": controls,
        "samples_used": CONTROL_TEXT,
    }
    if out_path:
        Path(out_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        emit(f"results saved to {out_path}")
    return data


def _sleep(seconds: float) -> None:
    """Bounded real-time wait (the experiment is a long honest run)."""
    time.sleep(max(0.0, seconds))
