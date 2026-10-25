"""Unit tests for the stage-5 fit algorithm (AGENT.md section 9).

Pure-function tests: no ffmpeg, no LLM, no TTS. The materialization step is
exercised only where it does not need external binaries.
"""

from __future__ import annotations

import pytest

from dubtool.stages.s5_fit_duration import (
    compute_slot,
    final_status,
)


def test_compute_slot_uses_next_segment_start_when_present():
    assert compute_slot({"start": 1.0, "end": 2.0}, next_start=3.0) == 2.0


def test_compute_slot_uses_end_plus_slack_for_last_segment():
    assert compute_slot({"start": 1.0, "end": 2.0}, next_start=None) == 2.0  # +1.0s


class TestFinalStatus:
    def test_fits_exactly_is_ok(self):
        assert final_status(2.0, 2.0, 1.15, reviewed=False, attempts_used=0) == "ok"

    def test_unregenerated_fit_that_needed_shortening_is_shortened(self):
        assert final_status(1.9, 2.0, 1.15, reviewed=False, attempts_used=1) == "shortened"

    def test_within_stretch_budget_is_stretched(self):
        assert final_status(2.25, 2.0, 1.15, reviewed=False, attempts_used=0) == "stretched"

    def test_stretched_even_if_shortening_was_attempted(self):
        assert final_status(2.3, 2.0, 1.15, reviewed=False, attempts_used=2) == "stretched"

    def test_too_long_after_attempts_is_overflow(self):
        assert final_status(3.0, 2.0, 1.15, reviewed=False, attempts_used=3) == "overflow"

    def test_reviewed_never_shortened_but_can_stretch(self):
        assert final_status(2.2, 2.0, 1.15, reviewed=True, attempts_used=0) == "stretched"
        assert final_status(3.5, 2.0, 1.15, reviewed=True, attempts_used=0) == "overflow"

    def test_adjusted_slot_boundary(self):
        assert final_status(0.79, 0.8, 1.15, reviewed=False, attempts_used=0) == "ok"
        assert final_status(0.82, 0.8, 1.15, reviewed=False, attempts_used=0) == "stretched"
        assert final_status(0.95, 0.8, 1.15, reviewed=False, attempts_used=0) == "overflow"
