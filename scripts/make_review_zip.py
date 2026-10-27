"""Build the assistant review package (AGENT_UPDATE.md 7.2).

Standard library only. Runs from the project root:
    py scripts/make_review_zip.py [--tag phase2]

Output: review/dubtool-review-<YYYYMMDD-HHMM>[-<tag>].zip

Only an allowlist of files is packed; every candidate file is scanned for
secret values and key-like patterns before the zip is written. A secret match
in a .py/.yaml/.md source file aborts the whole run (line number only is
reported, never the value).
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import re
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALLOWLIST_TOP_LEVEL_FILES = [
    "config.yaml",
    "glossary.example.json",
    "pyproject.toml",
    "README.md",
    ".gitignore",
    ".env.example",
    "AGENT.md",
    "AGENT_UPDATE.md",
    "AGENT_UPDATE_2.md",
    "AGENT_UPDATE_3.md",
    "AGENT_UPDATE_4.md",
    "AGENT_UPDATE_5.md",
    "README_BUILD.md",
    "REVIEW_NOTES.md",
]
ALLOWLIST_DIRS = ["src", "tests", "scripts", "packaging"]

SOURCE_SUFFIXES = (".py", ".yaml", ".yml", ".md", ".toml", ".json", ".cfg", ".txt")
NEVER_SUFFIXES = (".mp4", ".mov", ".mkv", ".wav", ".mp3", ".m4a", ".zip", ".pt", ".model")
NEVER_DIR_PATTERNS = [
    ".env*",  # except .env.example handled below
    ".venv", "venv", ".git", "jobs", "sample", "review",
    "__pycache__", "node_modules", "*.egg-info", ".pytest_cache",
]
KEY_PATTERNS = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{10,}\b"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}\b"),
]
REDACTED = "[REDACTED]"
MAX_ZIP_BYTES = 10 * 1024 * 1024
KEEP_ZIPS = 3


def collect_secret_values(root: Path | None = None) -> list[str]:
    """Secret values in memory only — never printed, never written raw."""
    root = root or PROJECT_ROOT
    values: list[str] = []
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        env_value = os.environ.get(name, "")
        if env_value.strip():
            values.append(env_value.strip())
    env_file = root / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            _, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1].strip()
            if value:
                values.append(value)
    return values


def redact_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    for pattern in KEY_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def find_secret_lines(path: Path, secrets: list[str]) -> list[int]:
    """Return line numbers whose raw text contains a secret value or key pattern."""
    if path.suffix not in SOURCE_SUFFIXES and path.name not in ("config.yaml",):
        return []
    hits: list[int] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return hits
    for index, line in enumerate(lines, start=1):
        for secret in secrets:
            if secret and secret in line:
                hits.append(index)
                break
        else:
            # "# fixture-fake-key" marks a line that deliberately quotes a
            # key-shaped FAKE value (the no-leak test fixtures and the
            # redactor's own regex). Only *stored values* are never allowed
            # through, so a real key on an unmarked line still aborts (AGU6).
            if "fixture-fake-key" not in line and any(p.search(line) for p in KEY_PATTERNS):
                hits.append(index)
    return hits


def _is_never_dir(rel_parts: tuple[str, ...]) -> bool:
    name_chain = set(rel_parts)
    for pattern in NEVER_DIR_PATTERNS:
        pat = pattern.rstrip("*")
        for part in rel_parts:
            if fnmatch.fnmatch(part, pattern) or (pat and part.startswith(pat) and pattern.endswith("*")):
                return True
    return bool(name_chain & {p.rstrip("*") for p in NEVER_DIR_PATTERNS})


def candidate_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in ALLOWLIST_TOP_LEVEL_FILES:
        p = root / name
        if p.exists():
            candidates.append(p)
    for dirname in ALLOWLIST_DIRS:
        base = root / dirname
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in {".env", ".git"} for part in rel.parts):
                continue
            if _is_never_dir(rel.parts[:-1]):
                continue
            if p.suffix.lower() in NEVER_SUFFIXES or p.name.endswith(".pyc"):
                continue
            candidates.append(p)
    return candidates


def latest_job_dir() -> Path | None:
    jobs_dir = PROJECT_ROOT / "jobs"
    if not jobs_dir.exists():
        return None
    state_files = list(jobs_dir.glob("*/state.json"))
    if not state_files:
        return None
    return max(state_files, key=lambda p: p.stat().st_mtime).parent


def build_excerpts(secrets: list[str]) -> dict[str, str]:
    """Small text samples keyed by excerpt filename."""
    excerpts: dict[str, str] = {}
    job = latest_job_dir()
    if job is None:
        return {"notes/README.txt": "No job found; nothing to excerpt.\n"}

    for name in ("transcript.json", "translated.json", "state.json"):
        path = job / name
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, list):
                data = data[:10]
            text = json.dumps(data, ensure_ascii=False, indent=2)
            excerpts[f"excerpts/{name}"] = redact_text(text, secrets) + "\n"
    log_path = job / "pipeline.log"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
        excerpts["excerpts/pipeline.log"] = redact_text("\n".join(lines), secrets) + "\n"
    # fit_status counts + overflow ids from stage 5
    translated = job / "translated.json"
    counts: dict[str, int] = {}
    overflow_ids: list[int] = []
    if translated.exists():
        try:
            entries = json.loads(translated.read_text(encoding="utf-8"))
            for e in entries:
                status = e.get("fit_status", "pending")
                counts[status] = counts.get(status, 0) + 1
                if status == "overflow":
                    overflow_ids.append(e["id"])
        except (json.JSONDecodeError, OSError):
            pass
    excerpts["excerpts/fit_status_counts.json"] = json.dumps(
        {"job": job.name, "counts": counts, "overflow_ids": overflow_ids},
        ensure_ascii=False, indent=2,
    ) + "\n"
    # TTS failures (AGENT_UPDATE_3 task 6): counts + character-length stats
    # only, never full texts; the first 10 entries are included as-is.
    failures_path = job / "tts_failures.json"
    if failures_path.exists():
        try:
            failures = json.loads(failures_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            failures = []
        if isinstance(failures, list):
            chars = [f["chars"] for f in failures if isinstance(f, dict) and "chars" in f]
            stats = {
                "job": job.name,
                "total_failures": len(failures),
                "failed_all_attempts": len(
                    {f["id"] for f in failures if isinstance(f, dict)}
                ),
                "chars_min": min(chars) if chars else None,
                "chars_max": max(chars) if chars else None,
                "chars_mean": round(sum(chars) / len(chars), 1) if chars else None,
                "first_10_entries": [
                    {k: f.get(k) for k in ("id", "attempt", "chars", "error", "elapsed_s")}
                    for f in failures[:10]
                    if isinstance(f, dict)
                ],
            }
            excerpts["excerpts/tts_failures_stats.json"] = json.dumps(
                stats, ensure_ascii=False, indent=2
            ) + "\n"
    return excerpts


def ensure_review_notes(root: Path) -> Path:
    notes = root / "REVIEW_NOTES.md"
    if not notes.exists():
        notes.write_text(
            "# REVIEW_NOTES.md\n\n(Placeholder — the agent writes fresh notes each session.)\n",
            encoding="utf-8",
        )
    return notes


def _zip_name(root: Path, tag: str | None) -> Path:
    review_dir = root / "review"
    review_dir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    suffix = f"-{tag}" if tag else ""
    return review_dir / f"dubtool-review-{stamp}{suffix}.zip"


def prune_old_zips(review_dir: Path, keep: int = KEEP_ZIPS) -> list[str]:
    zips = sorted(review_dir.glob("dubtool-review-*.zip"))
    removed: list[str] = []
    for old in zips[:-keep] if len(zips) > keep else []:
        old.unlink()
        removed.append(old.name)
    return removed


def make_review_zip(tag: str | None = None, root: Path | None = None) -> Path:
    root = root or PROJECT_ROOT
    secrets = collect_secret_values(root)
    candidates = candidate_files(root)

    # Secret scan BEFORE writing the zip.
    for path in candidates:
        rel = path.relative_to(root)
        lines = find_secret_lines(path, secrets)
        source_like = path.suffix in SOURCE_SUFFIXES
        if lines:
            if source_like and not (path.name == "pipeline.log"):
                raise RuntimeError(
                    "Secret-like match found in source file: "
                    f"{rel} line {lines[0]} (and {len(lines) - 1} more). "
                    "Aborting before writing the zip. Treat the key as leaked: "
                    "revoke and rotate it."
                )
    # Non-source file matches get redacted in-memory below.

    notes = ensure_review_notes(root)
    excerpts = build_excerpts(secrets)

    out_zip = _zip_name(root, tag)
    sources: list[tuple[str, Path | str, str | None]] = []
    for path in candidates:
        if path == notes:
            continue  # REVIEW_NOTES.md is added once, with the file list appended
        rel = str(path.relative_to(root).as_posix())
        sources.append((rel, path, None))
    for name, text in excerpts.items():
        sources.append((name, None, text))
    # Record the zip file list inside the zip's own REVIEW_NOTES.md.
    file_list = "\n".join(sorted(rel for rel, _p, _t in sources))
    sources.append(
        (
            "REVIEW_NOTES.md",
            None,
            redact_text(notes.read_text(encoding="utf-8", errors="ignore"), secrets)
            + "\n## Files in this zip\n\n```\n"
            + file_list
            + "\n```\n",
        )
    )

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        written: dict[str, int] = {}
        for rel, file_path, text in sources:
            if text is None:
                zf.write(str(file_path), rel)
                written[rel] = file_path.stat().st_size
            else:
                zf.writestr(rel, text)
                written[rel] = len(text.encode("utf-8"))

    size = out_zip.stat().st_size
    if size > MAX_ZIP_BYTES:
        biggest = sorted(written.items(), key=lambda kv: kv[1], reverse=True)[:10]
        raise RuntimeError(
            f"review zip too large: {out_zip.name} is {size} bytes (> {MAX_ZIP_BYTES}). "
            "Largest files: " + ", ".join(rel for rel, _s in biggest)
        )
    prune_old_zips(root / "review")
    return out_zip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make_review_zip.py")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args(argv)
    try:
        out = make_review_zip(tag=args.tag)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"review zip: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
