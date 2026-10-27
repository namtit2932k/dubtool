"""No-leak test (AGENT_UPDATE_5.md 2.5): after a mocked failing run with
three fake keys stored, the app-data folder (logs, settings.json, the
diagnostics zip), the process environment and any captured command line
must contain none of the values. The scan is quiet: assertions only, no
key value is ever printed by *this* test either.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

FAKE_KEYS = ["fake-key-alpha-111", "fake-key-beta-222", "fake-key-gamma-333"]


@pytest.fixture()
def secret_env(tmp_path, monkeypatch):
    """Isolated app data + in-memory ordered key store of three fake keys."""
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    (appdata / "jobs").mkdir()
    from dubtool.app import paths, secrets as secrets_mod

    stored = list(FAKE_KEYS)
    for attr, value in (
        ("jobs_dir", lambda: appdata / "jobs"),
        ("settings_dir", lambda: appdata),
        ("logs_dir", lambda: appdata / "logs"),
    ):
        if hasattr(paths, attr):
            monkeypatch.setattr(paths, attr, value, raising=False)
    monkeypatch.setattr(secrets_mod, "get_gemini_keys", lambda: list(stored))
    monkeypatch.setattr(secrets_mod, "get_gemini_key", lambda: stored[0] if stored else None)
    monkeypatch.setattr(secrets_mod, "has_key", lambda: bool(stored))
    if hasattr(secrets_mod, "key_source"):
        monkeypatch.setattr(secrets_mod, "key_source", lambda: "store")
    return appdata, stored


@pytest.fixture()
def no_processes(monkeypatch):
    """Record-and-refuse: no code under test may spawn a process (its
    command line is the classic leak channel). Returns captured lines."""
    captured: list[str] = []

    def refuse(*args, **kwargs):
        try:
            captured.append(subprocess.list2cmdline(args[0]) if args else str(args))
        except Exception:
            captured.append(str(args))
        raise AssertionError("test run must not spawn processes")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "check_output", refuse)
    return captured


def test_no_key_anywhere_after_mocked_failing_run(secret_env, no_processes):
    appdata, values = secret_env

    # ---- the mocked failing run: real client + real log machinery --------
    job = __import__("dubtool.job", fromlist=["Job"]).Job(appdata / "jobs" / "j1")
    job.ensure_dirs()
    logger = logging.getLogger("dubtool")
    handler = logging.FileHandler(job.log_path, encoding="utf-8")
    logger.addHandler(handler)
    try:
        from dubtool.llm.base import LLMError
        from dubtool.llm.client import LLMClient

        class FailingPoolProvider:
            """Fails fast (LLMError → 2s+4s backoff, then give-up) so the
            offline test stays quick while still exercising every retry/
            warning log path with the real client."""

            name = "gemini"
            model = "fake-model"

            def complete(self, system, user, *, json_mode=True, max_tokens=8000):
                raise LLMError("simulated provider failure")

        client = LLMClient(FailingPoolProvider())
        with pytest.raises(Exception):
            client.translate_batch(
                [{"id": 1, "text": "Hi", "max_chars": 5}], [], None
            )
    finally:
        logger.removeHandler(handler)
        handler.close()

    # settings.json snapshot (no secret fields even when keys exist)
    from dubtool.app import settings as settings_mod

    target = appdata / "settings.json"
    old_path = getattr(settings_mod, "path", None)
    settings_mod.path = lambda: target  # type: ignore[assignment]
    try:
        settings_mod.save(settings_mod.load())
    except AttributeError:
        pass
    if old_path is not None:
        settings_mod.path = old_path  # type: ignore[assignment]

    # ---- diagnostics export ------------------------------------------------
    from dubtool.app import diagnostics

    out = diagnostics.export_diagnostics(appdata / "diag.zip", jobs_dir=appdata / "jobs")
    assert out.exists()

    # ---- the quiet searches ------------------------------------------------
    def scan(folder: Path) -> list[str]:
        hits: list[str] = []
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            text = data.decode("utf-8", errors="ignore")
            blobs = [text]
            if path.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(path) as zf:
                        blobs += [
                            zf.read(n).decode("utf-8", errors="ignore")
                            for n in zf.namelist()
                        ]
                except zipfile.BadZipFile:
                    pass
            for value in values:
                if any(value in blob for blob in blobs):
                    hits.append(str(path.relative_to(folder)))
        return hits

    hits = scan(appdata)
    assert hits == [], "key values leaked into app data files"

    for value in values:
        for name, content in list(os.environ.items()):
            assert value not in str(content), f"key leaked into env var {name}"
        assert value not in subprocess.list2cmdline(sys.argv), (
            "key leaked into process command line"
        )

    assert no_processes == [], "the mocked run spawned processes"

    # A controlled negative control: the scrubber alone must redact values
    from dubtool.app import diagnostics as diag

    scrubber = diag.build_scrubber()
    for value in values:
        assert "[REDACTED]" in scrubber(f"noise {value} noise"), (
            "exact stored keys are not all scrubbed"
        )
