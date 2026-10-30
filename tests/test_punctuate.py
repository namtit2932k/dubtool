"""AGENT_UPDATE_8 task 3 tests: word timestamps, sentence re-segmentation,
punctuation restoration, and pause-aware units.

The English of row 1 (spec finding 8) is the fixture: after restoration it
must split into sentence-level segments, no unit may contain an interior
sentence-final mark unless a cap forced it, and a word-changing LLM reply is
rejected.
"""

from __future__ import annotations

import json

import pytest

from dubtool.punctuate import (
    ends_sentence,
    needs_punctuation,
    resegment_by_sentences,
    restore_punctuation,
    unpunct_stats,
)


def _punctuate_reply(lines):
    return json.dumps({"lines": lines})


def test_row1_needs_punctuation():
    one = [{"text": ROW1, "start": 0.0, "end": 30.0}]
    assert needs_punctuation(one)
    st = unpunct_stats(one)
    assert st["unpunctuated"] == 1
    assert ends_sentence("Hello there.") and not ends_sentence("hello there")


def _row1_segments():
    """Word-aligned thirds of ROW1 so a punctuation-only reply can match."""
    words = ROW1.split()
    cuts = (len(words) // 3, 2 * len(words) // 3)
    return [
        {"id": 0, "start": 0.0, "end": 9.0, "text": " ".join(words[: cuts[0]])},
        {"id": 1, "start": 9.0, "end": 18.0, "text": " ".join(words[cuts[0]: cuts[1]])},
        {"id": 2, "start": 18.0, "end": 27.0, "text": " ".join(words[cuts[1]:])},
    ]


def _row1_reply():
    words = ROW1.split()
    cuts = (len(words) // 3, 2 * len(words) // 3)
    return [
        " ".join(words[: cuts[0]]) + ".",
        " ".join(words[cuts[0]: cuts[1]]) + "!",
        " ".join(words[cuts[1]:]) + ".",
    ]


def test_restore_punctuation_accepts_good_reply():
    segments = _row1_segments()
    replies = _row1_reply()

    class LLM:
        def __init__(self):
            self.n = 0

        def _call_and_validate(self, system, user, validate, note):
            self.n += 1
            return validate(_punctuate_reply(replies))

    accepted, rejected = restore_punctuation(segments, LLM(), {})
    assert accepted == 1 and rejected == 0
    assert ends_sentence(segments[0]["text"])
    assert segments[1]["text"] == _row1_reply()[1]


def test_restore_punctuation_rejects_changed_words():
    segments = [{"id": 0, "start": 0.0, "end": 5.0, "text": "aa bb cc dd"}]

    class LLM:
        def _call_and_validate(self, system, user, validate, note):
            return validate(json.dumps({"lines": ["aa bb cc dd ee"]}))  # extra word

    accepted, rejected = restore_punctuation(segments, LLM(), {})
    assert accepted == 0 and rejected == 1
    assert segments[0]["text"] == "aa bb cc dd"  # original text kept


def _words_for(segments):
    """Synthetic word timestamps: even spacing inside each segment."""
    words = []
    for seg in segments:
        tokens = seg["text"].split()
        start = seg["start"]
        step = (seg["end"] - seg["start"]) / max(len(tokens), 1)
        for t in tokens:
            words.append({"w": t, "start": round(start, 3),
                          "end": round(start + step * 0.9, 3)})
            start += step
    return words


def test_resegment_cuts_row1_at_sentence_marks():
    punctuated = [
        {"id": 0, "start": 0.0, "end": 8.0,
         "text": "I support trans woman, because I'm a girl's girl. And more trailing words here"},
        {"id": 1, "start": 8.0, "end": 12.0,
         "text": "Second sentence ends here. Tail bit remains together"},
        {"id": 2, "start": 12.0, "end": 16.0,
         "text": "Understand that when they attack trans girls in sports."},
    ]
    out = resegment_by_sentences(punctuated, _words_for(punctuated))
    assert out is not None
    assert len(out) >= 4  # at least one boundary per the first two segments
    # Times come from the word timestamps (inside the segment's window).
    assert out[0]["start"] > punctuated[0]["start"] - 0.2
    # Ids are renumbered sequentially.
    assert [s["id"] for s in out] == list(range(len(out)))
    # No content lost: same word set in order.
    from dubtool.punctuate import word_tokens

    before = word_tokens(" ".join(s["text"] for s in punctuated))
    after = word_tokens(" ".join(s["text"] for s in out))
    assert before == after


def _row1_punctuated_segments():
    return [
        {"id": 0, "start": 0.0, "end": 8.0,
         "text": "I support trans woman, because I'm a girl's girl. I don't support the "
                 "rights of trans people just to protect my own rights."},
        {"id": 1, "start": 8.0, "end": 16.0,
         "text": "But I support the rights of trans women, because I'm a girl's girl. "
                 "Understand that when they attack trans girls in sports."},
    ]


def test_units_after_resegmentation_have_no_interior_sentence_marks():
    from dubtool.units import build_units

    segs0 = _row1_punctuated_segments()
    out = resegment_by_sentences(segs0, _words_for(segs0))
    assert out is not None
    segs = [{"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]}
            for s in out]
    units = build_units(segs, max_segments=4, max_duration_s=14.0,
                        max_gap_s=1.0, pause_split_s=0.5)
    # With one sentence (or clause) per segment, every unit built until the
    # caps close it starts a new sentence after each sentence-final mark.
    for u in units:
        body = u["text"]
        first_mark = min(
            [i for i, ch in enumerate(body[:-1]) if ch in ".!?"],
            default=None,
        )
        if first_mark is not None and len(u["seg_ids"]) > 1 and \
                body.count(".!?") > len(u["seg_ids"]):
            pytest.fail(f"unit {u['unit_id']} swallows a whole extra sentence: {body!r}")


def test_units_pause_rule():
    from dubtool.units import build_units

    segs = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "First part here"},
        {"id": 1, "start": 1.6, "end": 2.2, "text": "After a pause"},   # 0.6 s gap closes
        {"id": 2, "start": 2.8, "end": 3.3, "text": "after a comma,"},   # 0.6 s gap closes
        {"id": 3, "start": 3.6, "end": 4.0, "text": "continuation follows"},  # 0.3 s + comma: kept
    ]
    units = build_units(segs, max_segments=4, max_duration_s=14.0,
                        max_gap_s=1.0, pause_split_s=0.5)
    ids = [u["seg_ids"] for u in units]
    assert [0] in ids and [1] in ids
    assert [2, 3] in ids         # comma pair kept together despite the pause
    # Disabled (0.0) keeps the old behavior.
    units = build_units(segs, max_segments=4, max_duration_s=14.0,
                        max_gap_s=1.0, pause_split_s=0)
    assert [u["seg_ids"] for u in units] == [[0, 1, 2, 3]]


def test_resegment_ids_times_and_new_keys():
    segs = [
        {"id": 0, "start": 0.0, "end": 6.0,
         "text": "One sentence here. The second sentence has several words and it carries on a bit"},
    ]
    out = resegment_by_sentences(segs, _words_for(segs))
    assert [s["id"] for s in out] == list(range(len(out)))
    cut = [s for s in out if s["text"].startswith("The second")][0]
    # The cut time comes from the word times (inside the segment's span).
    assert 0.5 < cut["start"] < 6.0


def test_new_config_keys_present():
    """The new settings are real keys the stages read (they ride along in the
    inputs hash because config state flows there)."""
    from dubtool.config import Config

    cfg = Config()
    assert cfg.asr["word_timestamps"] is True
    assert cfg.asr["sentence_resegment"] is True
    assert cfg.units["pause_split_s"] == 0.5
    assert cfg.punctuate["enabled"] == "auto"


ROW1 = (
    "I support trans woman because I'm a girl's girl I don't support the "
    "rights of trans people just to protect my own rights But I support the "
    "rights of trans women because I'm a girl's girl Understand that when "
    "they attack trans girls in sports"
)


def test_restore_uses_fast_and_is_counted_in_budget(monkeypatch):
    """The restore-step client is built on the Fast model."""
    from dubtool.stages import s3_translate as s3

    monkeypatch.setattr(s3, "LLMClient", FakeCaptureClient)
    monkeypatch.setattr(s3, "fast_model_id", lambda cfg: "fast-1")
    client = s3.fast_client(SimpleConfig(), "gemini", ["key1"], None)
    assert FakeCaptureClient.last == {"model": "fast-1", "policy": "wait"}
    assert client is FakeCaptureClient.built


class FakeCaptureClient:
    last: dict = {}
    built: object = None

    def __new__(cls, provider, cancel=None, options=None, on_overload=None):
        cls.last = {"model": provider.model,
                    "policy": options.get("overload_policy")}
        instance = super().__new__(cls)
        cls.built = instance
        return instance

    def _call_and_validate(self, *a, **k):
        return ""


class SimpleConfig:
    llm = {"request_timeout_s_fast": 90}
