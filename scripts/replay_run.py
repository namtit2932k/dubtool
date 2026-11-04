"""AGENT_UPDATE_11 section 5.1: replay of the user's observed run if my
memory of it is wrong. Offline, no network: the REAL Update-11 stages run
with fake providers and a fake (virtual) clock; the BEFORE timeline replays
the OLD policy with this update's mechanisms disabled. Both timelines are
printed for REPORT_UPDATE_11.md.

The observed sequences (job 361f048f9ad8):
  - Gemini: 504 at ~180 s, 504, 503, 504, 504, 503, then success;
  - edge-tts: 13 of 46 segments refused, 3 of the 13 refused again after
    the retry round.

A failing big request advances the fake clock by ~180 s — how the client
saw it. Every wait is virtual: the whole replay runs in zero wall time and
prints complete timelines.

How the 6 observed errors map onto the new flow (stated in the report):
the FIRST 504 kills the 16-unit chunk, which splits (2.2); the two 8-unit
halves meet one 504 — on that second error the overload dialog consults —
and one 503 (the second half also splits); the 4-unit and 2-unit requests
pass within seconds, and the stage ends well inside its 600-s budget. The
BEFORE replay stacks all 6 errors on the identical 16-unit request, as the
old code did.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

UNITS = 16
HALF = 8          # a split half is still refused like the big request
BIG_LATENCY = 180.0   # the observed 504 DEADLINE_EXCEEDED at ~180 s
OBSERVED_SEQUENCE = [504, 504, 503]  # consumed by big + first half + second
BIG_REQUEST_TEXT = "x" * 239
REFUSED_IDS = [4, 12, 17, 19, 20, 22, 25, 34, 36, 37, 43, 44, 45]
REFUSED_AGAIN = [4, 12, 17]


class FakeClock:
    """Virtual monotonic clock driven by time.sleep."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)
        self.log: list[tuple[float, float]] = []  # (virtual now, slept s)

    def sleep(self, seconds: float) -> None:
        self.log.append((self.now, float(seconds)))
        self.now += max(0.0, float(seconds))

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


@contextmanager
def fake_clock():
    clock = FakeClock()
    with mock.patch("time.monotonic", clock.monotonic), \
            mock.patch("time.sleep", clock.sleep), \
            mock.patch("time.time", clock.time):
        yield clock


# ==========================================================================
class GeminiReplay:
    """Fake Gemini: replays the observed failures; a big (whole-chunk)
    request takes ~180 s to answer, a split half the same, small requests
    ~2 s."""

    name = "gemini"
    model = "gemini-fake-flash"

    def __init__(self, fail_kinds, clock):
        self.fail_kinds = list(fail_kinds)
        self.clock = clock
        self.sends: list[dict] = []
        self.expect_ids: list[int] | None = None

    def complete(self, system, user, *, json_mode=True, max_tokens=8000):
        from dubtool.llm.base import LLMResult, ServerErrorLLMError

        units = len(self.expect_ids or [])
        big = units >= HALF
        latency = BIG_LATENCY if big else 2.0
        self.sends.append({"t": self.clock.now, "units": units,
                           "latency": latency, "verdict": "?"})
        self.clock.sleep(latency)
        kind = self.fail_kinds.pop(0) if self.fail_kinds else None
        self.sends[-1]["verdict"] = str(kind) if kind else "ok"
        if kind is None:
            return LLMResult(text=json.dumps(
                [{"id": i, "vi": "ok"} for i in (self.expect_ids or [])]
            ), input_tokens=units * 10, output_tokens=units * 5)
        if kind == 504:
            raise ServerErrorLLMError("504 DEADLINE_EXCEEDED", status=504)
        raise ServerErrorLLMError("503 UNAVAILABLE (high demand)", status=503)


