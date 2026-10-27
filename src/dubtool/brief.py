"""Video brief resolution (AGENT_UPDATE_3.md 7.1).

The brief is user-supplied only: CLI `--brief "<text>"`, `--brief-file
<path>`, or a `<video name>.brief.txt` next to the input video, remembered
in the job directory as brief.txt. The agent never writes a brief. Capped
at 1,000 characters.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

MAX_BRIEF_CHARS = 1000


def normalize_brief(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) > MAX_BRIEF_CHARS:
        text = text[:MAX_BRIEF_CHARS]
    return text


def read_brief_file(path: Path) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"could not read brief file {path}: {exc}") from exc
    return normalize_brief(text)


def default_brief_path(input_video: Path | None) -> Path | None:
    """<video name>.brief.txt next to the input video (e.g. sample/input/test1.brief.txt)."""
    if input_video is None:
        return None
    return input_video.parent / f"{input_video.stem}.brief.txt"


def job_brief_path(job_dir: Path) -> Path:
    return Path(job_dir) / "brief.txt"


def save_brief(job_dir: Path, brief: str) -> None:
    brief = normalize_brief(brief)
    if brief:
        job_brief_path(job_dir).write_text(brief, encoding="utf-8")


def _briefs_path() -> Path:
    from dubtool.app.paths import settings_dir

    return settings_dir() / "briefs.json"


def brief_key(video: Path) -> str:
    """Stable key: a hash of the resolved video path (16 hex chars)."""
    import hashlib

    try:
        resolved = str(Path(video).resolve())
    except OSError:
        resolved = str(video)
    if hasattr(hashlib, "sha256"):  # defensive: hashlib always has it
        return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def _read_briefs() -> dict:
    try:
        import json

        data = json.loads(_briefs_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_briefs(data: dict) -> bool:
    try:
        import json

        path = _briefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError):
        return False


def load_video_brief(video: Path) -> str:
    """The stored description for this video ('' when none)."""
    text = _read_briefs().get(brief_key(video))
    if isinstance(text, str):
        return normalize_brief(text)
    return ""


def save_video_brief(video: Path, text: str) -> None:
    """Store (or remove, when empty) this video's description. Write errors
    are handled silently (AGENT_UPDATE_5.md task 5.2)."""
    brief = normalize_brief(text)
    data = _read_briefs()
    key = brief_key(video)
    changed = False
    if brief:
        if data.get(key) != brief:
            data[key] = brief
            changed = True
    elif key in data:
        data.pop(key, None)
        changed = True
    if not changed:
        return
    if data:
        _write_briefs(data)
    else:
        try:
            _briefs_path().unlink(missing_ok=True)
        except OSError:
            pass


def resolve_brief(
    cli_brief: str | None = None,
    cli_brief_file: str | Path | None = None,
    input_video: Path | None = None,
    job_dir: Path | None = None,
) -> str:
    """CLI text wins, then the CLI file, then brief.txt in the job dir, then
    <video>.brief.txt next to the input video. Empty string when none."""
    if cli_brief and cli_brief.strip():
        return normalize_brief(cli_brief)
    if cli_brief_file:
        return read_brief_file(cli_brief_file)
    job = job_brief_path(job_dir) if job_dir is not None else None
    if job is not None and job.exists():
        return read_brief_file(job)
    fallback = default_brief_path(input_video)
    if fallback is not None and fallback.exists():
        return read_brief_file(fallback)
    return ""
