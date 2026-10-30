"""AGENT_UPDATE_8 task 4 tests: the filler/vocative detector, per-video
suggestions, actions, the prompt policy and the suggestions dialog.

Offline: no LLM, no network, no key reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dubtool import fillers
from dubtool.llm import prompts
from dubtool.stages.s3_translate import _filter_glossary, _words_in_range


SETTINGS = fillers.load_settings()


# ------------------------------------------------------------------ detector

def test_vocative_girl_with_comma():
    spans = fillers.tag_fillers("Girl, I support trans women because I care.", settings=SETTINGS)
    assert [(s["kind"], s["text"].lower()) for s in spans] == [("vocative", "girl")]


def test_noun_girls_girl_not_tagged():
    spans = fillers.tag_fillers("I'm a girl's girl and I mean it.", settings=SETTINGS)
    assert spans == []


def test_trans_girls_not_tagged():
    assert fillers.tag_fillers("I support the rights of trans girls in sports.", settings=SETTINGS) == []


def test_the_girls_not_tagged():
    assert fillers.tag_fillers("the girls showed up at the party.", settings=SETTINGS) == []


def test_like_as_filler_vs_verb():
    filler = fillers.tag_fillers("like, what do you mean?", settings=SETTINGS)
    assert [("discourse", "like")] == [(s["kind"], s["text"].lower()) for s in filler]
    assert fillers.tag_fillers("I like pizza more than I should.", settings=SETTINGS) == []


def test_vocative_after_pause_needs_word_times():
    # "Girl I don't support…" spoken with a 0.4 s pause after "Girl".
    words = [
        {"w": "Girl", "start": 0.0, "end": 0.3},
        {"w": "I", "start": 0.7, "end": 0.8},
        {"w": "don't", "start": 0.8, "end": 1.1},
    ]
    spans = fillers.tag_fillers("Girl I don't support that.", words, settings=SETTINGS)
    assert [s["kind"] for s in spans] == ["vocative"]


def test_no_pause_no_tag():
    words = [
        {"w": "Girl", "start": 0.0, "end": 0.3},
        {"w": "I", "start": 0.31, "end": 0.4},
    ]
    spans = fillers.tag_fillers("Girl I don't support that.", words, settings=SETTINGS)
    assert spans == []


def test_multiword_discourse_keys():
    spans = fillers.tag_fillers("okay so thanks for watching, you know.", settings=SETTINGS)
    texts = {s["text"].lower() for s in spans}
    assert {"okay so", "you know"} <= texts


def test_translate_as_action_rendered():
    spans = fillers.tag_fillers("dude, that was wild.", settings=SETTINGS)
    assert [s["action"] for s in spans] == ["drop"]
    policy = fillers.render_policy(None, {"girl": "translate_as: chị em ơi"}, SETTINGS)
    assert "chị em ơi" in policy


# ------------------------------------------------- per-video suggestions 5.3

def _seg(i: int, text: str, t0: float) -> dict:
    words = [
        {"w": w, "start": t0 + 0.1 * k, "end": t0 + 0.1 * k + 0.08}
        for k, w in enumerate(text.split())
    ]
    return {"id": i, "start": t0, "end": t0 + 1.2, "text": text, "words": words}


def test_suggestions_count_threshold_and_stoplist():
    segs = [_seg(i, "girl, I support that today.", i * 2.0) for i in range(10)]
    segs.append(_seg(10, "The cat sat here.", 20.0))
    out = fillers.suggest_candidates(segs)
    tokens = [s["token"] for s in out]
    assert "girl" in tokens          # 10 sentence-initial commas
    assert "the" not in tokens       # stop list
    # threshold: max(5, 3% of 11)=5 -> girl (10) in, sparse tokens out
    assert all(s["count"] >= 5 for s in out)


def test_suggestions_below_threshold_excluded(tmp_path):
    segs = [_seg(i, "girl, hi.", i * 2.0) for i in range(3)]
    assert fillers.suggest_candidates(segs) == []


def test_resolve_actions_user_choices_win():
    sug = [{"token": "girl", "count": 9}, {"token": "like", "count": 6}]
    actions = fillers.resolve_actions(sug, SETTINGS, {"girl": "translate_as: chị em ơi"})
    assert actions["girl"] == "translate_as: chị em ơi"
    assert actions["like"] == "drop"


def test_per_video_choices_roundtrip(monkeypatch, tmp_path):
    from dubtool.app import paths

    monkeypatch.setattr(paths, "settings_dir", lambda: tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    assert fillers.load_video_choices(video) == {}
    choices = {"girl": "translate_as: chị em ơi"}
    assert fillers.save_video_choices(video, choices)
    assert fillers.load_video_choices(video) == choices
    # a same-name different-size file gets a different key
    video.write_bytes(b"fakes")
    assert fillers.load_video_choices(video) == {}


# ------------------------------------------------------------------- policy

def test_filter_glossary_only_chunk_terms():
    glossary = {
        "_comment": "x",
        "subscribe": "đăng ký",
        "AI": "keep original",
        "planet earth": "Trái Đất",
    }
    out = _filter_glossary(glossary, ["Please subscribe and like the AI content."])
    assert out == {"subscribe": "đăng ký", "AI": "keep original"}


def test_filter_glossary_returns_none_and_gaps():
    assert _filter_glossary(None, ["x"]) is None
    assert _filter_glossary({}, ["x"]) == {}


def test_translate_user_policy_block_present():
    user = prompts.render_translate_user(
        [{"id": 1, "text": "Girl, hi.", "max_chars": 30}], [], {},
        policy="Filler policy: drop girl",
    )
    assert "Filler policy: drop girl" in user
    assert "Fragments:" in user
    assert user.index("Filler policy") < user.index("Fragments:")
    without = prompts.render_translate_user(
        [{"id": 1, "text": "hi", "max_chars": 30}], [], {}
    )
    assert "Filler policy" not in without


def test_shorten_batch_policy():
    out = prompts.render_shorten_batch_user(
        [{"id": 1, "text": "a", "vi": "b", "max_chars": 5}], policy="POL-BLOCK"
    )
    assert "POL-BLOCK" in out


def test_words_in_range_midpoints():
    words = [
        {"w": "a", "start": 0.0, "end": 1.0},
        {"w": "b", "start": 2.0, "end": 3.0},
        {"w": "c", "start": 4.0, "end": 5.0},
    ]
    got = [w["w"] for w in _words_in_range(words, 1.0, 3.5)]
    assert got == ["b"]


# ----------------------------------------------------------------- 4b wiring

def test_stage3_wiring_smoke(tmp_path, monkeypatch):
    """_filler_state: suggestions file written, dialog NEVER asked for
    non-interactive runs, saved choices win over the dialog."""
    import dubtool.app.paths as paths
    from dubtool.app import settings as settings_mod
    from dubtool.stages.s3_translate import _filler_state

    monkeypatch.setattr(paths, "settings_dir", lambda: tmp_path)
    monkeypatch.setattr(settings_mod, "path", lambda: tmp_path / "settings.json")

    class Job:
        class dir:  # noqa: N801
            pass

    job = Job()
    job.dir = tmp_path
    words = []
    transcript = []
    for i in range(10):
        t0 = i * 2.0
        text = "girl, I support that today."
        ws = [
            {"w": w, "start": t0 + 0.1 * k, "end": t0 + 0.1 * k + 0.08}
            for k, w in enumerate(text.split())
        ]
        transcript.append({"id": i, "start": t0, "end": t0 + 1.2, "text": text})
        words.extend(ws)

    class FakeWords:
        job_dir = tmp_path

    monkeypatch.setattr(
        "dubtool.punctuate.read_words", lambda job: words, raising=False
    )

    class Config:
        fillers = {"ask": True}
        fillers_video = tmp_path / "v.mp4"
        llm_interactive = True
        # No dialog callback yet (task 4b says the CLI/headless path has none):
        fillers_dialog = None

    cfg = Config()
    state = _filler_state(job, cfg, transcript)
    assert state["suggestions"]
    assert state["actions"]["girl"] == "drop"      # shipped default, no ask
    assert json.loads((tmp_path / "suggested_fillers.json").read_text("utf-8"))

    # With a callable dialog the ask path runs and the choice is saved.
    cfg2 = Config()
    cfg2.fillers_dialog = lambda suggestions: {"girl": "translate_as: chị em ơi"}
    state2 = _filler_state(job, cfg2, transcript)
    assert state2["actions"]["girl"] == "translate_as: chị em ơi"
    saved = fillers.load_video_choices(tmp_path / "v.mp4")
    assert saved == {"girl": "translate_as: chị em ơi"}
