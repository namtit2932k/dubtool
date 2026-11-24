"""Tests for AutoRotateProvider (`auto-rotate`) and its UI/catalog wiring."""

from __future__ import annotations

import os
import types

import pytest

from dubtool.llm import catalog as catalog_mod
from dubtool.llm import get_provider
from dubtool.llm.auto_rotate import (
    AUTO_ROTATE_MODEL_ID,
    AutoRotateProvider,
    _build_interleaved_slots,
)
from dubtool.llm.base import (
    InvalidKeyError,
    KeyExhaustedError,
    LLMResult,
    MissingKeyError,
    RateLimitError,
    RequestTooLargeError,
    ServerErrorLLMError,
)


class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _FakeInner:
    def __init__(self, provider: str, model: str, key: str, handler):
        self.name = provider
        self.model = model
        self.key = key
        self.structured = "json_schema" if provider == "gemini" else "json_object"
        self._handler = handler
        self.pool = None

    def complete(self, system, user, *, schema=None, json_mode=True, max_tokens=8000):
        return self._handler(self.provider_tuple, schema, max_tokens)

    @property
    def provider_tuple(self):
        return (self.name, self.model, self.key)


def test_catalog_contains_auto_rotate_and_preserves_default():
    info = catalog_mod.get(AUTO_ROTATE_MODEL_ID)
    assert info is not None
    assert info.provider == "auto"
    assert info.paid is False
    assert info.translate_concurrency == 2
    assert info.max_output_tokens == 8000
    assert catalog_mod.DEFAULT_MODEL_ID == AUTO_ROTATE_MODEL_ID
    assert "auto" not in catalog_mod.PAID_PROVIDERS
    groups = dict(catalog_mod.grouped_order())
    assert [e.id for e in groups["auto"]] == [AUTO_ROTATE_MODEL_ID]


def test_build_interleaved_slots_spreads_providers_and_tiers():
    slots = _build_interleaved_slots(
        gemini_keys=["AIzaG1", "AIzaG2"],
        groq_keys=["gsk_Q1", "gsk_Q2"],
    )
    t1 = [s for s in slots if s.tier == 1]
    t2 = [s for s in slots if s.tier == 2]
    # 2 Gemini keys x 3 Gemini Tier-1 models = 6, + 2 Groq keys x 1 Groq Tier-1 model = 2 -> 8
    assert len(t1) == 8
    # First 4 Tier-1 slots alternate Gemini and Groq 1:1
    assert [s.provider for s in t1[:4]] == ["gemini", "groq", "gemini", "groq"]
    # Pool stays strictly on fast non-thinking Tier-1 models (no gpt-oss, no 3.6/3.8 thinking models)
    assert t2 == []
    assert all("gpt-oss" not in s.model for s in slots)
    assert all(s.model not in ("gemini-3.6-flash", "gemini-3.8-flash") for s in slots)


def test_auto_rotate_adaptive_503_cooldown_ladder():
    clock = _FakeClock(start=1000.0)
    prov = AutoRotateProvider(
        api_keys={"gemini": ["AIzaK1", "AIzaK2"]},
        clock=clock,
        provider_factory=lambda p, m, k: _FakeInner(p, m, k, lambda *a: LLMResult(text="ok")),
    )
    slot = next(s for s in prov._slots if s.model == "gemini-3.5-flash-lite")

    # 1st 503 -> 15s
    assert prov._mark_model_503(slot) == 15.0
    # Concurrent 503 from a 2nd thread while still cooling -> stays 15s (no double bump)
    clock.advance(2.0)
    assert prov._mark_model_503(slot) == 15.0
    # After 15s expires, 2nd wave 503 -> 30s
    clock.advance(14.0)
    assert prov._mark_model_503(slot) == 30.0
    # After 30s expires, 3rd wave 503 -> 45s
    clock.advance(31.0)
    assert prov._mark_model_503(slot) == 45.0
    # Any success resets streak back to 15s
    prov._clear_model_503_streak(slot)
    clock.advance(46.0)
    assert prov._mark_model_503(slot) == 15.0


