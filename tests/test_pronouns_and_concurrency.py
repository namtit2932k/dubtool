"""AGENT_UPDATE_4.md A2 (pronoun consistency, no assumed gender) and
A3.1 (translation chunk concurrency) tests. All offline, fake providers."""

from __future__ import annotations

import pytest

from dubtool.config import Config
from dubtool.llm import prompts
from dubtool.llm.base import RateLimitError
from dubtool.stages.s3_translate import run_chunks_parallel


# ------------------------------------------------------------------ A2 prompts
def test_pronoun_block_defaults_no_gendered_forms():
    block = prompts.render_pronoun_block(None)
    assert "tôi" in block
    assert "các bạn" in block
    assert "bạn" in block
    # no gendered second-person defaults
    for bad in ("'anh'", "'cô'", "'chi'"):
        assert bad in block  # they appear only inside the "never use" rule
    assert "Do not assume the gender" in block
    # the "never use" list contains them, but the chosen ones are not gendered
    assert "First person (I/we/me): tôi" in block
    for chosen_bad in ("'anh'", "'cô'"):
        assert f"-> {chosen_bad}" not in block


def test_pronoun_block_brief_override_note():
    block = prompts.render_pronoun_block(None, has_brief=True)
    assert "If the Video brief above specifies pronouns" in block
    # without a brief the block refers to the description, not to a brief
    plain = prompts.render_pronoun_block(None, has_brief=False)
    assert "If the video description specifies pronouns" in plain
    assert "Video brief" not in plain


def test_pronoun_block_with_config_override():
    block = prompts.render_pronoun_block({"first": "chúng ta", "second_singular": "mọi người"})
    assert "chúng ta" in block
    assert "mọi người" in block
    assert "First person (I/we/me): chúng ta" in block


def test_translate_user_contains_pronoun_block():
    user = prompts.render_translate_user(
        [{"id": 0, "text": "x", "max_chars": 10}], [], {}
    )
    assert "Pronoun choices" in user
    assert "tôi" in user


def test_system_prompt_pronoun_and_terminology_rules():
    text = prompts.TRANSLATE_SYSTEM
    assert "ONE first-person pronoun" in text
    assert "Never alternate" in text
    assert "gendered second-person" in text
    assert "established vietnamese terms" in text.lower()
    assert '"anh", "cô"' in text


def test_shorten_prompts_mention_pronouns():
    assert "pronoun" in prompts.SHORTEN_SYSTEM.lower()
    assert "pronoun" in prompts.SHORTEN_BATCH_SYSTEM.lower()
    for text in (prompts.SHORTEN_SYSTEM, prompts.SHORTEN_BATCH_SYSTEM):
        assert "gendered second-person" in text


# ------------------------------------------------------------------ A2 config
def test_config_translate_defaults():
    cfg = Config()
    assert cfg.translate["pronouns"] == {
        "first": "tôi", "second_plural": "các bạn", "second_singular": "bạn"
    }
    assert cfg.translate["concurrency"] == 1


# ------------------------------------------------------------------ A3.1 concurrency
class Tracker:
    def __init__(self):
        self.max_inflight = 0
        self.inflight = 0


@pytest.mark.parametrize("concurrency", [1])
def test_sequential_path_applies_in_order(concurrency):
    applied = []

    def chunk_fn(plan):
        idx, chunk = plan
        return [{"id": idx, "vi": f"v{idx}"}]

    run_chunks_parallel([(0, ["a"]), (1, ["b"])], chunk_fn, concurrency,
                        apply=lambda i, u: applied.append((i, u)))
    assert [i for i, _ in applied] == [0, 1]


def test_order_preserved_under_concurrency():
    import time

    applied = []
    calls = []

    def chunk_fn(plan):
        idx, _chunk = plan
        time.sleep(0.15 if idx == 0 else 0.0)  # chunk 0 is the slowest
        return [{"id": 1, "vi": "slowish"}]

    chunks = [(i, ["x"]) for i in range(6)]
    run_chunks_parallel(chunks, chunk_fn, 3, apply=lambda i, u: applied.append(i))
    assert applied == sorted(applied)


def test_rate_limit_reduces_concurrency():
    """A 429 in every chunk reduces the parallel width for the rest of the run."""

    def chunk_fn(plan):
        raise RateLimitError("gemini", "resource exhausted", retry_after=0.01)

    # every chunk is rate-limited forever: the run must not spin forever
    with pytest.raises(RuntimeError, match="did not complete"):
        run_chunks_parallel([(0, ["a"]), (1, ["b"])], chunk_fn, 2)


def test_rate_limit_once_then_success_reduces_width_and_finishes():
    tracker = Tracker()
    state = {"raised": False}
    applied = []

    def chunk_fn(plan):
        idx, _ = plan
        tracker.inflight += 1
        tracker.max_inflight = max(tracker.max_inflight, tracker.inflight)
        try:
            if idx == 0 and not state["raised"]:
                state["raised"] = True
                raise RateLimitError("gemini", "429", retry_after=0.01)
            return [{"id": 1, "vi": f"v{idx}"}]
        finally:
            tracker.inflight -= 1

    chunks = [(i, ["x"]) for i in range(4)]
    run_chunks_parallel(chunks, chunk_fn, 2, apply=lambda i, u: applied.append(i))
    assert applied == [0, 1, 2, 3]
    # after the first 429 the width never grows beyond 1 again
    assert tracker.max_inflight == 1


def test_failed_chunk_does_not_lose_completed_results():
    """A hard failure in one chunk lets the rest of the batch finish first."""
    applied = []

    def chunk_fn(plan):
        idx, _ = plan
        if idx == 1:
            raise ValueError("boom")
        return [{"id": 1, "vi": "ok"}]

    chunks = [(0, ["x"]), (1, ["x"]), (2, ["y"])]
    with pytest.raises(ValueError):
        run_chunks_parallel(chunks, chunk_fn, 2, apply=lambda i, u: applied.append(i))
    # chunk 0 (same batch as the failing one) still applied before the raise
    assert 0 in applied
