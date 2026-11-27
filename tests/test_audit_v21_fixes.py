"""Regression tests for the Update 21 ponytail-audit & Windows UIA fixes."""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest


def test_settings_fresh_install_keeps_light_retention(tmp_path: Path):
    """Fresh install (missing settings.json) must default to keep_level='light',
    while legacy settings with keep_intermediate=False still migrate to 'none'."""
    from dubtool.app import settings as settings_mod

    fresh = settings_mod.load(tmp_path / "nonexistent.json")
    assert fresh["keep_level"] == "light"

    legacy_file = tmp_path / "legacy.json"
    legacy_file.write_text('{"keep_intermediate": false}', encoding="utf-8")
    migrated = settings_mod.load(legacy_file)
    assert migrated["keep_level"] == "none"


def test_settings_validate_rejects_protected_jobs_dir():
    from dubtool.app import settings as settings_mod

    s = dict(settings_mod.DEFAULTS)
    s["jobs_dir"] = str(Path.home())
    problems = settings_mod.validate(s)
    assert any("jobs_dir" in p for p in problems)


def test_housekeeping_skips_non_job_subfolders_and_protected_dirs(tmp_path: Path):
    from dubtool.app import housekeeping as hk

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    unrelated = jobs / "FamilyPhotos"
    unrelated.mkdir()
    (unrelated / "vacation.jpg").write_bytes(b"img" * 200)
    old_t = 1_500_000_000.0
    os.utime(unrelated, (old_t, old_t))
    os.utime(unrelated / "vacation.jpg", (old_t, old_t))

    deleted, reclaimed = hk.prune_jobs(jobs, days=1, all_jobs=True, now=1_800_000_000.0)
    assert deleted == []
    assert reclaimed == 0
    assert (unrelated / "vacation.jpg").exists()

    # Protected directory (user home) is refused outright.
    del_home, rec_home = hk.prune_jobs(Path.home(), days=1, all_jobs=True)
    assert del_home == [] and rec_home == 0


def test_failguard_and_diagnostics_redact_all_provider_keys(tmp_path: Path, monkeypatch, caplog):
    from dubtool.app import diagnostics, failguard, paths

    # Construct fake key-shaped strings dynamically so static review-zip scans stay clean.
    fake_anthropic = "sk-" + "ant-" + "A" * 24
    fake_openai = "sk-" + "proj-" + "B" * 24
    fake_groq = "gsk" + "_" + "C" * 24
    fake_aq = "AQ" + "." + "D" * 24

    sample = (
        f"err1={fake_anthropic} err2={fake_openai} err3={fake_groq} err4={fake_aq} "
        "GPT_API_KEY=secretval1 CLAUDE_API_KEY=secretval2"
    )
    scrubbed = diagnostics.scrub_line(sample)
    for secret in (fake_anthropic, fake_openai, fake_groq, fake_aq, "secretval1", "secretval2"):
        assert secret not in scrubbed

    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(failguard, "_show_dialog", lambda _e: None)
    failguard._REPORTED.clear()
    with caplog.at_level(logging.ERROR, logger="dubtool"):
        failguard.handle_slot_exception(RuntimeError(f"boom {fake_anthropic} {fake_groq}"))
    assert fake_anthropic not in caplog.text
    assert fake_groq not in caplog.text


def test_keypool_wait_for_key_fails_fast_and_honors_short_retry_after():
    from dubtool.llm.keypool import KeyPool

    now = [1000.0]
    pool = KeyPool(["k1", "k2"], cooldown_s=30.0, clock=lambda: now[0])
    # Short 4s TPM hint must NOT be inflated to 30s.
    pool.mark_rate_limited("k1", 4.0)
    assert 3.5 <= pool.earliest_cooldown() <= 4.1

    # Invalid + daily mix must fail fast instead of spinning at 100% CPU.
    pool2 = KeyPool(["k1", "k2"], cooldown_s=30.0, clock=lambda: now[0])
    pool2.mark_invalid("k1")
    pool2.mark_daily_limit("k2", until_epoch=2000.0)
    assert pool2.wait_for_key() is False


def test_llm_client_catches_key_exhausted_and_uses_thread_local_units():
    from dubtool.llm.base import KeyExhaustedError, LLMResult
    from dubtool.llm.client import LLMClient

    waited = [0]
    calls = [0]

    class _PoolProvider:
        name = "gemini"
        model = "gemini-3.5-flash-lite"
        structured = "none"
        timeout_s = 30.0
        pool = object()

        def complete(self, system, user, *, schema=None, json_mode=True, max_tokens=8000):
            calls[0] += 1
            if calls[0] == 1:
                raise KeyExhaustedError("all keys cooling")
            return LLMResult(text="ok", input_tokens=1, output_tokens=1)

        def wait_for_key(self) -> bool:
            waited[0] += 1
            return True

        def raise_exhausted(self) -> None:
            raise KeyExhaustedError("exhausted")

        def handle_rate_limit(self, exc, used_key):
            return "wait"

        def handle_invalid_key(self, used_key):
            return "exhausted"

    client = LLMClient(_PoolProvider(), options={})
    assert client._call("sys", "usr") == "ok"
    assert waited[0] == 1

    # Thread-local request_units isolation across concurrent workers.
    seen: dict[str, int | None] = {}
    barrier = threading.Barrier(2, timeout=5.0)

    def _worker(name: str, units: int):
        client.request_units = units
        barrier.wait()
        seen[name] = client.request_units

    t1 = threading.Thread(target=_worker, args=("a", 2))
    t2 = threading.Thread(target=_worker, args=("b", 20))
    t1.start()
    t2.start()
    t1.join(5)
    t2.join(5)
    assert seen == {"a": 2, "b": 20}