def test_auto_rotate_round_robins_across_gemini_and_groq():
    calls: list[tuple[str, str, str]] = []
    clock = _FakeClock()

    def handler(pt, schema, max_tokens):
        calls.append(pt)
        return LLMResult(text='{"items":[]}')

    prov = AutoRotateProvider(
        api_keys={
            "gemini": ["AIzaKey1", "AIzaKey2"],
            "groq": ["gsk_Key1"],
            "openai": ["sk-paid1"],
            "anthropic": ["sk-ant-paid1"],
        },
        clock=clock,
        provider_factory=lambda p, m, k: _FakeInner(p, m, k, handler),
    )
    # Never includes paid providers
    assert all(s.provider in ("gemini", "groq") for s in prov._slots)
    assert prov.model == AUTO_ROTATE_MODEL_ID

    for _ in range(3):
        prov.complete("sys", "usr")

    assert [c[0] for c in calls] == ["gemini", "groq", "gemini"]
    # Distinct Gemini keys used across the first two Gemini calls
    gemini_keys_used = [c[2] for c in calls if c[0] == "gemini"]
    assert len(set(gemini_keys_used)) == 2


def test_auto_rotate_503_cools_model_and_rotates_immediately():
    calls: list[tuple[str, str, str]] = []
    clock = _FakeClock()

    def handler(pt, schema, max_tokens):
        calls.append(pt)
        if pt[0] == "gemini" and pt[1] == "gemini-3.5-flash-lite":
            raise ServerErrorLLMError("503 unavailable", status=503)
        return LLMResult(text="ok")

    prov = AutoRotateProvider(
        api_keys={"gemini": ["AIzaK1", "AIzaK2"], "groq": ["gsk_K1"]},
        clock=clock,
        provider_factory=lambda p, m, k: _FakeInner(p, m, k, handler),
    )
    res = prov.complete("sys", "usr")
    assert res.text == "ok"
    assert calls[0] == ("gemini", "gemini-3.5-flash-lite", "AIzaK1")
    # Immediately rotated to Groq without sleeping
    assert calls[1][0] == "groq"
    assert prov.switches == 1

    # Next calls skip gemini-3.5-flash-lite on ALL keys while cooling down (45s)
    calls.clear()
    for _ in range(3):
        prov.complete("sys", "usr")
    assert all(not (c[0] == "gemini" and c[1] == "gemini-3.5-flash-lite") for c in calls)


def test_auto_rotate_429_cools_only_one_slot_and_rotates():
    calls: list[tuple[str, str, str]] = []
    clock = _FakeClock()

    def handler(pt, schema, max_tokens):
        calls.append(pt)
        if pt == ("groq", "qwen/qwen3.8-27b", "gsk_1"):
            raise RateLimitError("groq", detail="tpm", kind="minute", retry_after=20.0)
        return LLMResult(text="ok")

    prov = AutoRotateProvider(
        api_keys={"groq": ["gsk_1", "gsk_2"]},
        clock=clock,
        provider_factory=lambda p, m, k: _FakeInner(p, m, k, handler),
    )
    res = prov.complete("sys", "usr")
    assert res.text == "ok"
    assert calls == [
        ("groq", "qwen/qwen3.8-27b", "gsk_1"),
        ("groq", "qwen/qwen3.8-27b", "gsk_2"),
    ]


def test_auto_rotate_401_disables_key_across_all_models():
    calls: list[tuple[str, str, str]] = []
    clock = _FakeClock()

    def handler(pt, schema, max_tokens):
        calls.append(pt)
        if pt[2] == "AIzaBad":
            raise InvalidKeyError("invalid api key")
        return LLMResult(text="ok")

    prov = AutoRotateProvider(
        api_keys={"gemini": ["AIzaBad", "AIzaGood"]},
        clock=clock,
        provider_factory=lambda p, m, k: _FakeInner(p, m, k, handler),
    )
    res = prov.complete("sys", "usr")
    assert res.text == "ok"
    # Every slot with AIzaBad is permanently marked dead
    assert all(s.dead for s in prov._slots if s.key == "AIzaBad")
    assert all(not s.dead for s in prov._slots if s.key == "AIzaGood")


def test_auto_rotate_propagates_request_too_large_from_gemini_and_hands_off_from_groq():
    # From Gemini (8,000-token cap), RequestTooLargeError propagates to split the chunk.
    def handler_all_large(pt, schema, max_tokens):
        raise RequestTooLargeError("too large", status=400)

    prov = AutoRotateProvider(
        api_keys={"gemini": ["AIzaGood"], "groq": ["gsk_Good"]},
        provider_factory=lambda p, m, k: _FakeInner(p, m, k, handler_all_large),
    )
    with pytest.raises(RequestTooLargeError):
        prov.complete("sys", "usr")

    # From Groq (950-token cap), if a Gemini slot is ready, hand off cleanly without splitting!
    calls: list[tuple[str, str, str]] = []

    def handler_groq_large(pt, schema, max_tokens):
        calls.append(pt)
        if pt[0] == "groq":
            raise RequestTooLargeError("groq length", status=400)
        return LLMResult(text="ok-from-gemini")

    prov2 = AutoRotateProvider(
        api_keys={"gemini": ["AIzaGood"], "groq": ["gsk_Good"]},
        provider_factory=lambda p, m, k: _FakeInner(p, m, k, handler_groq_large),
    )
    prov2._cursor_t1 = 1  # start on Groq slot
    res = prov2.complete("sys", "usr")
    assert res.text == "ok-from-gemini"
    assert calls[0][0] == "groq"
    assert calls[1][0] == "gemini"


