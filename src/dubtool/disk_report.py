"""AGU10 task 7 — the read-only disk report and the disk budget.

`scripts/disk_report.py` and `dubtool doctor --disk` both call this module.
It prints a report and writes it to ``<build root>/logs/``:

* top-level entries of the project and of the build root (size + files),
* the 30 largest files, the folder duplicates of 1 MB or more
  (size + SHA-256, skipping ``.env*`` and virtual environments),
* the locations OUTSIDE the project (%LOCALAPPDATA%/DubTool,
  %APPDATA%/DubTool, the Hugging Face cache with one line per model
  folder, the pip cache, %TEMP% dubtool/DubToolProbe entries),
* the git facts of task 6.4,
* a final "Disk budget" line against ``workspace.project_max_mb``.

It never reads the content of ``.env*`` files or settings files — file
sizes only. Every git helper runs through ``dubtool.proc`` and raises
nothing: git facts degrade to "unavailable" lines.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import time
from pathlib import Path

from dubtool import proc

try:
    from dubtool.workspace import build_root as _build_root

    HAVE_WORKSPACE = True
except Exception:  # pragma: no cover
    HAVE_WORKSPACE = False

DUP_MIN_BYTES = 1024 * 1024
LARGEST_LIMIT = 30

# Top-level project entries the docs describe. docs/WORKSPACE.md (AGU10
# 7.4) documents every folder and file; anything NOT in this list is
# flagged by the report as undocumented. Prefixes end with '*'.
DOCUMENTED_ENTRIES: tuple[str, ...] = (
    "src", "tests", "scripts", "packaging", "docs", "agent", "AGENTS.md", ".git",
    ".venv", ".venv-build", ".pytest_cache", ".freebuff", ".vscode",
    "sample", "models", "review", "jobs", "screenshots", "vendor",
    "build", "dist", "inventory*", "tmp", "hf-cache", "eval",
    ".gitignore", ".env", ".env.example", "config.yaml",
    "glossary.json", "glossary.example.json", "pyproject.toml",
    "README.md", "README_BUILD.md", "REVIEW_NOTES.md",
    "REPORT_UPDATE_*.md", "cleanup_dryrun.txt", "*.log", "build_log*",
    "exe_build*",
)

_GITIGNORED_TOPS = re.compile(
    r"^(tmp|inventory|hf-cache|build|dist|jobs|vendor|models|sample|"
    r"review|screenshots|\.freebuff|\.venv|\.pytest_cache|__pycache__)$"
)


# ------------------------------------------------------------- measuring
def _is_broken_entry(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        return not p.exists()
    except OSError:
        return True


def tree_stats(path: Path) -> tuple[int, int]:
    """(size_bytes, file_count) of a directory, without following links."""
    total = 0
    files = 0
    try:
        it = path.iterdir()
    except OSError:
        return 0, 0
    for child in it:
        if _is_broken_entry(child):
            continue
        try:
            if child.is_dir():
                d, f = tree_stats(child)
                total += d
                files += f
            elif child.is_file():
                total += child.stat().st_size
                files += 1
        except OSError:
            continue
    return total, files


def top_entries(path: Path) -> list[tuple[str, int, int]]:
    """(name, size_bytes, file_count) for every top-level child."""
    rows: list[tuple[str, int, int]] = []
    if not path.is_dir():
        return rows
    for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        if _is_broken_entry(child):
            continue
        try:
            if child.is_dir():
                size, files = tree_stats(child)
            elif child.is_file():
                size, files = child.stat().st_size, 1
            else:
                continue
        except OSError:
            continue
        rows.append((child.name, size, files))
    return rows


def walk_files(roots: list[Path]) -> list[tuple[Path, int]]:
    out: list[tuple[Path, int]] = []
    for root in roots:
        if root.is_file():
            out.append((root, root.stat().st_size))
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [d for d in dirnames if not _is_broken_entry(Path(dirpath) / d)]
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    if p.is_symlink():
                        continue
                    out.append((p, p.stat().st_size))
                except OSError:
                    continue
    return out


def largest_files(roots: list[Path], limit: int = LARGEST_LIMIT) -> list[tuple[Path, int]]:
    files = walk_files(roots)
    files.sort(key=lambda kv: -kv[1])
    return files[:limit]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                h.update(chunk)
    except OSError as exc:  # pragma: no cover
        return f"unreadable ({exc})"
    return h.hexdigest()


VENV_DIR_NAMES = re.compile(
    r"^(\.venv.*|venv|venv-build|__pycache__|\.pytest_cache)$", re.IGNORECASE
)


def find_duplicates(roots: list[Path], min_bytes: int = DUP_MIN_BYTES,
                    hash_: bool = True) -> list[tuple[int, str, list[str]]]:
    """Sizes of 1 MB+ present at two or more paths (same size; SHA-256 when
    `hash_`). Never touches ``.env*`` or any virtual environment."""
    candidates: dict[int, list[Path]] = {}
    for path, size in walk_files(roots):
        rel_parts = [p for p in path.parts]
        tops = {part for part in rel_parts}
        if any(VENV_DIR_NAMES.match(top) for top in tops):
            continue
        if path.name.startswith(".env"):
            continue
        if size >= min_bytes:
            candidates.setdefault(size, []).append(path)
    out: list[tuple[int, str, list[str]]] = []
    for size, paths in sorted(candidates.items(), key=lambda kv: -kv[0]):
        if len(paths) < 2:
            continue
        digest = ""
        if hash_:
            digest = _sha256(paths[0])
            if any(_sha256(p) != digest for p in paths[1:]):
                continue  # same size, different content — not a duplicate
        out.append((size, digest, [str(p) for p in paths]))
    return out


# ------------------------------------------------- outside-the-project rows
def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def hf_cache_candidates() -> list[Path]:
    cands: list[Path] = []
    env = _env_path("HF_HUB_CACHE")
    if env:
        cands.append(env)
    hf_home = _env_path("HF_HOME")
    if hf_home:
        cands.append(hf_home / "hub")
    local = _env_path("LOCALAPPDATA")
    home = Path.home()
    for base in (local, home):
        if base:
            cands.append(base / ".cache" / "huggingface" / "hub")
    seen: set[str] = set()
    out: list[Path] = []
    for c in cands:
        if str(c).lower() in seen:
            continue
        seen.add(str(c).lower())
        out.append(c)
    return out


def _safe_is_dir(p: Path | None) -> bool:
    if p is None:
        return False
    try:
        return p.is_dir()
    except (PermissionError, OSError):
        return False


def outside_rows() -> list[tuple[str, int, int]]:
    """(label, size_bytes, file_count) for the locations outside the
    project. The Hugging Face cache expands into one line per model
    folder (AGU10 7.1)."""
    rows: list[tuple[str, int, int]] = []
    local = _env_path("LOCALAPPDATA")
    appdata = _env_path("APPDATA")
    temp = _env_path("TEMP") or Path(os.environ.get("TMP", ""))
    for base, label in ((local, "LOCALAPPDATA"), (appdata, "APPDATA")):
        if not base:
            continue
        try:
            d = base / "DubTool"
            if _safe_is_dir(d):
                size, files = tree_stats(d)
                rows.append((f"%{label}%\\DubTool (app data)", size, files))
                hedgehog = d / "jobs"
                if _safe_is_dir(hedgehog):
                    jsize, jfiles = tree_stats(hedgehog)
                    rows.append((f"%{label}%\\DubTool\\jobs", jsize, jfiles))
        except (PermissionError, OSError):
            continue
    try:
        for cache in hf_cache_candidates():
            if _safe_is_dir(cache):
                size, files = tree_stats(cache)
                rows.append((f"HF cache: {cache}", size, files))
                for model in sorted(cache.glob("models--*"), key=lambda p: p.name):
                    if _safe_is_dir(model):
                        msize, mfiles = tree_stats(model)
                        if msize:
                            rows.append((f"HF model: {model.name}", msize, mfiles))
    except (PermissionError, OSError):
        pass
    try:
        pip = local / "pip" / "Cache" if local else None
        if _safe_is_dir(pip):
            size, files = tree_stats(pip)
            rows.append((f"%LOCALAPPDATA%\\pip\\Cache", size, files))
    except (PermissionError, OSError):
        pass
    try:
        if _safe_is_dir(temp):
            for child in sorted(temp.iterdir()):
                try:
                    if not _is_broken_entry(child) and ("dubtool" in child.name.lower()
                                                        or "DubToolProbe" in child.name):
                        size, files = tree_stats(child) if _safe_is_dir(child) else (child.stat().st_size, 1)
                        if size:
                            rows.append((f"%TEMP%\\{child.name}", size, files))
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return rows


# ---------------------------------------------------------------- git facts
def _git_out(project: Path, *args: str) -> str | None:
    done = proc.run(["git", *args], cwd=str(project))
    if done.returncode != 0:
        return None
    return done.stdout


def git_facts(project: Path) -> list[tuple[str, str]]:
    """The task 6.4 report facts: count-objects, unreachable blobs of 1 MB
    or more (sizes only, no contents), and whether anything large is
    currently staged."""
    facts: list[tuple[str, str]] = []
    out = _git_out(project, "count-objects", "-vH")
    if out:
        interesting = [ln for ln in out.splitlines()
                       if ln.startswith(("count:", "size:", "size-pack:", "prune-pack:"))]
        facts.append(("git count-objects", " | ".join(interesting)))
    else:
        facts.append(("git count-objects", "unavailable (not a git repo?)"))
    out = _git_out(project, "fsck", "--unreachable", "--no-reflogs")
    if out is None:
        facts.append(("git fsck unreachable", "unavailable"))
    else:
        big = []
        for line in out.splitlines():
            m = re.search(r"\b([0-9a-f]{40})\b", line)
            if not m:
                continue
            size = _git_out(project, "cat-file", "-s", m.group(1))
            if size and int(size.strip()) >= DUP_MIN_BYTES:
                big.append(size.strip())
        if big:
            facts.append(("git fsck unreachable >= 1 MB",
                          f"{len(big)} object(s): " + ", ".join(f"{int(s)/1024/1024:.1f} MB" for s in big)))
        else:
            facts.append(("git fsck unreachable >= 1 MB", "none"))
    out = _git_out(project, "diff", "--cached", "--stat")
    if out is None:
        facts.append(("staged", "unavailable"))
    else:
        stat_text = [ln for ln in out.splitlines() if "changed" in ln]
        anything = out.strip()
        facts.append(("staged (git diff --cached --stat)",
                      stat_text[0] if stat_text else ("empty" if not anything else "files staged")))
    return facts


# ----------------------------------------------------------- undocumented
def undocumented(entries: list[tuple[str, int, int]]) -> list[str]:
    names = [n for n, _, _ in entries]
    flags: list[str] = []
    for name in names:
        for doc in DOCUMENTED_ENTRIES:
            if "*" in doc and fnmatch.fnmatch(name.lower(), doc.lower()):
                break  # glob: *.log (suffix), build_log* (prefix), both
            if doc == name:
                break
        else:
            flags.append(name)
    return flags


# ----------------------------------------------------------------- report
def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def build_report(project: Path, budget_mb: int, build_root: Path | None = None,
                 now: float | None = None) -> str:
    """The full report text. Pure function of the tree; tests call this on
    fake trees."""
    now = time.time() if now is None else now
    lines: list[str] = []
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)) + "Z"
    lines.append("DubTool disk report (read-only) " + stamp)
    lines.append(f"project: {project}")

    proj_entries = top_entries(project)
    proj_size = sum(s for _, s, _ in proj_entries)
    lines.append("")
    lines.append(f"== project top level ({_mb(proj_size)}) ==")
    for name, size, files in sorted(proj_entries, key=lambda r: -r[1]):
        lines.append(f"  {name:28s} {_mb(size):>10s}  {files} file(s)")

    br_entries: list[tuple[str, int, int]] = []
    br = build_root
    if HAVE_WORKSPACE and build_root is None:
        try:
            br = _build_root()
        except Exception:
            br = None
    if br is not None:
        br_entries = top_entries(br)
        br_size = sum(s for _, s, _ in br_entries)
        lines.append("")
        lines.append(f"== build root top level ({_mb(br_size)}) ==")
        lines.append(f"  {br}")
        for name, size, files in sorted(br_entries, key=lambda r: -r[1]):
            lines.append(f"  {name:28s} {_mb(size):>10s}  {files} file(s)")

    roots = [project] + ([br] if br is not None else [])
    lines.append("")
    lines.append(f"== largest {LARGEST_LIMIT} files ==")
    for p, size in largest_files(roots):
        try:
            shown = str(p.relative_to(project))
        except ValueError:
            shown = str(p)
        lines.append(f"  {_mb(size):>10s}  {shown}")

    lines.append("")
    lines.append("== duplicates >= 1 MB (same size and SHA-256) ==")
    dups = find_duplicates(roots)
    if not dups:
        lines.append("  none")
    for size, digest, paths in dups:
        shown = "\n    ".join(paths)
        lines.append(f"  {_mb(size)}  {digest[:12]}…\n    {shown}")

    outside = outside_rows()
    lines.append("")
    lines.append("== outside the project ==")
    for label, size, files in outside:
        lines.append(f"  {label}  {_mb(size)}  {files} file(s)")

    # AGENT_UPDATE_11 items 1/8: the read-only legacy folders and what the
    # .freebuff folder actually is (sanitized-run homes of old verification
    # runs, not agent session data; no verification writes there anymore).
    lines.append("")
    lines.append("== legacy folders and .freebuff (AGENT_UPDATE_11 items 1/8) ==")
    try:
        from dubtool.app.paths import legacy_jobs_dir, legacy_models_dir

        legacy = legacy_jobs_dir()
        if legacy is not None:
            lines.append(f"  legacy in-project jobs/: {_mb(sum(f.stat().st_size for f in legacy.rglob('*') if f.is_file()))} — READ-ONLY, new jobs go to the build root")
        legacy_models = legacy_models_dir()
        if legacy_models is not None:
            lines.append(f"  legacy in-project models/: {_mb(sum(f.stat().st_size for f in legacy_models.rglob('*') if f.is_file()))} — READ-ONLY, the shared cache lives in the build root")
    except Exception:
        pass
    freebuff_dir = project / ".freebuff"
    if freebuff_dir.exists():
        try:
            size = sum(f.stat().st_size for f in freebuff_dir.rglob("*") if f.is_file())
        except Exception:
            size = 0
        lines.append(f"  .freebuff/: {_mb(size)} — sanitized-run HOME folders of past verification runs (duplicate model downloads likely). It is not agent session data; since AGENT_UPDATE_11 item 3 no verification run writes here — sanitized homes go to <build root>/tmp/ and are removed when the run ends.")

    lines.append("")
    lines.append("== git facts (task 6.4; cleanup commands are quoted in the report only) ==")
    for label, value in git_facts(project):
        lines.append(f"  {label}: {value}")

    flags = undocumented(proj_entries)
    if flags:
        lines.append("")
        lines.append("== undocumented top-level entries (docs/WORKSPACE.md) ==")
        for name in flags:
            lines.append(f"  {name}")

    # ---- disk budget --------------------------------------------------
    app_jobs = hf = 0
    for label, size, _ in outside:
        if label.endswith("%LOCALAPPDATA%\\DubTool\\jobs"):
            app_jobs = size
        if label.startswith("HF cache"):
            hf = max(hf, size)
    br_size = sum(s for _, s, _ in br_entries) if br_entries else 0
    over = proj_size > budget_mb * 1024 * 1024
    lines.append("")
    verdict = "OVER budget" if over else "within budget"
    biggest = sorted(proj_entries, key=lambda r: -r[1])[:10]
    lines.append(
        f"Disk budget: project {_mb(proj_size)}, build root {_mb(br_size)}, "
        f"appjobs {_mb(app_jobs)}, hf cache {_mb(hf)} — {verdict} "
        f"({budget_mb} MB cap; 10 largest (MB): "
        + ", ".join(f"{n} {s/(1024*1024):.1f}" for n, s, _ in biggest) + ")"
    )
    if over:
        lines.append("NOTE: over budget — run scripts/disk_report.py, then the "
                     "manual cleanup, then scripts/migrate_workspace.py --apply "
                     "(the user decides; nothing is deleted automatically).")
    return "\n".join(lines)


def write_report(project: Path, budget_mb: int) -> Path:
    """Print to stdout is the caller's job; this writes the file to
    <build root>/logs/ and returns its path."""
    try:
        logs = _build_root() / "logs" if HAVE_WORKSPACE else None
    except Exception:
        logs = None
    text = build_report(project, budget_mb)
    if logs is not None:
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / f"disk_report-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path
    return Path("-")


def main(project: Path | None = None, quiet: bool = False) -> int:
    if project is None:
        project = Path(__file__).resolve().parents[2]
    from dubtool.config import load  # noqa: PLC0415

    cfg = load(project)
    budget = int(cfg.workspace.get("project_max_mb", 1500))
    text = build_report(project, budget)
    print(text)
    path = write_report(project, budget)
    if not quiet and str(path) != "-":
        print(f"\n(report written to {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
