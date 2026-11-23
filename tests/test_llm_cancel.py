"""Regression tests for cancelling Stage 3 / LLMClient / punctuate during repeated 503 errors."""

from __future__ import annotations

import threading

import pytest

from dubtool import heartbeat, punctuate
from dubtool.job import StageCancelled
from dubtool.llm.base import ServerErrorLLMError
from dubtool.llm.client import LLMClient
from dubtool.stages import s3_translate


class _Always503Provider:
    name = "gemini"
    model = "gemini-3.6-flash"

    def __init__(self, *, cancel_on_call: int | None = None, cancel: threading.Event | None = None):
        self.calls = 0
        self._cancel_on_call = cancel_on_call
        self._cancel = cancel

    def complete(self, system, user, **kwargs):
        self.calls += 1
        if self._cancel_on_call == self.calls and self._cancel is not None:
            self._cancel.set()
        raise ServerErrorLLMError("gemini server error 503 UNAVAILABLE (high demand)", status=503)


def test_llm_client_cancel_aborts_503_retry_immediately() -> None:
    cancel = threading.Event()
    provider = _Always503Provider(cancel_on_call=1, cancel=cancel)
    client = LLMClient(
        provider,
        cancel=cancel,
        options={"backoff_s": [10.0, 30.0, 90.0], "overload_policy": "wait"},
    )
    with pytest.raises(StageCancelled):
        client._call("sys", "user")
    assert provider.calls == 1  # never retries attempt 2/3 after cancel


def test_llm_client_cancel_during_backoff_sleep_raises_stage_cancelled() -> None:
    cancel = threading.Event()
    provider = _Always503Provider()
    client = LLMClient(
        provider,
        cancel=cancel,
        options={"backoff_s": [30.0, 60.0], "overload_policy": "wait"},
    )
    token = heartbeat.bind(lambda _k, _m: None)
    try:
        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        try:
            with pytest.raises(StageCancelled):
                client._call("sys", "user")
        finally:
            timer.cancel()
    finally:
        heartbeat.reset(token)
    assert provider.calls == 1


def test_restore_punctuation_propagates_cancel_and_never_swallows_it() -> None:
    cancel = threading.Event()
    provider = _Always503Provider(cancel_on_call=1, cancel=cancel)
    client = LLMClient(
        provider,
        cancel=cancel,
        options={"backoff_s": [0.01, 0.01], "overload_policy": "wait"},
    )
    segments = [{"id": i, "start": float(i), "end": float(i + 1), "text": f"hello world {i}"} for i in range(60)]
    with pytest.raises(StageCancelled):
        punctuate.restore_punctuation(segments, client, {"window_segments": 10}, cancel=cancel)
    assert provider.calls == 1


def test_restore_punctuation_short_circuits_after_consecutive_503_windows() -> None:
    provider = _Always503Provider()
    client = LLMClient(
        provider,
        options={"backoff_s": [0.001, 0.001], "server_error_backoff_s": [0.001, 0.001], "overload_policy": "wait"},
    )
    segments = [{"id": i, "start": float(i), "end": float(i + 1), "text": f"hello world {i}"} for i in range(100)]
    accepted, rejected = punctuate.restore_punctuation(
        segments, client, {"window_segments": 10, "max_consecutive_server_errors": 2}
    )
    assert accepted == 0
    assert rejected == 10  # 2 attempted + 8 skipped
    # Only 2 windows attempted (2 attempts each = 4 calls), NOT all 10 windows (20 calls)!
    assert provider.calls == 4


def test_enforce_budget_propagates_stage_cancelled() -> None:
    class _CancelClient:
        def shorten_batch(self, items, policy=None):
            raise StageCancelled("cancelled")

    entries = [{"id": 1, "text": "hello", "vi": "x" * 100, "max_chars": 10}]
    with pytest.raises(StageCancelled):
        s3_translate.enforce_budget(entries, _CancelClient(), tolerance=1.15)


def test_progress_widget_cancel_button_shows_cancelling_feedback(qtbot, monkeypatch, tmp_path) -> None:
    from dubtool.app import i18n, settings
    from dubtool.app.ui.main_window import MainWindow

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    i18n.set_language("vi")
    win = MainWindow()
    qtbot.addWidget(win)
    win.progress_group.show_running()
    assert win.progress_group.cancel_btn.isEnabled()
    assert win.progress_group.cancel_btn.text() == i18n.tr("progress.cancel")

    called = {"flag": False}
    monkeypatch.setattr(win.service, "cancel", lambda: called.__setitem__("flag", True))
    win.progress_group.cancel_btn.click()

    assert called["flag"] is True
    assert not win.progress_group.cancel_btn.isEnabled()
    assert win.progress_group.cancel_btn.text() == i18n.tr("progress.cancelling")


def test_s2_transcribe_collect_stops_immediately_on_cancel() -> None:
    """Pressing Cancel during Whisper ASR stops on the next yielded segment."""
    from types import SimpleNamespace
    from dubtool.stages import s2_transcribe as s2

    cancel = threading.Event()
    yielded = 0

    def _gen():
        nonlocal yielded
        for i in range(100):
            yielded += 1
            if i == 2:
                cancel.set()
            yield SimpleNamespace(start=float(i), end=float(i + 1), text=f"seg {i}", words=[])

    with pytest.raises(StageCancelled):
        s2._collect(_gen(), cancel=cancel)
    assert yielded <= 3