def test_auto_rotate_handles_persisted_daily_key_exhausted():
    class _FakeDailyPool:
        def all_daily_exhausted(self):
            return True

    def factory(p, m, k):
        def handler(pt, schema, max_tokens):
            if pt == ("gemini", "gemini-3.5-flash-lite", "AIza1"):
                raise KeyExhaustedError("daily quota persisted")
            return LLMResult(text="ok")

        inner = _FakeInner(p, m, k, handler)
        if p == "gemini" and m == "gemini-3.5-flash-lite" and k == "AIza1":
            inner.pool = _FakeDailyPool()
        return inner

    prov = AutoRotateProvider(
        api_keys={"gemini": ["AIza1"], "groq": ["gsk_1"]},
        provider_factory=factory,
    )
    res = prov.complete("sys", "usr")
    assert res.text == "ok"
    dead_slots = [s for s in prov._slots if s.dead]
    assert len(dead_slots) == 1
    assert dead_slots[0].model == "gemini-3.5-flash-lite"


def test_auto_rotate_refuses_paid_only_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(MissingKeyError):
        AutoRotateProvider(api_keys={"openai": ["sk-paid"], "anthropic": ["sk-ant-paid"]})


def test_get_provider_builds_auto_rotate_with_full_key_dict():
    cfg = types.SimpleNamespace(llm_provider="auto", llm={"auto": {"model": AUTO_ROTATE_MODEL_ID}})
    prov = get_provider(
        cfg,
        provider_id="auto",
        api_keys={"gemini": ["AIzaTest1"], "groq": ["gsk_Test1"], "openai": ["sk-paid"]},
    )
    assert isinstance(prov, AutoRotateProvider)
    assert prov.name == "auto"
    assert prov.model == AUTO_ROTATE_MODEL_ID
    assert prov._n_gemini == 1
    assert prov._n_groq == 1


def test_secrets_has_key_auto(monkeypatch):
    from dubtool.app import secrets

    stores = {"gemini": [], "groq": [], "openai": ["sk-paid"]}
    monkeypatch.setattr(secrets, "get_keys", lambda p="gemini": list(stores.get(secrets.normalize_provider(p), [])))
    assert secrets.has_key("auto") is False
    stores["groq"] = ["gsk_1"]
    assert secrets.has_key("auto") is True
    stores["groq"] = []
    stores["gemini"] = ["AIza1"]
    assert secrets.has_key("auto") is True


@pytest.mark.gui
def test_ui_shows_auto_rotate_in_main_combo_and_excludes_from_fallback(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from dubtool.app import secrets
    from dubtool.app.ui.main_window import MainWindow
    from dubtool.app.ui.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    assert app is not None

    monkeypatch.setattr(
        secrets,
        "has_key",
        lambda p="gemini": secrets.normalize_provider(p) in ("auto", "gemini", "groq"),
    )
    monkeypatch.setattr(secrets, "get_keys", lambda p="gemini": ["AIza1"] if p == "gemini" else ["gsk_1"] if p == "groq" else [])
    monkeypatch.setattr(secrets, "env_key_sources", lambda p="gemini": [])

    win = MainWindow()
    try:
        ids = []
        auto_enabled = None
        for i in range(win.model_combo.count()):
            item = win.model_combo.model().item(i)
            mid = item.data(Qt.ItemDataRole.UserRole)
            if mid:
                ids.append(mid)
                if mid == AUTO_ROTATE_MODEL_ID:
                    auto_enabled = item.isEnabled()
        assert AUTO_ROTATE_MODEL_ID in ids
        assert ids[0] == AUTO_ROTATE_MODEL_ID
        assert auto_enabled is True
    finally:
        win.close()

    dlg = SettingsDialog()
    try:
        fb_ids = [dlg.fallback_combo.itemData(i) for i in range(dlg.fallback_combo.count())]
        assert AUTO_ROTATE_MODEL_ID not in fb_ids
    finally:
        dlg.close()