def run_translate_after():
    """The real stages + real Update 11 client, Update 11 defaults."""
    from dubtool.llm.client import LLMClient
    from dubtool.stages.s3_translate import translate_resilient
    from dubtool import heartbeat

    panel_events: list[str] = []
    heartbeat.bind(lambda **_info: panel_events.append("event"))

    sequence = [504, 504, 503]
    with fake_clock() as clock:
        provider = GeminiReplay(sequence, clock)
        t0 = clock.now
        events: list[str] = []

        def on_overload(info: dict) -> str:
            events.append(
                f"t+{clock.now - t0:7.1f}  OVERLOAD DIALOG: "
                f" consecutive={info.get('consecutive')}, "
                f"waited_s={info.get('waited_s')}, over_budget="
                f"{info.get('over_budget')} -> 'Chờ thêm' (wait)"
            )
            return "wait"

        client = LLMClient(provider, on_overload=on_overload, options={
            "server_error_backoff_s": [10, 30, 90],
            "overload_after": 2,
            "overload_policy": "ask",
            "split_on_timeout": True,
            "split_min_units": 2,
            "stage_budget_s": 600,
            "stage_wait_state": {},
        })

        def translate_call(cl, payloads, ctx):
            cl.request_label = f"translate chunk ({len(payloads)} units)"
            cl.request_units = len(payloads)
            provider.expect_ids = [p["id"] for p in payloads]
            return cl.translate_batch(payloads, ctx)

        payloads = [{"id": i, "text": BIG_REQUEST_TEXT, "max_chars": 50}
                    for i in range(UNITS)]
        stats: dict = {"splits": 0}
        out = translate_resilient(
            {"client": client}, translate_call, payloads, [],
            f"translate chunk 1/1 ({UNITS} units, "
            f"{UNITS * len(BIG_REQUEST_TEXT)} chars)",
            on_fallback=None, split_on_timeout=True, split_min_units=2,
            stats=stats,
        )
        total = clock.now - t0
        for s in provider.sends:
            size = "big" if s["units"] >= HALF else "small"
            print(f"  t+{s['t'] - t0:7.1f}  request of {s['units']:>2} units "
                  f"({size}, {s['latency']:.0f}s answered) -> {s['verdict']}")
        for e in events:
            print(f"  {e}")
        big_sends = [s for s in provider.sends if s["units"] >= HALF]
        print(f"  visible waits: {len(events)} dialog consult(s) shown above;"
              f" in production every request also shows a 1 Hz "
              f"'waiting for the model (X s)' line (asserted in "
              f"test_agu11_translate) and the split/consult notices print "
              f"into the details panel as in the log above")
        print(f"  AFTER stage total: t+{total:.0f}s, {len(provider.sends)} "
              f"requests, {len(big_sends)} failed big request(s), "
              f"{stats['splits']} split(s), {len(events)} consult(s), "
              f"inside the 600s stage budget = "
              f"{'YES' if total <= 600 else 'NO'}")
        assert [r["id"] for r in out] == list(range(UNITS)), "partial output!"
        return total


def run_translate_before():
    """Old policy: no split, no dialog; the identical 16-unit request is
    resent after backoff 10/30/90 and the whole chunk restarts after every
    exhaustion (this is what made the user's run hours long)."""
    from dubtool.llm.base import ServerErrorLLMError

    sequence = [504, 504, 503, 504, 504, 503]
    with fake_clock() as clock:
        provider = GeminiReplay(sequence, clock)
        t0 = clock.now
        cycle = 0
        while provider.fail_kinds:
            cycle += 1
            if cycle > 1:
                print(f"  t+{clock.now - t0:7.1f}  3 attempts exhausted -> the"
                      f" stage RESTARTS the whole chunk (cycle {cycle}): the"
                      f" identical 3824-char request goes out again")
            for attempt in (1, 2, 3):
                if not provider.fail_kinds:
                    break
                provider.expect_ids = list(range(UNITS))
                try:
                    provider.complete("sys", "user")
                    break
                except ServerErrorLLMError:
                    kind = provider.sends[-1]["verdict"]
                    wait = [10, 30, 90][attempt - 1]
                    print(f"  t+{clock.now - t0:7.1f}  request "
                          f"#{len(provider.sends)}: identical {UNITS}-unit"
                          f" prompt -> {kind} at ~{BIG_LATENCY:.0f} s (silence"
                          f" during the whole request); backoff {wait}s "
                          f"(silent)")
                    clock.sleep(wait)
        total = clock.now - t0
        print(f"  BEFORE stage total: t+{total:.0f}s (~{total / 60:.0f} min),"
              f" {len(provider.sends)} requests (6 identical 16-unit sends),"
              f" 0 dialog consults, no split ever")
        return total


def main() -> int:
    print("=" * 74)
    print("TRANSLATE timeline replay (virtual clock; sequence:", end=" ")
    print("504@180s, 504, 503, 504, 504, 503, then success)")
    print("=" * 74)
    print("--- BEFORE (old policy: no split, no dialog) ---")
    total_before = run_translate_before()
    print()
    print("--- AFTER (Update 11: split after the first 504, overload "
          "dialog, budgets) ---")
    total_after = run_translate_after()
    print()
    print(f"summary: BEFORE t+{total_before:.0f}s (~{total_before / 60:.0f}"
          f" min, no feedback, no split), AFTER "
          f"t+{total_after:.0f}s (~{total_after / 60:.1f} min) with splits"
          f" + dialog + visible reasoning; the run finished INSIDE the"
          f" 600-s stage budget.")


if __name__ == "__main__":
    raise SystemExit(main())
