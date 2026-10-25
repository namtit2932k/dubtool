"""Video brief resolution (AGENT_UPDATE_3.md 7.1).

The brief is user-supplied only: CLI `--brief "<text>"`, `--brief-file
<path>`, or a `<video name>.brief.txt` next to the input video, remembered
in the job directory as brief.txt. The agent never writes a brief. Capped
at 1,000 characters.
"""

from __future__ import annotations

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