def test_quota_state_next_pacific_midnight_within_24h_during_vn_morning():
    from dubtool.llm.quota_state import next_pacific_midnight

    # 2026-04-02 02:00 UTC = 09:00 ICT = 2026-04-01 19:00 PDT.
    # Next Pacific midnight is 2026-04-02 00:00 PDT = 2026-04-02 07:00 UTC (5h away).
    now_ts = dt.datetime(2026, 4, 2, 2, 0, 0, tzinfo=dt.timezone.utc).timestamp()
    nxt = next_pacific_midnight(now_ts)
    delta_h = (nxt - now_ts) / 3600.0
    assert 4.5 <= delta_h <= 5.5


def test_parsing_rejects_null_id_and_duplicate_shorten_ids():
    from dubtool.llm.base import LLMError
    from dubtool.llm.parsing import parse_batch_shortened, parse_json_array

    with pytest.raises(LLMError):
        parse_json_array('[{"id": null, "vi": "Xin chào."}]', [0])
    with pytest.raises(LLMError):
        parse_batch_shortened('[{"id": 0, "vi": "A"}, {"id": 0, "vi": "B"}]', [0])


def test_proc_run_enforces_timeout_and_input_with_cancel():
    import subprocess
    from dubtool import proc

    cancel = threading.Event()
    res = proc.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().strip().upper())"],
        input="hello\n",
        cancel=cancel,
        timeout=10.0,
    )
    assert res.stdout.strip() == "HELLO"

    with pytest.raises(subprocess.TimeoutExpired):
        proc.run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cancel=cancel,
            timeout=0.4,
        )


@pytest.mark.gui
def test_settings_dialog_quota_badge_mismatch_and_fillers_and_uia_names(qtbot, monkeypatch, tmp_path):
    from dubtool.app import secrets as sec_mod
    from dubtool.app import settings as settings_mod
    from dubtool.app.ui import fillers_dialog as fd
    from dubtool.app.ui import main_window as mw
    from dubtool.app.ui import settings_dialog as sd
    from dubtool.llm import quota_state

    settings_file = tmp_path / "settings.json"
    settings_mod.save({"model_id": "auto-rotate"}, settings_file)
    monkeypatch.setattr(settings_mod, "path", lambda: settings_file)
    monkeypatch.setattr(quota_state, "state_path", lambda: tmp_path / "quota_state.json")

    key_val = "fake-gemini-quota-key-9999"
    quota_state.mark_daily(key_val, "gemini-3.5-flash-lite", time.time() + 3600.0)
    monkeypatch.setattr(sec_mod, "get_keys", lambda p="gemini": [key_val] if p == "gemini" else [])
    monkeypatch.setattr(sec_mod, "env_key_sources", lambda p="gemini": [])

    # 1. Opening SettingsDialog with an active daily quota must NOT crash with AttributeError.
    dlg = sd.SettingsDialog()
    qtbot.addWidget(dlg)
    assert dlg.key_list.count() == 1
    assert "⏳" in dlg.key_list.item(0).text()
    assert "gemini-3.5-flash-lite" in dlg.key_list.item(0).toolTip()

    # 2. FillersDialog must reveal QLineEdit when switching to ACTION_TRANSLATE.
    fdlg = fd.FillersDialog([{"token": "bro", "count": 5}])
    qtbot.addWidget(fdlg)
    fdlg.show()
    _tok, combo, target = fdlg._rows[0]
    assert not target.isVisible()
    idx = combo.findData(fd.ACTION_TRANSLATE)
    combo.setCurrentIndex(idx)
    assert target.isVisible()

    # 3. MainWindow options controls must have unique accessibleName (and unique
    # objectName on every combo box so Windows UIA has no ambiguous twins).
    win = mw.MainWindow()
    qtbot.addWidget(win)
    combos = [
        win.options.keep_combo,
        win.options.readable_format_combo,
        win.options.voice_combo,
        win.lang_video_combo,
    ]
    obj_names = [c.objectName() for c in combos]
    assert all(obj_names) and len(set(obj_names)) == len(combos)
    controls = [*combos, win.options.gain_slider]
    acc_names = [c.accessibleName() for c in controls]
    assert all(acc_names) and len(set(acc_names)) == len(controls)
