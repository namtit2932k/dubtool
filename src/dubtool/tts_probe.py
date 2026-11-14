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


def classify_exact(exc: Exception) -> str:
    """Classify a TTS exception by exact kind name (never an 'other' bucket)."""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "noaudioreceived" in low:
        return "NoAudioReceived"
    if "503" in text and ("handshake" in low or "invalid response" in low or "wsserverhandshakeerror" in low):
        return "handshake_503"
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "429" in text:
        return "http_429"
    return type(exc).__name__


def run_ab_slice(
    out_path: str | Path,
    *,
    start_idx: int = 0,
    count: int = 10,
    seed: int = DEFAULT_SEED,
    voice: str = "vi-VN-HoaiMyNeural",
) -> dict:
    """Run an interleaved slice of `count` texts comparing Condition A (Update 14)
    and Condition B (Update 12 baseline), plus a control request every 10 requests.
    """
    from concurrent.futures import ThreadPoolExecutor
    import sys
    from dubtool.tts.edge import EdgeTTSProvider

    all_texts = build_texts(max(60, start_idx + count))
    slice_texts = all_texts[start_idx : start_idx + count]
    rng = random.Random(seed + start_idx)
    tmp_dir = Path(tempfile.mkdtemp(prefix="dubtool_ab_probe_"))

    provider_a = EdgeTTSProvider(request_timeout_s=45.0, rate_limit_s=0.0)
    provider_b = EdgeTTSProvider(request_timeout_s=45.0, rate_limit_s=0.0)
    provider_ctrl = EdgeTTSProvider(request_timeout_s=45.0, rate_limit_s=0.0)

    def _new_bucket() -> dict:
        return {
            "requests": 0,
            "ok": 0,
            "handshake_503": 0,
            "NoAudioReceived": 0,
            "timeout": 0,
            "failures_by_kind": {},
            "elapsed_s": [],
            "retried_ok": 0,
            "breaker_pauses": 0,
        }

    stats_a = _new_bucket()
    stats_b = _new_bucket()
    stats_ctrl = _new_bucket()

    consec_fail_a = 0
    consec_fail_b = 0
    breaker_opens_a = 0
    interval_a = 1.0 / 1.5  # max_rps: 1.5
    interval_b = 1.0 / 1.0  # max_rps: 1.0

    def _call_one(prov, text: str, tag: str, direct_retries: int) -> tuple[bool, str | None, float, bool]:
        t0 = time.monotonic()
        used_retry = False
        for attempt in range(direct_retries + 1):
            out_file = tmp_dir / f"probe_{tag}_{threading.get_ident()}_{attempt}.mp3"
            try:
                prov.synthesize(text, voice, out_file)
                return True, None, time.monotonic() - t0, used_retry
            except Exception as exc:
                kind = classify_exact(exc)
                if attempt < direct_retries and kind == "NoAudioReceived":
                    used_retry = True
                    time.sleep(0.4)
                    continue
                return False, kind, time.monotonic() - t0, used_retry
            finally:
                out_file.unlink(missing_ok=True)
        return False, "NoAudioReceived", time.monotonic() - t0, used_retry

    import threading

    total_condition_reqs = 0
    try:
        # Process texts in pairs so Condition A can exercise concurrency=2 on each pair
        for offset in range(0, len(slice_texts), 2):
            pair = slice_texts[offset : offset + 2]
            order = ["A", "B"]
            rng.shuffle(order)
            for cond in order:
                if cond == "A":
                    # Condition A: concurrency=2, max_rps=1.5, direct_retries=1, breaker_consecutive=6, schedule=[10,20,30]
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futs = []
                        for p_i, txt in enumerate(pair):
                            if p_i > 0:
                                time.sleep(interval_a)
                            futs.append(pool.submit(_call_one, provider_a, txt, f"A_{offset}_{p_i}", 1))
                        for fut in futs:
                            ok, kind, el, retried = fut.result()
                            stats_a["requests"] += 1
                            stats_a["elapsed_s"].append(round(el, 3))
                            if retried and ok:
                                stats_a["retried_ok"] += 1
                            if ok:
                                stats_a["ok"] += 1
                                consec_fail_a = 0
                            else:
                                consec_fail_a += 1
                                if kind in ("handshake_503", "NoAudioReceived", "timeout"):
                                    stats_a[kind] += 1
                                stats_a["failures_by_kind"][kind] = stats_a["failures_by_kind"].get(kind, 0) + 1
                                if kind == "handshake_503":
                                    interval_a = max(interval_a, 2.5)  # floor 2.5s
                                if consec_fail_a >= 6:
                                    sched = [10, 20, 30]
                                    pause = sched[min(breaker_opens_a, len(sched) - 1)]
                                    breaker_opens_a += 1
                                    consec_fail_a = 0
                                    stats_a["breaker_pauses"] += 1
                                    time.sleep(pause)
                            total_condition_reqs += 1
                            if total_condition_reqs % 10 == 0:
                                ok_c, kind_c, el_c, _ = _call_one(provider_ctrl, CONTROL_TEXT, f"ctrl_{total_condition_reqs}", 0)
                                stats_ctrl["requests"] += 1
                                stats_ctrl["elapsed_s"].append(round(el_c, 3))
                                if ok_c:
                                    stats_ctrl["ok"] += 1
                                else:
                                    if kind_c in ("handshake_503", "NoAudioReceived", "timeout"):
                                        stats_ctrl[kind_c] += 1
                                    stats_ctrl["failures_by_kind"][kind_c] = stats_ctrl["failures_by_kind"].get(kind_c, 0) + 1
                else:
                    # Condition B: concurrency=1, max_rps=1.0, direct_retries=0, breaker_consecutive=3, flat 60s
                    for p_i, txt in enumerate(pair):
                        time.sleep(interval_b)
                        ok, kind, el, _ = _call_one(provider_b, txt, f"B_{offset}_{p_i}", 0)
                        stats_b["requests"] += 1
                        stats_b["elapsed_s"].append(round(el, 3))
                        if ok:
                            stats_b["ok"] += 1
                            consec_fail_b = 0
                        else:
                            consec_fail_b += 1
                            if kind in ("handshake_503", "NoAudioReceived", "timeout"):
                                stats_b[kind] += 1
                            stats_b["failures_by_kind"][kind] = stats_b["failures_by_kind"].get(kind, 0) + 1
                            if kind == "handshake_503":
                                interval_b = max(interval_b, 5.0)  # floor 5.0s
                            if consec_fail_b >= 3:
                                consec_fail_b = 0
                                stats_b["breaker_pauses"] += 1
                                time.sleep(60.0)
                        total_condition_reqs += 1
                        if total_condition_reqs % 10 == 0:
                            ok_c, kind_c, el_c, _ = _call_one(provider_ctrl, CONTROL_TEXT, f"ctrl_{total_condition_reqs}", 0)
                            stats_ctrl["requests"] += 1
                            stats_ctrl["elapsed_s"].append(round(el_c, 3))
                            if ok_c:
                                stats_ctrl["ok"] += 1
                            else:
                                if kind_c in ("handshake_503", "NoAudioReceived", "timeout"):
                                    stats_ctrl[kind_c] += 1
                                stats_ctrl["failures_by_kind"][kind_c] = stats_ctrl["failures_by_kind"].get(kind_c, 0) + 1
    finally:
        for prov in (provider_a, provider_b, provider_ctrl):
            if hasattr(prov, "close"):
                try:
                    prov.close()
                except Exception:
                    pass
        import shutil as _sh
        _sh.rmtree(tmp_dir, ignore_errors=True)

    payload = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "start_idx": start_idx,
        "count": len(slice_texts),
        "voice": voice,
        "A": stats_a,
        "B": stats_b,
        "control": stats_ctrl,
    }
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload

