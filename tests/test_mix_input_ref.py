"""AGU10 5.1 regression: the s6 mix must open the input video even when it
was never copied into the job folder (jobs.copy_input=false — this failed
in the packaged verification run: ffmpeg got the bare 'test2.mp4' with the
job dir as cwd)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dubtool import proc


def _capture(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr("dubtool.audio_utils.ffmpeg_bin", lambda: "ffmpeg")
    return seen


def test_mix_input_outside_working_dir_uses_absolute(monkeypatch, tmp_path):
    from dubtool.audio_utils import mix_original_with_dub

    working = tmp_path / "job"
    working.mkdir()
    bg = tmp_path / "sample input.mp4"  # outside the job dir, with a space
    bg.write_bytes(b"v")
    dub = working / "vi.wav"
    dub.write_bytes(b"a")
    out = working / "output.nosubs.mp4"
    seen = _capture(monkeypatch)
    mix_original_with_dub(
        working_dir=working, background=bg, dub=dub, out=out, graph="dummy-graph"
    )
    assert seen["cmd"][seen["cmd"].index("-i") + 1] == str(bg.resolve())
    # relative-friendly arguments stay relative
    assert seen["cmd"][seen["cmd"].index("-i") + 3] == "vi.wav"
    assert seen["cmd"][-1] == "output.nosubs.mp4"
    assert not (working / "mix_script.txt").exists()


def test_mix_input_inside_working_dir_stays_relative(monkeypatch, tmp_path):
    from dubtool.audio_utils import mix_original_with_dub

    working = tmp_path / "job"
    working.mkdir()
    bg = working / "test2.mp4"  # legacy copied-input layout
    bg.write_bytes(b"v")
    dub = working / "vi.wav"
    dub.write_bytes(b"a")
    out = working / "output.nosubs.mp4"
    seen = _capture(monkeypatch)
    mix_original_with_dub(
        working_dir=working, background=bg, dub=dub, out=out, graph="g"
    )
    assert seen["cmd"][seen["cmd"].index("-i") + 1] == "test2.mp4"
